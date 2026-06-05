# PR-9 Ensure ManufacturerPart on Existing — Implementation Plan

**Goal:** Hot-Fix für die Idempotenz-Lücke aus PR-5 — `ensure_supplier_parts`
zieht jetzt auch fehlende ManufacturerParts nach. Refactor: gemeinsame
Helper-Funktion `ensure_manufacturer_part` nutzt sowohl `create_part_in_inventree`
als auch `ensure_supplier_parts`.

---

## File Structure

**Modified:**
- `scripts/inventree_sync/client.py` — neuer Helper `ensure_manufacturer_part`,
  zwei Call-sites umgestellt.
- `scripts/e2e_revision_handling.py` — neuer Test
  `test_ensure_manufacturer_part_backfills_missing` + Registrierung.

**Untouched:**
- Alles andere (fetchers.py, part_manager.py, models.py, …).

---

## Task 1: Helper + Refactor

- [ ] **Step 1.1: `ensure_manufacturer_part` in client.py**

Direkt nach `get_or_create_manufacturer` (Zeile ~120) einfügen:

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
        alternates with the same MPN are NOT treated as already-linked.
      - get_or_create_manufacturer fails (returns None)

    Post-filters on (mp.part == part.pk) AND mp.MPN == mpn AND
    Company(mp.manufacturer).name matches case-insensitively, because some
    InvenTree server versions silently ignore filter kwargs.

    Errors during Create are logged but never raised — sync-loop callers
    must not bail on a single MfrPart-create failure.
    """
    mpn = (mpn or "").strip()
    manufacturer_name = (manufacturer_name or "").strip()
    if not mpn or not manufacturer_name:
        return

    # Idempotency check on (MPN, manufacturer-name) — comparing only MPN
    # would incorrectly skip second-source alternates.
    target_name_lower = manufacturer_name.lower()
    try:
        existing = ManufacturerPart.list(api, part=part.pk)
    except Exception as exc:
        logger.warning(
            "ManufacturerPart lookup failed for part=%s; skipping MfrPart "
            "create to preserve idempotency (next sync retries): %s",
            part.pk, exc)
        return
    for mp in existing:
        if int(getattr(mp, "part", -1)) != int(part.pk):
            continue   # defensive: part= filter may have been ignored
        if (mp.MPN or "").strip() != mpn:
            continue
        existing_mfr_name = _resolve_manufacturer_name(api, int(mp.manufacturer))
        if not existing_mfr_name:
            logger.warning("Cannot resolve Company name; skipping to preserve idempotency.")
            return
        if existing_mfr_name.lower() == target_name_lower:
            return  # exact (MPN, manufacturer) already linked on THIS Part

    # Need a Manufacturer Company first.
    manufacturer = get_or_create_manufacturer(api, manufacturer_name)
    if manufacturer is None:
        return

    try:
        ManufacturerPart.create(api, {
            "part": part.pk,
            "manufacturer": manufacturer.pk,
            "MPN": mpn,
        })
        logger.info(
            "Created ManufacturerPart %s / %s for Part pk=%s",
            manufacturer_name, mpn, part.pk,
        )
    except Exception as exc:
        logger.warning(
            "ManufacturerPart creation failed for Part pk=%s (mpn=%s): %s",
            part.pk, mpn, exc,
        )
```

- [ ] **Step 1.2: `create_part_in_inventree` — replace inline block**

In `client.py` finde die "3. Manufacturer part" Sektion und ersetze
den ganzen Block durch:

```python
    # 3. Manufacturer part (idempotent via ensure_manufacturer_part)
    ensure_manufacturer_part(api, part, part_data.mpn, part_data.manufacturer)
```

- [ ] **Step 1.3: `ensure_supplier_parts` — neuer Aufruf**

In `ensure_supplier_parts` direkt nach der SKU-Normalisierung
(`lcsc_skus = list(...); mouser_skus = list(...)`) — also bevor die
existing-skus query oder die LCSC/Mouser-Loops kommen — einfügen:

```python
    # PR-9: ManufacturerPart-Linkage nachziehen, falls fehlend (z.B. weil
    # der Part im ersten Sync wegen Company-403 ohne MfrPart angelegt
    # wurde). Idempotent auf das (MPN, manufacturer-name)-Paar — derselbe
    # MPN von einem anderen Manufacturer (Second-Source-Alternate) wird
    # absichtlich als zusätzlicher MfrPart angelegt.
    ensure_manufacturer_part(api, part, part_data.mpn, part_data.manufacturer)
```

- [ ] **Step 1.4: Smoke-Test imports**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -c "
from scripts.inventree_sync.client import (
    ensure_manufacturer_part, ensure_supplier_parts, create_part_in_inventree
)
print('imports OK')
"
```

Expected: `imports OK`.

- [ ] **Step 1.5: Full pytest still green**

```bash
python3 -m pytest scripts/tests/ -q
```

Expected: 129/129 grün (kein pytest-Test betroffen — alles Mock-frei).

---

## Task 2: E2E test

- [ ] **Step 2.1: Test hinzufügen**

In `scripts/e2e_revision_handling.py` direkt nach
`test_generic_connector_mpn_disambiguation` (vor Entry-Point):

```python
def test_ensure_manufacturer_part_backfills_missing(api: InvenTreeAPI) -> None:
    """ensure_supplier_parts now backfills a missing ManufacturerPart (PR-9).

    Reproduces the PowerBoard-v1.1 first-sync failure mode: a Part exists
    without ManufacturerPart linkage. Calling ensure_supplier_parts on it
    must create the MfrPart from part_data. Idempotent: a second call
    must not produce a duplicate.
    """
    from inventree.company import ManufacturerPart
    from inventree_sync.client import ensure_supplier_parts
    from inventree_sync.models import PartData

    target = _track(Part.create(api, {
        "name": f"{PREFIX} MfrBackfill",
        "description": "mfr backfill",
        "active": True,
        "component": True,
    }))
    pd = PartData(
        mpn=f"{PREFIX}-MPN-BACKFILL",
        manufacturer=f"{PREFIX} BackfillMfr",
    )

    # Pre-condition: no MfrPart yet.
    pre = ManufacturerPart.list(api, part=target.pk)
    assert len(pre) == 0, f"expected 0 MfrPart, got {len(pre)}"

    # Call 1: should create the MfrPart.
    ensure_supplier_parts(api, target, pd,
                          lcsc_supplier=None, mouser_supplier=None)
    mps = ManufacturerPart.list(api, part=target.pk)
    assert len(mps) == 1, f"expected 1 MfrPart after first call, got {len(mps)}"
    assert (mps[0].MPN or "").strip() == pd.mpn, (
        f"MfrPart.MPN expected {pd.mpn!r}, got {mps[0].MPN!r}")

    # Track the manufacturer Company for cleanup.
    _created_companies.append(Company(api, pk=mps[0].manufacturer))

    # Call 2: idempotent — must not produce a second MfrPart.
    ensure_supplier_parts(api, target, pd,
                          lcsc_supplier=None, mouser_supplier=None)
    mps2 = ManufacturerPart.list(api, part=target.pk)
    assert len(mps2) == 1, (
        f"expected MfrPart-count to remain 1 after second call, got {len(mps2)}")

    print(f"  PASS  ensure_manufacturer_part backfill+idempotent "
          f"(pk={target.pk}, MfrPart pk={mps[0].pk})")
```

- [ ] **Step 2.2: Register in `main()`**

Test-Tuple um den neuen Test ergänzen.

- [ ] **Step 2.3: Run E2E locally**

```bash
source ~/.inventree_test.env
python3 scripts/e2e_revision_handling.py
```

Expected: 18/18 tests pass.

---

## Task 3: Commit, Push, PR, Copilot, Merge

- [ ] **Step 3.1: Commit + push**
- [ ] **Step 3.2: gh pr create**
- [ ] **Step 3.3: Copilot review loop**
- [ ] **Step 3.4: Squash-merge + sync main**

---

## Task 4: Re-Trigger PowerBoard v1.1 + verify

```bash
gh workflow run "Create Release Docs" --repo OE5XRX/HW-Module-PowerBoard --ref v1.1
```

Nach Abschluss:

```bash
source ~/.inventree_test.env
python3 -c "
from inventree.api import InvenTreeAPI
from inventree.part import Part
from inventree.company import ManufacturerPart
api = InvenTreeAPI()
# Stichprobe: INA226 muss jetzt MfrPart haben
for p in Part.list(api, search='INA226'):
    if p.name == 'INA226':
        mps = ManufacturerPart.list(api, part=p.pk)
        print(f'INA226 pk={p.pk} has {len(mps)} MfrPart(s)')
        for mp in mps:
            print(f'  pk={mp.pk} MPN={mp.MPN!r}')
        break
"
```

Expected: INA226 hat 1 ManufacturerPart mit MPN=INA226.
