# PR-5 — Sync-Ready Foundation

**Status:** Spec ready.
**Scope:** Bundle 5 backlog items so the production marathon-sync (re-pflegen
aller bestehenden Releases ins InvenTree) auf einmal durchläuft, statt
mehrfach abzubrechen oder Duplikate zu erzeugen.
**Backlog items addressed:** #13, #15, #19, #16, #17.
**Predecessor:** PR-4 (Safety & Reports) — `main` @ 2f773b9.
**Erstellt:** 2026-06-04.

---

## Motivation

Der User muss schnellstmöglich **alle bestehenden Releases** ins InvenTree
re-syncen — danach Bestandsaufnahme zuhause, dann fehlende Teile bestellen.
Die fünf Items in dieser PR addressieren genau die zwei Pain Points, die
einen Marathon-Sync sonst zerschießen:

1. **Daten-Integrität bei Re-Sync:** #13 (MPN+Mfr-Dedup) + #19 (Wert-
   Normalisierung) verhindern, dass derselbe physische Bauteil als
   `R 10k 0805` und `R 10K 0805` zwei InvenTree-Parts wird.
2. **Robustheit über lange Läufe:** #16 (kein früher `sys.exit(1)`) + #17
   (Retry) sorgen dafür, dass ein einzelner 502 von Mouser nicht den
   ganzen Sync-Job killt.
3. **Bestand vs. Bedarf:** #15 (Min-Stock aus BOM-Qty) macht die InvenTree-
   "Low Stock"-Page brauchbar als Einkaufsliste.

Alle fünf sind klein genug, dass sie in einer PR + einem Test-Setup
zusammenpassen. Sequentielle PRs würden den Marathon-Sync nur verzögern.

---

## Goals

- Nach Re-Sync eines Release: **kein** duplizierter Part durch Naming-
  Variationen oder MPN-Schreibweise-Differenzen.
- `--planned-builds N` (Default 10): `Part.minimum_stock` reflektiert
  realistischen Jahresbedarf pro Bauteil.
- Ein einzelner Fail (LCSC down, Mouser-502, unauffindbarer SKU) erzeugt
  Log-Eintrag, **kein** Job-Abbruch — alle anderen Parts werden trotzdem
  synced, Summary am Ende, Exit-Code 1.
- Drei transiente HTTP-Fails am Stück (Status 429/5xx) werden automatisch
  retried mit 0s / 2s / 4s Backoff.

## Non-Goals

- Keine Octopart/DigiKey-Integration.
- Keine SI-Prefix-Konversion (`1000` → `1k`) — zu fehleranfällig bei
  `1000pF` vs `1nF`. Nur whitespace/case/symbol-Normalisierung.
