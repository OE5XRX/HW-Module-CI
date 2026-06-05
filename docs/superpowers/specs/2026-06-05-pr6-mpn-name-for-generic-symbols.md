# PR-6 — MPN-basierter Part-Name für generische KiCad-Symbole

**Status:** Spec ready.
**Scope:** Hot-Fix vor dem Marathon-Sync — verhindert dass physisch
verschiedene Bauteile mit demselben generischen KiCad-Symbol
(`Conn_02x10_Row_Letter_First`, `Conn_Coaxial`, …) zu **einem einzigen**
InvenTree-Part collapse'n.
**Predecessor:** PR-5 Sync-Ready Foundation (main @ 11ce28c).
**Erstellt:** 2026-06-05 nach Dry-Run-Analyse von 5 Modulen.

---

## Motivation

Beim Dry-Run von DeviceTester v1.0 entdeckt: J203 (Stiftleiste Mouser
`798-PCN1020P254DS72`) und J302 (Buchsenleiste Mouser `798-PCN10C20S254DS72`)
sind zwei physisch verschiedene Bauteile mit demselben KiCad-Symbol
`Conn_02x10_Row_Letter_First`. Der heutige `generate_part_name`-Generator
nimmt für Nicht-RCL-Symbole `kicad_value` direkt durch — beide bekommen
denselben generierten Namen.

**Was im Real-Sync passiert:**
1. J203 → `find_existing_part(SKU)` ❌ → `_fetch_and_merge` liefert MPN
   `PCN10-20P-2.54DS` → `find_part_by_mpn_and_manufacturer` ❌ →
   `find_part_by_name("Conn_02x10_Row_Letter_First")` ❌ → **CREATE**
   Part `Conn_02x10_Row_Letter_First` mit J203's SKU.
2. J302 → `find_existing_part(SKU)` ❌ → `_fetch_and_merge` liefert MPN
   `PCN10C-20S-2.54DS` (anders!) → `find_part_by_mpn_and_manufacturer`
   ❌ → `find_part_by_name("Conn_02x10_Row_Letter_First")` ✅ **trifft
   J203's Part** → `ensure_supplier_parts` attached J302's Mouser-SKU
   an J203's Part.

**Resultat:** Ein InvenTree-Part hat zwei Supplier-SKUs für zwei
physisch verschiedene Bauteile. Beim BOM-Refresh überschreibt der zweite
SKU die Parameter/Beschreibung des ersten — die Hardware-Wahrheit geht
verloren.

Der KiCad-Symbol-Name ist **bewusst generisch** (Schaltplan-Konvention).
Die physische Identität kommt aus dem MPN, den uns LCSC und Mouser auf
SKU-Lookup liefern.

---

## Goals

- Generische KiCad-Symbol-Klassen erzeugen unterscheidbare InvenTree-
  Part-Namen, sobald der MPN bekannt ist.
- Strukturierte Bauteil-Klassen (R/C/L/CP/Crystal) bleiben unverändert
  — `R 10k 0805` ist lesbarer als `0805W8F1002T5E`.
- Echte Bauteilnamen (STM32U575CITx, INA226, USBLC6-2SC6) bleiben
  unverändert — `kicad_value` ist da schon der MPN.

## Non-Goals

- Keine retrospektive Umbenennung bereits existierender InvenTree-Parts.
- Kein neuer Lookup-Schritt in `ensure_parts_exist` — die existierende
  5-stufige Kette (SKU → MPN+Mfr → Name → CREATE) bleibt unverändert.
- Keine Eingriffe in `find_part_by_name` selbst.

---

## Architektur

### Erkennungs-Heuristik: was ist ein "generisches Symbol"?

```python
_GENERIC_SYMBOL_PREFIXES = (
    "Conn_",            # alle KiCad-Connector-Symbole
    "Screw_Terminal_",  # alle Klemmen-Symbole
)
```

Begründung — warum Präfix-Liste statt vollständigem Pattern-Match:

- KiCad-Symbol-Bibliotheken nutzen konsequent diese Präfixe für
  generische Connector-Familien (Conn_01x02_Pin, Conn_02x10_..., 
  Conn_Coaxial, Conn_ARM_JTAG_SWD_10, …).
- Hierarchie ist **Symbol-Library-Konvention**, nicht Bauteil-Eigenschaft
  — daher nicht in einer YAML, sondern als Code-Konstante.
- Aufnahme weiterer Präfixe in Zukunft ist eine 1-Zeilen-Änderung.

Bewusst **nicht** in der Generic-Liste:
- `LED`, `Fuse`, `Crystal` — diese Symbole sind im KiCad-Standard zwar
  auch generisch, der `kicad_value` ist aber traditionell sehr aussagekräftig
  (`8MHz/20pF`, evtl `Red 0805`). Hier kann der MPN auch ein eindeutiger
  Identifier sein, aber das Risiko von "schlechteren" Namen (Yageo-MPN
  statt "C 100nF 0805") wäre höher.

### Neue Signatur von `generate_part_name`

```python
def generate_part_name(
    kicad_part: str,
    kicad_value: str,
    footprint: str,
    part_data: Optional["PartData"] = None,
) -> str:
```

- `part_data=None` (heutiger Default) verhält sich wie heute — wichtig
  für Dry-Run, wo Fetcher nicht läuft.
- `part_data` non-None: Generic-Symbol-Path nutzt `part_data.mpn` wenn
  vorhanden, sonst Fallback auf heutigen `val`.

### Konkret in `generate_part_name`

```python
# ... existing logic for R, C, CP, L, Crystal unchanged ...
else:
    # Generic-Symbol-Klassen: MPN aus part_data nutzen wenn verfügbar.
    # Schützt vor Name-Kollisionen bei physisch verschiedenen Connectoren
    # die alle das KiCad-Symbol Conn_<...> teilen.
    if (part_data is not None and part_data.mpn
            and kicad_part.startswith(_GENERIC_SYMBOL_PREFIXES)):
        return part_data.mpn.strip()
    return val
```

### Integration in `part_manager.ensure_parts_exist`

Im CREATE-Pfad (nach erfolgreichem `_fetch_and_merge`):

```python
# Zeile ~219 (vor find_part_by_mpn_and_manufacturer Block):
name = generate_part_name(
    kicad_part, kicad_value, kicad_footprint,
    part_data=part_data,
)
```

(Heute ist die `name`-Generation in einem der späteren Branches; das
muss nach dem MPN+Mfr-Lookup-Block bleiben, weil wir `part_data` brauchen.
Tatsächlich: `name` wird heute schon nach `_fetch_and_merge` generiert —
die Änderung ist nur das zusätzliche `part_data=part_data`-Argument.)

**Dry-Run-Path** (`ensure_parts_exist` mit `reporter is not None`):
`generate_part_name(kicad_part, kicad_value, kicad_footprint)` — ohne
`part_data`, also heutiges Verhalten. **Das ist Absicht** — Dry-Run zeigt
die generischen Namen weiter als CREATE-Vorschlag. Beim Real-Sync werden
die dann durch echte MPNs ersetzt.

---

## Tests

### Pytest — `scripts/tests/test_generate_part_name.py` (neu)

~8 Cases. Existierender `test_normalization.py` testet nur `_normalize_value`,
nicht `generate_part_name` als Ganzes — neuer Test-File hält die Trennung
sauber.

1. `test_R_unchanged` — `generate_part_name("R", "10k", "R_0805_2012Metric")`
   → `"R 10k 0805"`. Auch mit `part_data` mit MPN: dieselbe Antwort
   (R bleibt strukturiert, MPN ignoriert).
2. `test_C_unchanged_with_part_data` — analog für C.
3. `test_generic_connector_falls_back_when_no_part_data` —
   `("Conn_02x10_Row_Letter_First", "Conn_02x10_Row_Letter_First", "PCN10-...", None)`
   → `"Conn_02x10_Row_Letter_First"`.
