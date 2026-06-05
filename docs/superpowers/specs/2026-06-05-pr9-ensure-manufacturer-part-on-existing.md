# PR-9 — ManufacturerPart-Linkage auf existing Parts nachziehen

**Status:** Spec ready.
**Scope:** Hot-Fix für die Idempotenz-Lücke aus PR-5: `ensure_supplier_parts`
legt nur fehlende SupplierParts an, **nicht** fehlende ManufacturerParts.
Beim PowerBoard-v1.1-Sync sind 15 Altlast-Parts ohne MfrPart geblieben.
**Predecessor:** PR-8 HTML-Strip Supplier Descriptions (main @ e60bbaf).
**Erstellt:** 2026-06-05 direkt nach erstem erfolgreichem CI-Sync.

---

## Motivation

Beim Real-Sync der PowerBoard v1.1 erschien nach Run 2 folgende Lücke:

```
=== INA226 Stichprobe ===
  Part pk=1001  name='INA226'  description='INA226'
    1 SupplierPart(s):  pk=230 SKU='C49851'
    0 ManufacturerPart(s):       ← FEHLT
```

INA226 wurde im **ersten** Run angelegt (Company-403 hat keine SupplierParts/
MfrParts zugelassen), beim **zweiten** Run via `find_part_by_name`
wiedererkannt und durch `ensure_supplier_parts` mit SupplierPart-Linkage
versorgt. Aber:

`ensure_supplier_parts` (in `client.py`) macht nur SupplierPart + Preise +
Parameters — **kein ManufacturerPart**. Der MfrPart-Pfad existiert nur in
`create_part_in_inventree`, der ausschließlich für neue Parts läuft.

Konsequenz: alle 15 Altlast-Parts (alle außer LMR51430, der in Run 2 frisch
angelegt wurde) sind ohne `ManufacturerPart`-Linkage. Das blockiert:
- PR-5 #13 `find_part_by_mpn_and_manufacturer`-Dedup beim nächsten Modul-
  Sync (z.B. BusBoard mit shared LCSC-Komponenten)
- InvenTree's `/api/company/part/manufacturer/?MPN=...` Suche im UI

Plus: dasselbe Problem würde bei jedem **Nightly-Refresh** sichtbar sein,
wenn ein Part ohne MfrPart einen MfrPart erst nach einem Modul-Sync
bekommen müsste.

---

## Goals

- Beim "Part schon vorhanden, ManufacturerPart fehlt aber"-Pfad wird der
  MfrPart **idempotent nachgezogen**.
- Beim "Part schon vorhanden, MfrPart auch da"-Pfad kein neuer Create,
  kein doppelter MfrPart.
- Bestehende Logik in `create_part_in_inventree` bleibt funktional
  identisch (DRY refactor — nutzt denselben Helper).

## Non-Goals

- Keine Rückwirkungen auf alternate-MPN-Handling (Multi-MPN-Alternates
  bleibt Backlog-Follow-up).
- Kein neuer API-Endpoint, keine Schema-Änderung.

---

## Architektur

### Neuer Helper in `client.py`

```python
def ensure_manufacturer_part(
    api: InvenTreeAPI,
    part: Part,
    mpn: str,
    manufacturer_name: str,
) -> None:
    """Idempotent ManufacturerPart linkage between Part and Manufacturer.

    Skips silently when:
      - mpn or manufacturer_name is empty / whitespace-only
      - a ManufacturerPart with the SAME (MPN, manufacturer-name) pair is
        already attached to *part* (case-insensitive). Different-manufacturer
        alternates with the same MPN are NOT treated as already-linked —
        they get a separate MfrPart, preserving second-source semantics.
      - get_or_create_manufacturer fails (returns None)

    Post-filters on (mp.part == part.pk) AND mp.MPN == mpn AND
    Company(mp.manufacturer).name matches case-insensitively, because some
    InvenTree server versions silently ignore filter kwargs (same defensive
    pattern as find_part_by_name / find_part_by_mpn_and_manufacturer).

    Errors during Create are logged but never raised — sync-loop callers
    must not bail on a single MfrPart-create failure.
    """
```

### Integration

**1) `create_part_in_inventree`:** der existierende inline-Block

```python
if part_data.mpn and part_data.manufacturer:
    manufacturer = get_or_create_manufacturer(api, part_data.manufacturer)
    if manufacturer:
        try:
            ManufacturerPart.create(...)
        ...
```

wird ersetzt durch:

```python
ensure_manufacturer_part(api, part, part_data.mpn, part_data.manufacturer)
```