- Keine pytest-Migration von `inventree_sync` (das ist #21, eigenes PR).
- Keine Source-Commit/Release-URL als PartParameter (#18, eigenes PR).

---

## Architektur

### #13 — MPN+Manufacturer Dedup

**Neue Funktion** in `scripts/inventree_sync/client.py`:

```python
def find_part_by_mpn_and_manufacturer(
    api: InvenTreeAPI, mpn: str, manufacturer_name: str
) -> Optional[Part]:
    """Find an existing Part by ManufacturerPart MPN + manufacturer name.

    Returns the linked Part if a ManufacturerPart with the given MPN exists
    AND its manufacturer matches (case-insensitive). Returns None otherwise.

    Idempotent + defensive: post-filters on MPN AND on manufacturer.name
    because some InvenTree server versions silently ignore the MPN filter
    (same pattern as find_part_by_name).
    """
```

Implementation:
1. `ManufacturerPart.list(api, MPN=mpn)`.
2. Post-Filter: `mp.MPN == mpn AND Company(api, pk=mp.manufacturer).name.lower() == manufacturer_name.lower()`.
3. Cache `Company`-Lookups (process-lifetime, analog `_parameter_template_cache`).

**Insert-Point in `part_manager.ensure_parts_exist`** — der Lookup-Kette
folgend, von most-specific zu fallback:

```
1. find_existing_part(lcsc_skus + mouser_skus)   # SKU exact match
2. fetch part_data (LCSC/Mouser)                 # need MPN+Mfr
3. find_part_by_mpn_and_manufacturer(...)         # NEW — MPN+Mfr match
4. find_part_by_name(generated_name)              # name match
5. create_part_in_inventree(...)                  # genuine new
```

Aktuell endet `ensure_parts_exist` Zeile ~195 mit `find_part_by_name`-
Aufruf — der MPN+Mfr-Check sitzt **davor** (specific > name). Wenn er
trifft, wird wie bei `find_part_by_name` `ensure_supplier_parts` zum
Anbinden der SKUs aufgerufen.

### #15 — Minimum-Stock from BOM Qty

**CLI:** `--planned-builds N` an `bom_export.py:parse_args` (Default 10
per Backlog-Spec).

**Logik in `populate_bom`** (oder in einer neuen `_update_min_stock`-
Hilfsfunktion, die `populate_bom` am Ende aufruft):

```python
def _update_min_stock(entries: list[BomEntry], planned_builds: int) -> None:
    for entry in entries:
        for inv_part in entry.inventree_part:
            current = getattr(inv_part, "minimum_stock", 0) or 0
            needed = entry.qty * planned_builds
            if needed > current:
                try:
                    inv_part.save({"minimum_stock": needed})
                    log.info("Set min_stock=%d on pk=%s (%s × %d builds)",
                             needed, inv_part.pk, entry.qty, planned_builds)
                except Exception as exc:
                    log.warning("min_stock update failed pk=%s: %s",
                                inv_part.pk, exc)
```

- **"Higher wins":** Wenn ein Part durch zwei Assemblies referenziert wird
  (jeweils qty=2, qty=5, planned_builds=10), bleibt der höhere Wert (50)
  stehen. Das spiegelt den realen Bedarf.
- **Dry-Run:** kein Effekt — `populate_bom` wird in dry-run nicht
  aufgerufen.
- **PCB/Stencil/Assembly-Parts:** ignoriert (kein BomEntry referenziert sie
  als sub-part).

### #19 — Value Normalization

**Neuer Helper** in `scripts/inventree_sync/categories.py`:

```python
def _normalize_value(value: str) -> str:
    """Normalize a KiCad value string for stable part-name generation.

    Rules (conservative):
      - Strip Unicode Omega (Ω U+03A9, Ohm-Sign U+2126).
      - Lowercase K (kilo): "10K" → "10k". M stays (Mega vs milli ambiguity).
      - µ → u (ASCII-friendly).
      - Strip whitespace between number and unit: "10 k" → "10k", "100 nF" → "100nF".
      - Idempotent: f(f(x)) == f(x).

    Examples:
      _normalize_value("10K")     → "10k"
      _normalize_value("10 kΩ")   → "10k"
      _normalize_value("100 nF")  → "100nF"
      _normalize_value("4.7µF")   → "4.7uF"
      _normalize_value("1MΩ")     → "1M"      # M preserved (mega)
    """
```

**Applied only on R/C/L/CP/XTAL** value-tokens in `generate_part_name`.
Andere Part-Namen (z.B. `STM32U575CITx`) durchlaufen **nicht** durch den
Normalizer — die werden als Identitäts-String an InvenTree gereicht.

Konkrete Code-Änderung in `generate_part_name`:
```python
val = re.sub(r"\s*/\s*", "/", kicad_value.strip())
val = re.sub(r"\s+", " ", val).strip()
if kicad_part in {"R", "C", "C_Polarized", "L", "L_Iron", "Crystal"}:
    val = _normalize_value(val)
# ... rest unchanged ...
```

### #16 — Aggregated Error Output

**Neuer Sammler** in `scripts/bom_export.py` (kein neues Modul — klein):

```python
class ErrorCollector:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str, str]] = []  # (category, target, reason)

    def add(self, category: str, target: str, reason: str) -> None:
        self.errors.append((category, target, reason))

    def has_errors(self) -> bool:
        return bool(self.errors)

    def print_summary(self) -> None:
        if not self.errors: return
        log.error("=" * 60)
        log.error("Sync completed with %d error(s):", len(self.errors))
        for cat, tgt, reason in self.errors:
            log.error("  [%s] %s — %s", cat, tgt, reason)
        log.error("=" * 60)
```

**Replace** `sys.exit(1)` in `match_supplier_parts` (Zeile ~179) durch:
- `collector.add("Parts", entry.reference, f"no InvenTree match (...)")`.
- Continue loop.

**main():** Am Ende — nach `attach_kibot_outputs` — prüfen:
```python
if collector.has_errors():
    collector.print_summary()
    sys.exit(1)
```

`match_supplier_parts` Signatur:
```python
def match_supplier_parts(
    api, entries,
    reporter: Optional[DryRunReporter] = None,
    collector: Optional[ErrorCollector] = None,
) -> None:
```
Wenn `collector is None` und es Fehler gibt → Fallback auf `sys.exit(1)` (backwards-compat für andere Aufrufer).

### #17 — Retry with Backoff

**Neuer Helper** in `scripts/inventree_sync/fetchers.py`:

```python
def _make_retry_session() -> requests.Session:
    """requests.Session with urllib3 Retry on transient 5xx/429.

    Settings:
      total=3                — three retries beyond the initial attempt.
      backoff_factor=2       — sleeps 0s, 2s, 4s between attempts.
      status_forcelist=[429, 500, 502, 503, 504]
      allowed_methods=[GET, POST] — LCSC search uses POST.
      raise_on_status=False  — let calling code see the final response,
                               since LCSC/Mouser return parseable JSON
                               even on some 4xx (which we want to log).
    """
    session = requests.Session()
    retry = urllib3.util.Retry(
        total=3, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
```

- **LCSCFetcher:** `self.session = _make_retry_session()` (statt naked
  `requests.Session()`). UA + Accept-Language Headers wie bisher.
- **MouserFetcher:** bekommt erstmals `self.session = _make_retry_session()`;
  `fetch()` ruft `self.session.post(...)` statt `requests.post(...)`.
- **Image-Downloads in `client.py`:** **unverändert** — `requests.get(...)`
  bleibt. PerimeterX-Blocks sind kein transient-failure; ein Retry
  verschlimmert nur Logs.

---

## Tests

### E2E (`scripts/e2e_revision_handling.py`)

Drei neue Tests, gleicher Stil wie die existierenden 13 (run-ID-prefixed,
real-server, cleanup-aware):

1. **`test_mpn_mfr_dedup`** — Synct ein 1-Zeilen-BOM `[LCSC=C25804]`,
   dann ein anderes 1-Zeilen-BOM `[LCSC=C25803]` **mit demselben MPN
   und Manufacturer** (LCSC kennt z.B. mehrere SKUs für dasselbe TI-IC).
   Erwartung: exakt 1 Part angelegt, 2 SupplierParts (oder die zwei sind
   schon im Datenbestand — checken über Part-Existenz, nicht Create).

2. **`test_value_normalization`** — BOM mit Value `"10K"` (großes K) →
   InvenTree-Part-Name muss `R 10k 0805` sein (klein-k).

3. **`test_minimum_stock_set`** — Sync mit `--planned-builds 5` und
   einem BOM-Eintrag `qty=3` → `Part.minimum_stock == 15`. Zweiter Sync
   mit `--planned-builds 2` → bleibt 15 ("higher wins").

### Pytest (zwei neue Files)

**`scripts/tests/test_normalization.py`** — ~10 Cases für `_normalize_value`:

```python
def test_normalize_strip_omega():       assert _normalize_value("10kΩ") == "10k"
def test_normalize_uppercase_k():       assert _normalize_value("10K") == "10k"
def test_normalize_lowercase_m_stays(): assert _normalize_value("10m") == "10m"  # milli
def test_normalize_uppercase_M_stays(): assert _normalize_value("1M") == "1M"    # mega
def test_normalize_micro_to_u():        assert _normalize_value("4.7µF") == "4.7uF"
def test_normalize_strip_whitespace():  assert _normalize_value("10 k") == "10k"
def test_normalize_compound():          assert _normalize_value("100 nF") == "100nF"
def test_normalize_idempotent():        assert _normalize_value(_normalize_value("10K")) == "10k"
def test_normalize_passthrough():       assert _normalize_value("8MHz") == "8MHz"
def test_normalize_empty():             assert _normalize_value("") == ""
```

**`scripts/tests/test_error_collector.py`** — ~5 Cases:

```python
def test_empty_collector_has_no_errors(): ...
def test_add_one_error(): ...
def test_multiple_errors_preserved_in_order(): ...
def test_print_summary_no_errors_is_quiet(): ...
def test_print_summary_with_errors_logs_each(): ...
```

### Verifiziert ohne Tests

- Retry-Verhalten: manual probe (`MOUSER_API_KEY=garbage python -c '...'`)
  zur Verifizierung des Backoff-Timings. Pytest-Mocking von urllib3-Adapter
  ist mehr Aufwand als Wert.

---

## CLI changes

`bom_export.py` — eine neue Option:

```
--planned-builds N   Multiplier for Part.minimum_stock = entry.qty × N
                     (Default: 10).  Higher existing values are preserved.
```

Workflow-Aufruf bleibt **unverändert** rückwärtskompatibel — wer den Flag
nicht setzt, kriegt Default 10 (matched the backlog spec).

---

## Backwards compatibility

| Change | Compat-Risk | Mitigation |
|---|---|---|
| `find_part_by_mpn_and_manufacturer` als zusätzlicher Lookup | Keiner — additiv | Wenn LookUp leer ist, läuft die bisherige Logik unverändert weiter |
| `_normalize_value` ändert generierte Namen für `10K`/`10 kΩ` Cases | Niedrig — neue Parts kriegen kanonische Form; ein "schon falsch geschriebener" Bestands-Part wird via MPN+Mfr-Dedup (#13) trotzdem gefunden | #13 deckt #19 ab |
| `match_supplier_parts` exited nicht mehr früh | Keiner — main() exit-Code bleibt 1 bei Fehlern; einziger Unterschied: alle Fehler werden gesammelt | n/a |
| `--planned-builds` default 10 | Niedrig — wenn ein Part bisher min_stock=0 hatte, wird er auf qty×10 erhöht | Spec sagt "higher wins" — User kann es manuell überschreiben |
| Retry-Adapter | Keiner — Latenz steigt nur bei Fehlern, im Erfolgsfall identisch | n/a |

---

## Files touched

```
scripts/inventree_sync/client.py        +40 LOC  (find_part_by_mpn_and_manufacturer)
scripts/inventree_sync/part_manager.py  +15 LOC  (MPN+Mfr lookup branch)
scripts/inventree_sync/categories.py    +30 LOC  (_normalize_value + integration)
scripts/inventree_sync/fetchers.py      +25 LOC  (_make_retry_session, Mouser session)
scripts/bom_export.py                   +60 LOC  (ErrorCollector, --planned-builds, min-stock)
scripts/e2e_revision_handling.py       +120 LOC  (3 new tests)
scripts/tests/test_normalization.py     +50 LOC  (NEW)
scripts/tests/test_error_collector.py   +30 LOC  (NEW)
```

**Total: ~370 LOC.**

---

## Implementation order

1. **#19 `_normalize_value`** (independent, pure func, pytest first).
2. **#13 `find_part_by_mpn_and_manufacturer`** + integration in `ensure_parts_exist`.
3. **#17 retry session** (independent, both fetchers).
4. **#16 `ErrorCollector`** + `match_supplier_parts` signature change.
5. **#15 `--planned-builds` + min-stock update** in `populate_bom`.
6. E2E tests against the live InvenTree server.

Each step is independently revertible — no cross-dependency.

---

## Open items (deferred to follow-up PRs)

- **#14** Regex/Pattern-Category-Map → eigene PR (genug Scope für eine separate Round).
- **#18** Source-Commit/Release-URL als Custom-PartParameter → eigene PR (braucht Workflow-YAML-Änderung).
- **#20** Webhook/PR-Comment nach Sync → eigene PR.
- **#21** Vollständige pytest-Migration von `inventree_sync` → eigene PR (groß).
- **Follow-up:** Multi-MPN-Alternates (ein Part = mehrere MPNs, z.B. Pin-kompatible second-source).
- **Follow-up:** Mixed-currency price-merge im Cost-Report.