4. `test_generic_connector_uses_mpn_when_part_data_provided` —
   `kicad_part="Conn_02x10_Row_Letter_First"`, `part_data.mpn="PCN10-20P-2.54DS"`
   → `"PCN10-20P-2.54DS"`.
5. `test_generic_connector_falls_back_when_part_data_has_empty_mpn` —
   `part_data.mpn=""` → heutiger Fallback.
6. `test_screw_terminal_uses_mpn` —
   `kicad_part="Screw_Terminal_01x02"`, `part_data.mpn="MKDS-1,5/2-5.08"` →
   `"MKDS-1,5/2-5.08"`.
7. `test_mpn_is_stripped` — `part_data.mpn="  PCN10-20P  "` → `"PCN10-20P"`.
8. `test_non_generic_ic_still_uses_kicad_value` —
   `kicad_part="STM32U575CITx"`, `kicad_value="STM32U575CITx"`,
   `part_data.mpn="STM32U575CIT6"` → `"STM32U575CITx"`. **Wichtig**: kein
   Override für ICs, nur für Generic-Connectoren.

### E2E — `scripts/e2e_revision_handling.py` neuer Test

`test_generic_connector_mpn_disambiguation`:
- Erstellt zwei dummy `Conn_02x10_Row_Letter_First`-BomEntries mit
  unterschiedlichen Mouser-SKUs (gemockt: zwei verschiedene `PartData`
  Objekte mit unterschiedlichen MPNs).
- Ruft `generate_part_name` mit jeweils dem entsprechenden `part_data`.
- Assertet: zwei verschiedene Namen.

Kein Real-Server-Round-Trip nötig — es ist eine pure-function-Property.
Kann auch in den Pytest-File wandern, aber für Konsistenz mit E2E-Schema
(„dieser Bug wäre uns aufgefallen wenn …") sinnvoll dort.

### Re-Verify Dry-Runs

Nach dem Code-Change die 5 Dry-Runs erneut laufen lassen. Dry-Run wird
**keine** Verhaltensänderung zeigen (siehe Architektur-Sektion), aber
das ist die Bestätigung dass Bestandsverhalten nicht regressed.

---

## Backwards compatibility

| Change | Compat-Risk | Mitigation |
|---|---|---|
| `generate_part_name` bekommt `part_data` als optionalen 4. Parameter | Keiner — heutige Aufrufer ohne Parameter verhalten sich identisch | n/a |
| Generic-Connector-Namen wechseln auf MPN beim ersten Real-Sync | **Mittel:** existierende `Conn_*`-Parts in InvenTree behalten den alten Namen, neue bekommen MPN | Existierende Parts werden bei zweitem Sync via MPN+Mfr-Dedup gefunden (PR-5 #13) — kein Doppelpart |
| Keine Änderung an Lookup-Kette | n/a | n/a |

---

## Files touched

```
scripts/inventree_sync/categories.py    +20 LOC  (Generic-Prefix-Liste, MPN-Branch)
scripts/inventree_sync/part_manager.py  +1  LOC  (part_data= an Aufruf weiterreichen)
scripts/tests/test_generate_part_name.py +60 LOC (NEW)
scripts/e2e_revision_handling.py        +30 LOC  (1 neuer Test + Registrierung)
```

**Total: ~110 LOC.**

---

## Implementation order

1. **Spec + Plan** auf Feature-Branch committen.
2. Pytest-File zuerst schreiben (alle 8 Cases, failing).
3. `categories.generate_part_name` erweitern bis alle Cases passen.
4. `part_manager.ensure_parts_exist` Aufruf updaten.
5. Bestehender Pytest-Suite gegen-laufen (105+ cases müssen grün bleiben).
6. E2E-Test hinzufügen + lokal gegen Server laufen lassen (16 → 17 tests).
7. Dry-Run-Re-Verify aller 5 Module — sollte identisch zu vorher sein.
8. Commit, push, PR, Copilot review loop, squash-merge.