Verhalten 100% identisch für den new-Part-Pfad: leere existing-Liste →
Create. Das Idempotenz-Verhalten ist zusätzlicher Schutz, kein Verlust.

**2) `ensure_supplier_parts`:** neuer Aufruf am Anfang nach der SKU-
Normalisierung:

```python
def ensure_supplier_parts(api, part, part_data, ...):
    # ... existing normalize-skus code ...

    # PR-9: Wenn der Part im ersten Sync wegen Company-403 ohne MfrPart
    # angelegt wurde, bei diesem Re-Sync nachziehen. Idempotent auf das
    # (MPN, manufacturer-name)-Paar: derselbe MPN von einem anderen
    # Manufacturer (Second-Source-Alternate) wird absichtlich als
    # zusätzlicher MfrPart angelegt. ManufacturerPart muss VOR den
    # SupplierParts da sein damit sie sich auf den richtigen MfrPart-pk
    # linken könnten (manufacturer_part field auf SupplierPart). Heute
    # lassen wir das Feld bewusst None, PR-9 bleibt davon entkoppelt —
    # Backlog-Follow-up wenn nötig.
    ensure_manufacturer_part(api, part, part_data.mpn, part_data.manufacturer)

    # ... existing supplier-part creation logic ...
```

### Effekt auf bestehende Parts

Beim nächsten Sync werden alle 15 PowerBoard-Component-Parts ohne MfrPart
über `find_part_by_name` → `ensure_supplier_parts` → `ensure_manufacturer_part`
nachgezogen. Beim Nightly-Refresh ebenso.

---

## Tests

### Pytest

Schwierig wegen InvenTreeAPI-Mocking. Helper ist klein genug, dass die
E2E-Coverage ausreicht.

### E2E — `scripts/e2e_revision_handling.py`

**Neuer Test `test_ensure_manufacturer_part_backfills_missing`** vor dem
Entry-Point:

1. Lege manuell einen Part an mit `name=f"{PREFIX} MfrBackfill"`, ohne MfrPart.
2. Konstruiere ein `PartData(mpn="MPN-BACKFILL-X", manufacturer="<name>")` —
   der Manufacturer-Company wird **implizit** im ersten ensure-Call via
   `get_or_create_manufacturer` angelegt (keine manuelle Vor-Erzeugung).
3. Rufe `ensure_supplier_parts(api, part, part_data, lcsc_supplier=None, mouser_supplier=None)`.
4. Assert: ManufacturerPart-Liste auf dem Part hat genau 1 Eintrag mit dem
   erwarteten MPN UND der verknüpfte Company.name matched PartData.manufacturer
   (case-insensitive — die Kontrakte von get_or_create_manufacturer und
   ensure_manufacturer_part vergleichen so).
5. Idempotenz: zweiter Aufruf → immer noch 1 MfrPart, kein duplicate.

Vorhandener `test_multi_sku_supplier_parts` wird **nicht** geändert —
deckt schon den new-Part-Pfad mit MfrPart-Creation ab (über
create_part_in_inventree).

---

## Backwards compatibility

| Change | Risk | Mitigation |
|---|---|---|
| Neuer Helper-Call in `ensure_supplier_parts` | Keiner — idempotent | Verhalten für Parts die schon MfrPart haben = Noop |
| Refactor `create_part_in_inventree` | Minimal — selber Code-Pfad, nur extrahiert | Identisches Verhalten für neue Parts |
| Re-Sync sammelt zusätzliche API-Calls (ManufacturerPart.list pro Entry) | Niedrig — eine extra GET pro Component | Im InvenTree-Refresh-Job akzeptabel |

---

## Files touched

```
scripts/inventree_sync/client.py        +35 LOC  (Helper + 2 Aufrufe)
scripts/e2e_revision_handling.py        +60 LOC  (1 neuer Test + Registrierung)
```

**Total: ~95 LOC.**

---

## Implementation order

1. Spec + Plan committen.
2. `ensure_manufacturer_part` in `client.py` (mit Doku).
3. `create_part_in_inventree` auf den Helper umstellen.
4. `ensure_supplier_parts` ruft den Helper.
5. Bestehender Pytest grün (129 erwartet, kein Test-Code-Change auf
   client.py-Niveau).
6. Neuer E2E `test_ensure_manufacturer_part_backfills_missing` (lokal
   gegen parts.oe5xrx.org).
7. Commit, Push, PR, Copilot review loop, squash-merge.
8. Re-Trigger PowerBoard v1.1 → INA226 Re-Check → MfrPart muss da sein.
