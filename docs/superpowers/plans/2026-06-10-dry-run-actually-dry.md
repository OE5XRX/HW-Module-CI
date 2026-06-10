# Dry-Run Actually Dry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `ensure_part_for_order_line` from making real InvenTree writes (`Part.create`, `Category.create`, `SupplierPart.create`, `ManufacturerPart.create`, image uploads, parameter updates) when the importer is invoked with `--dry-run`.

**Architecture:** Thread an optional `DryRunReporter` (the existing helper from `inventree_sync/dry_run.py`) through `ensure_part_for_order_line` and `_import_one_order` / `main()`. When the reporter is present, all four resolution branches (SKU-Hit, MPN-Hit, Name-Hit, Create) record their decision and return `(None, None)` instead of writing. The CLI instantiates the reporter only for `--dry-run` and calls `print_report()` at the end. Same pattern that `bom_export.py` + `part_manager.ensure_parts_exist` already use.

**Tech Stack:** Python 3.13, `inventree==0.23.1`, `unittest.mock`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-10-dry-run-actually-dry-design.md`

**Working directory for all commands:** `/home/pbuchegger/OE5XRX/HW-Module-CI`

---

## Conventions

- All tests use `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` at the top so `inventree_sync` and the script imports resolve from `scripts/`.
- Activate the venv before pytest: `source .venv/bin/activate && pytest <path>`.
- Commit messages: `fix(import-orders): <subject>`.
- TDD strict: failing test → confirm fail → implement → confirm pass → commit.

---

## File Structure

| File | What changes |
|---|---|
| `scripts/inventree_sync/order_import.py` | `ensure_part_for_order_line`: new `reporter` kwarg, return-type lockerung, four dry-run branches |
| `scripts/import_supplier_order.py` | `_import_one_order` + `main()` thread the reporter through; PO record + `print_report()` |
| `scripts/tests/test_order_import_part_resolution.py` | 5 new tests covering the dry-run branches |
| `scripts/tests/test_order_import_cli.py` | 1 new test covering CLI reporter wiring |

---

## Task 1: `ensure_part_for_order_line` dry-run support

**Files:**
- Modify: `scripts/inventree_sync/order_import.py:343-452`
- Test: `scripts/tests/test_order_import_part_resolution.py`

The function currently returns `tuple[Part, SupplierPart]`. After this task it returns `tuple[Optional[Part], Optional[SupplierPart]]` — `(None, None)` in dry-run mode, unchanged tuple in real-run mode.

- [ ] **Step 1: Write the 5 failing tests**

Open `scripts/tests/test_order_import_part_resolution.py` and append after the existing tests:

```python
# ---------------------------------------------------------------------------
# Dry-run path tests (reporter passed in → no writes, only records)
# ---------------------------------------------------------------------------

from inventree_sync.dry_run import DryRunReporter  # noqa: E402


def _supplier_setup():
    """Fresh fetcher / supplier mocks used across dry-run tests."""
    lcsc_fetcher = MagicMock()
    mouser_fetcher = MagicMock()
    lcsc_supplier = MagicMock(); lcsc_supplier.pk = 1; lcsc_supplier.name = "LCSC"
    mouser_supplier = MagicMock(); mouser_supplier.pk = 2; mouser_supplier.name = "Mouser"
    return lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier


def test_dry_run_sku_hit_records_reuse_and_no_writes():
    """SKU lookup hit → REUSE record, no ensure_supplier_parts/create_part call."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as find_exist, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat:
        find_exist.return_value = _part_mock(pk=101)
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    lookup_sp.assert_not_called()
    esp.assert_not_called()
    create.assert_not_called()
    rcat.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "REUSE"
    assert r.category == "Parts"
    assert r.target == line.sku
    assert "pk=101" in r.detail


def test_dry_run_mpn_hit_records_reuse_via_mpn_and_no_writes():
    """MPN+Mfr hit → REUSE record mentioning MPN, no ensure_supplier_parts call."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp:
        fe.return_value = None
        fmpn.return_value = _part_mock(pk=202)
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    fname.assert_not_called()         # MPN-Hit short-circuits before name lookup
    esp.assert_not_called()
    create.assert_not_called()
    rcat.assert_not_called()
    lookup_sp.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "REUSE"
    assert "MPN+Mfr" in r.detail
    assert "pk=202" in r.detail


def test_dry_run_name_hit_records_reuse_via_name_and_no_writes():
    """Name lookup hit → REUSE record mentioning name, no ensure_supplier_parts call."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = _part_mock(pk=303)
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    esp.assert_not_called()
    create.assert_not_called()
    rcat.assert_not_called()
    lookup_sp.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "REUSE"
    assert "name" in r.detail.lower()
    assert "pk=303" in r.detail


def test_dry_run_create_records_create_and_no_writes():
    """No lookup hit → CREATE record with planned name, no actual create."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    rcat.assert_not_called()
    create.assert_not_called()
    esp.assert_not_called()
    lookup_sp.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "CREATE"
    assert r.category == "Parts"
    assert r.target == line.sku
    assert "0805B333K500NT" in r.detail   # planned name = part_data.mpn


def test_dry_run_fetcher_failure_still_records_create_using_file_data():
    """Supplier API None → fallback PartData → CREATE record from file row mpn."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = None   # supplier API down
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    create.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "CREATE"
    # Name fallback: part_data.mpn (None) → line.mpn ("0805B333K500NT")
    assert "0805B333K500NT" in r.detail
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_part_resolution.py -v -k "dry_run"`

Expected: all 5 new tests fail with either `TypeError: ensure_part_for_order_line() got an unexpected keyword argument 'reporter'` or assertions about `part is None` failing because the current code returns a real Part mock.

- [ ] **Step 3: Implement the dry-run threading in `ensure_part_for_order_line`**

Open `scripts/inventree_sync/order_import.py`. First, update the function signature and docstring (around line 343-365):

```python
def ensure_part_for_order_line(
    api: InvenTreeAPI,
    line: SupplierOrderLine,
    supplier_kind: str,                       # "LCSC" or "Mouser"
    lcsc_fetcher: Optional[LCSCFetcher],
    mouser_fetcher: Optional[MouserFetcher],
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
    category_map: dict,
    *,
    reporter: Optional["DryRunReporter"] = None,
) -> tuple[Optional[Part], Optional[SupplierPart]]:
    """Resolve a line to (Part, SupplierPart), creating both if needed.

    Dedup chain (same priority as part_manager.ensure_parts_exist):
      1. find_existing_part via the line's SKU.
      2. Fetcher (LCSC or Mouser) → PartData.
      3. find_part_by_mpn_and_manufacturer.
      4. find_part_by_name (name = part_data.mpn or line.mpn or sku).
      5. create_part_in_inventree.

    On fetcher failure (3rd-party API down or SKU unknown), a minimal
    PartData is synthesised from the file row so the Part can still be
    created — just without datasheet/image/parameter enrichment.

    Fetcher / supplier arguments are ``Optional`` because a single-supplier
    import run (e.g. ``--lcsc-csv`` without ``--mouser-xls``) instantiates
    only the side it needs. The *unused* side may legally be ``None``; the
    side matching ``supplier_kind`` MUST be non-None — this is enforced at
    the top of the function so misuse fails loud at the call site rather
    than silently NPE-ing inside the dedup chain.

    Dry-run (``reporter is not None``): all read-only lookups still run
    (SKU → fetcher → MPN+Mfr → Name) but every write side-effect
    (``ensure_supplier_parts``, ``resolve_part_category``,
    ``create_part_in_inventree``, ``_lookup_supplier_part``) is replaced by
    a ``reporter.record(...)`` call and the function returns
    ``(None, None)``. Caller MUST tolerate the nullable return.
    """
```

Add the lazy import for `DryRunReporter` near the top of the file (after the existing imports from `.client` / `.fetchers`, around line 38):

```python
from .dry_run import DryRunReporter
```

Then replace the four resolution branches (lines 393-452) with the dry-run-aware versions. Find this current block:

```python
    # 1. SKU lookup
    existing = find_existing_part(api, lcsc_skus, mouser_skus)
    if existing is not None:
        return existing, _lookup_supplier_part(api, line.sku)

    # 2. Supplier fetch
    if is_lcsc:
        part_data = lcsc_fetcher.fetch_by_sku(line.sku)
    else:
        part_data = mouser_fetcher.fetch(line.sku)
    if part_data is None:
        logger.warning(
            "Supplier API returned no data for %s SKU %r — "
            "falling back to file row.", supplier_kind, line.sku)
        part_data = _partdata_from_line(line)
    # Always stamp the right SKU back onto part_data so downstream creates
    # have it.
    if is_lcsc:
        part_data.lcsc_sku = line.sku
    else:
        part_data.mouser_sku = line.sku

    # 3. MPN + Manufacturer
    mpn = (part_data.mpn or line.mpn or "").strip()
    mfr = (part_data.manufacturer or line.mfr_name or "").strip()
    if mpn and mfr:
        by_mpn = find_part_by_mpn_and_manufacturer(api, mpn, mfr)
        if by_mpn is not None:
            ensure_supplier_parts(
                api, by_mpn, part_data,
                lcsc_supplier, mouser_supplier,
                lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
            )
            return by_mpn, _lookup_supplier_part(api, line.sku)

    # 4. Name lookup
    name = (part_data.mpn or line.mpn or line.sku).strip()
    by_name = find_part_by_name(api, name)
    if by_name is not None:
        ensure_supplier_parts(
            api, by_name, part_data,
            lcsc_supplier, mouser_supplier,
            lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
        )
        return by_name, _lookup_supplier_part(api, line.sku)

    # 5. Create
    category = resolve_part_category(
        api, "", part_data, line.package, category_map,
    )
    created = create_part_in_inventree(
        api, name, part_data, category,
        lcsc_supplier, mouser_supplier,
        lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
    )
    if created is None:
        raise RuntimeError(
            f"create_part_in_inventree returned None for line {line.sku!r}"
        )
    return created, _lookup_supplier_part(api, line.sku)
```

Replace with:

```python
    # 1. SKU lookup
    existing = find_existing_part(api, lcsc_skus, mouser_skus)
    if existing is not None:
        if reporter is not None:
            reporter.record(
                "REUSE", "Parts", line.sku,
                f"existing pk={existing.pk}",
            )
            return None, None
        return existing, _lookup_supplier_part(api, line.sku)

    # 2. Supplier fetch (read-only; runs in dry-run too)
    if is_lcsc:
        part_data = lcsc_fetcher.fetch_by_sku(line.sku)
    else:
        part_data = mouser_fetcher.fetch(line.sku)
    if part_data is None:
        logger.warning(
            "Supplier API returned no data for %s SKU %r — "
            "falling back to file row.", supplier_kind, line.sku)
        part_data = _partdata_from_line(line)
    # Always stamp the right SKU back onto part_data so downstream creates
    # have it.
    if is_lcsc:
        part_data.lcsc_sku = line.sku
    else:
        part_data.mouser_sku = line.sku

    # 3. MPN + Manufacturer
    mpn = (part_data.mpn or line.mpn or "").strip()
    mfr = (part_data.manufacturer or line.mfr_name or "").strip()
    if mpn and mfr:
        by_mpn = find_part_by_mpn_and_manufacturer(api, mpn, mfr)
        if by_mpn is not None:
            if reporter is not None:
                reporter.record(
                    "REUSE", "Parts", line.sku,
                    f"via MPN+Mfr pk={by_mpn.pk}",
                )
                return None, None
            ensure_supplier_parts(
                api, by_mpn, part_data,
                lcsc_supplier, mouser_supplier,
                lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
            )
            return by_mpn, _lookup_supplier_part(api, line.sku)

    # 4. Name lookup
    name = (part_data.mpn or line.mpn or line.sku).strip()
    by_name = find_part_by_name(api, name)
    if by_name is not None:
        if reporter is not None:
            reporter.record(
                "REUSE", "Parts", line.sku,
                f"via name {name!r} pk={by_name.pk}",
            )
            return None, None
        ensure_supplier_parts(
            api, by_name, part_data,
            lcsc_supplier, mouser_supplier,
            lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
        )
        return by_name, _lookup_supplier_part(api, line.sku)

    # 5. Create
    if reporter is not None:
        reporter.record(
            "CREATE", "Parts", line.sku,
            f"name={name!r}",
        )
        return None, None
    category = resolve_part_category(
        api, "", part_data, line.package, category_map,
    )
    created = create_part_in_inventree(
        api, name, part_data, category,
        lcsc_supplier, mouser_supplier,
        lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
    )
    if created is None:
        raise RuntimeError(
            f"create_part_in_inventree returned None for line {line.sku!r}"
        )
    return created, _lookup_supplier_part(api, line.sku)
```

- [ ] **Step 4: Run the new tests + existing tests to verify pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_part_resolution.py -v`

Expected: All tests pass (8 existing + 5 new = 13 total).

- [ ] **Step 5: Run the whole suite for regression check**

Run: `source .venv/bin/activate && pytest scripts/tests/`

Expected: 186 + 5 = 191 passed. If something else broke, investigate before committing.

- [ ] **Step 6: Commit**

```bash
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_part_resolution.py
git commit -m "fix(import-orders): make ensure_part_for_order_line dry-run-aware

Thread a DryRunReporter through ensure_part_for_order_line. When present,
the four resolution branches (SKU-Hit / MPN-Hit / Name-Hit / Create)
record their decision via reporter.record(...) and return (None, None)
instead of calling create_part_in_inventree / ensure_supplier_parts /
resolve_part_category / _lookup_supplier_part. Supplier fetcher calls
are kept because they're read-only and provide the data needed for an
accurate planned-name in the CREATE record.

Return type relaxed to tuple[Optional[Part], Optional[SupplierPart]] —
the existing 2 call sites either already check or will be updated in
the next commit.

Fixes the bug surfaced during the first real --dry-run against
parts.oe5xrx.org where 10 parts + 2 categories + N MfrParts were
created despite the flag.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: CLI integration — `_import_one_order` + `main()` reporter wiring

**Files:**
- Modify: `scripts/import_supplier_order.py:86-196`
- Test: `scripts/tests/test_order_import_cli.py`

- [ ] **Step 1: Write the failing test**

Open `scripts/tests/test_order_import_cli.py` and append after the existing tests:

```python
def test_dry_run_instantiates_reporter_and_prints_report(tmp_path, monkeypatch):
    """--dry-run → DryRunReporter created, ensure_part called with it,
    print_report invoked, exit 0."""
    csv_file = tmp_path / "LCSC__WM_1.csv"
    csv_file.write_text(
        "LCSC Part Number,Manufacture Part Number,Manufacturer,Customer NO.,"
        "Package,Description,RoHS,Quantity,Unit Price($),Ext.Price($),"
        "Estimated lead time (business days),Updated lead time,"
        "Date Code / Lot No.\n"
        "C1,MPN1,M1,,0805,desc,YES,5,0.1,0.5,,,\n"
    )
    monkeypatch.setenv("INVENTREE_API_HOST", "http://localhost")
    monkeypatch.setenv("INVENTREE_API_TOKEN", "deadbeef")

    with patch("import_supplier_order.InvenTreeAPI"), \
         patch("import_supplier_order.LCSCFetcher"), \
         patch("import_supplier_order.MouserFetcher"), \
         patch("import_supplier_order.get_or_create_supplier") as gos, \
         patch("import_supplier_order.get_receive_location") as grl, \
         patch("import_supplier_order.ensure_part_for_order_line") as epfol, \
         patch("import_supplier_order.upsert_purchase_order") as upsert, \
         patch("import_supplier_order.load_category_map", return_value={}), \
         patch("import_supplier_order.DryRunReporter") as reporter_cls:
        reporter_instance = MagicMock()
        reporter_instance.records = []
        reporter_cls.return_value = reporter_instance
        gos.return_value = MagicMock(pk=1)
        grl.return_value = MagicMock(pk=7)
        # Dry-run path returns (None, None) per Task 1
        epfol.return_value = (None, None)
        upsert.return_value = MagicMock(
            action="DRY_RUN_CREATE", po_reference="WM",
            lines_added=1, lines_updated=0, lines_deleted=0,
        )
        rc = cli.main(["--lcsc-csv", str(csv_file), "--dry-run"])

    assert rc == 0
    reporter_cls.assert_called_once_with()
    reporter_instance.print_report.assert_called_once()
    # ensure_part_for_order_line must receive the reporter as kwarg
    assert epfol.call_count == 1
    assert epfol.call_args.kwargs.get("reporter") is reporter_instance


def test_real_run_does_not_instantiate_reporter(tmp_path, monkeypatch):
    """Without --dry-run, no DryRunReporter is created."""
    csv_file = tmp_path / "LCSC__WM_1.csv"
    csv_file.write_text(
        "LCSC Part Number,Manufacture Part Number,Manufacturer,Customer NO.,"
        "Package,Description,RoHS,Quantity,Unit Price($),Ext.Price($),"
        "Estimated lead time (business days),Updated lead time,"
        "Date Code / Lot No.\n"
        "C1,MPN1,M1,,0805,desc,YES,5,0.1,0.5,,,\n"
    )
    monkeypatch.setenv("INVENTREE_API_HOST", "http://localhost")
    monkeypatch.setenv("INVENTREE_API_TOKEN", "deadbeef")

    with patch("import_supplier_order.InvenTreeAPI"), \
         patch("import_supplier_order.LCSCFetcher"), \
         patch("import_supplier_order.MouserFetcher"), \
         patch("import_supplier_order.get_or_create_supplier") as gos, \
         patch("import_supplier_order.get_receive_location") as grl, \
         patch("import_supplier_order.ensure_part_for_order_line") as epfol, \
         patch("import_supplier_order.upsert_purchase_order") as upsert, \
         patch("import_supplier_order.load_category_map", return_value={}), \
         patch("import_supplier_order.DryRunReporter") as reporter_cls:
        gos.return_value = MagicMock(pk=1)
        grl.return_value = MagicMock(pk=7)
        epfol.return_value = (MagicMock(pk=100), MagicMock(pk=200, SKU="C1"))
        upsert.return_value = MagicMock(
            action="CREATED", po_reference="WM",
            lines_added=1, lines_updated=0, lines_deleted=0,
        )
        rc = cli.main(["--lcsc-csv", str(csv_file)])

    assert rc == 0
    reporter_cls.assert_not_called()
    # ensure_part_for_order_line called WITHOUT reporter kwarg (or with None)
    assert epfol.call_args.kwargs.get("reporter") is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_cli.py -v -k "reporter"`

Expected: both tests fail — `AttributeError: module 'import_supplier_order' has no attribute 'DryRunReporter'` or `epfol.call_args.kwargs.get("reporter")` returning the wrong thing because `_import_one_order` doesn't yet pass the kwarg through.

- [ ] **Step 3: Update `_import_one_order` + `main()` in the CLI**

Open `scripts/import_supplier_order.py`.

Add the import at the top (after the existing `from inventree_sync.order_import import ...`, around line 27):

```python
from inventree_sync.dry_run import DryRunReporter
```

Replace `_import_one_order` (current lines 86-135). Find:

```python
def _import_one_order(
    api: InvenTreeAPI,
    order: SupplierOrder,
    lcsc_fetcher: Optional[LCSCFetcher],
    mouser_fetcher: Optional[MouserFetcher],
    lcsc_supplier,
    mouser_supplier,
    category_map: dict,
    receive_location,
    dry_run: bool,
) -> int:
    """Process one parsed SupplierOrder. Returns exit-code (0 ok, 1 drift).

    *lcsc_fetcher* / *mouser_fetcher* are ``Optional`` because single-
    supplier runs (``--lcsc-csv`` xor ``--mouser-xls``) instantiate only
    the side they need. The side matching ``order.supplier_name`` MUST be
    non-None — ``ensure_part_for_order_line`` enforces that at the call
    site of every line.
    """
    supplier_kind = order.supplier_name  # "LCSC" or "Mouser"
    supplier = lcsc_supplier if supplier_kind == "LCSC" else mouser_supplier

    log.info("Resolving %d parts from %s order %s…",
             len(order.lines), supplier_kind, order.reference)

    sku_to_sp: dict = {}
    for line in order.lines:
        try:
            _, sp = ensure_part_for_order_line(
                api, line, supplier_kind,
                lcsc_fetcher, mouser_fetcher,
                lcsc_supplier, mouser_supplier,
                category_map,
            )
        except Exception as exc:
            log.error("Failed to resolve %s line %s: %s",
                      supplier_kind, line.sku, exc)
            return 1
        sku_to_sp[line.sku] = sp

    try:
        report = upsert_purchase_order(
            api=api, order=order, supplier=supplier,
            sku_to_supplier_part=sku_to_sp,
            receive_location=receive_location,
            dry_run=dry_run,
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    log.info(
        "%s PO %s — added=%d updated=%d deleted=%d",
        report.action, report.po_reference,
        report.lines_added, report.lines_updated, report.lines_deleted,
    )
    return 0
```

Replace with:

```python
def _import_one_order(
    api: InvenTreeAPI,
    order: SupplierOrder,
    lcsc_fetcher: Optional[LCSCFetcher],
    mouser_fetcher: Optional[MouserFetcher],
    lcsc_supplier,
    mouser_supplier,
    category_map: dict,
    receive_location,
    dry_run: bool,
    reporter: Optional[DryRunReporter] = None,
) -> int:
    """Process one parsed SupplierOrder. Returns exit-code (0 ok, 1 drift).

    *lcsc_fetcher* / *mouser_fetcher* are ``Optional`` because single-
    supplier runs (``--lcsc-csv`` xor ``--mouser-xls``) instantiate only
    the side they need. The side matching ``order.supplier_name`` MUST be
    non-None — ``ensure_part_for_order_line`` enforces that at the call
    site of every line.

    *reporter* is set when ``--dry-run`` is active. It's threaded into
    ``ensure_part_for_order_line`` so the resolution chain records
    decisions instead of executing writes. PO upsert decisions are
    recorded here using the ``UpsertReport.action`` from
    ``upsert_purchase_order`` (which is dry-run-aware on its own).
    """
    supplier_kind = order.supplier_name  # "LCSC" or "Mouser"
    supplier = lcsc_supplier if supplier_kind == "LCSC" else mouser_supplier

    log.info("Resolving %d parts from %s order %s…",
             len(order.lines), supplier_kind, order.reference)

    sku_to_sp: dict = {}
    for line in order.lines:
        try:
            _, sp = ensure_part_for_order_line(
                api, line, supplier_kind,
                lcsc_fetcher, mouser_fetcher,
                lcsc_supplier, mouser_supplier,
                category_map,
                reporter=reporter,
            )
        except Exception as exc:
            log.error("Failed to resolve %s line %s: %s",
                      supplier_kind, line.sku, exc)
            return 1
        # Real-run path: sp is a SupplierPart, add it to the mapping.
        # Dry-run path: sp is None, skip — upsert_purchase_order short-
        # circuits before touching the mapping in all three paths.
        if sp is not None:
            sku_to_sp[line.sku] = sp

    try:
        report = upsert_purchase_order(
            api=api, order=order, supplier=supplier,
            sku_to_supplier_part=sku_to_sp,
            receive_location=receive_location,
            dry_run=dry_run,
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if reporter is not None:
        # In dry-run, fold the PO decision into the report under a stable
        # category name. Action prefix "DRY_RUN_" is stripped so the
        # printed report reads cleanly.
        action_clean = report.action.removeprefix("DRY_RUN_")
        action_kind = "CREATE" if action_clean in ("CREATE", "RECONCILE") else "REUSE"
        reporter.record(
            action_kind, "PurchaseOrder", order.reference,
            f"{action_clean} added={report.lines_added} "
            f"updated={report.lines_updated} deleted={report.lines_deleted}",
        )
    else:
        log.info(
            "%s PO %s — added=%d updated=%d deleted=%d",
            report.action, report.po_reference,
            report.lines_added, report.lines_updated, report.lines_deleted,
        )
    return 0
```

Now update `main()` to instantiate the reporter and print at the end. Find (around line 145):

```python
def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    _suppress_category_warning()
    log.info(
        "Importing supplier-order parts without KiCad context — "
        "categories will fall back to supplier-provided or 'Miscellaneous'.")

    api = InvenTreeAPI()
```

Insert a reporter line after the log.info:

```python
def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    _suppress_category_warning()
    log.info(
        "Importing supplier-order parts without KiCad context — "
        "categories will fall back to supplier-provided or 'Miscellaneous'.")

    reporter = DryRunReporter() if args.dry_run else None

    api = InvenTreeAPI()
```

Then find the two `_import_one_order` call sites in `main()` (around lines 175-195) and add `reporter=reporter`:

Find the first call:

```python
    if args.lcsc_csv:
        order = parse_lcsc_csv(Path(args.lcsc_csv))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
        )
```

Replace with:

```python
    if args.lcsc_csv:
        order = parse_lcsc_csv(Path(args.lcsc_csv))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
            reporter=reporter,
        )
```

Find the second call:

```python
    if args.mouser_xls:
        order = parse_mouser_xls(Path(args.mouser_xls))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
        )
```

Replace with:

```python
    if args.mouser_xls:
        order = parse_mouser_xls(Path(args.mouser_xls))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
            reporter=reporter,
        )
```

Finally, before the `return rc` at the bottom of `main()`, add the print_report call:

```python
    if reporter is not None:
        reporter.print_report(title="Supplier Order Import (dry run)")

    return rc
```

- [ ] **Step 4: Run new CLI tests + existing CLI tests**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_cli.py -v`

Expected: all tests pass (6 existing + 2 new = 8).

- [ ] **Step 5: Run the whole suite for regression check**

Run: `source .venv/bin/activate && pytest scripts/tests/`

Expected: 191 + 2 = 193 passed.

- [ ] **Step 6: Sanity-check `--help` still works**

Run: `source .venv/bin/activate && python3 scripts/import_supplier_order.py --help`

Expected: usage text prints cleanly, no import errors. `--dry-run` flag is listed.

- [ ] **Step 7: Commit**

```bash
git add scripts/import_supplier_order.py scripts/tests/test_order_import_cli.py
git commit -m "fix(import-orders): CLI threads DryRunReporter through resolution chain

main() instantiates DryRunReporter when --dry-run is set, threads it to
_import_one_order, which threads it into ensure_part_for_order_line as
a kwarg. Resolution decisions are recorded; PO upsert decisions are
folded into the report after upsert_purchase_order returns (which has
its own dry-run gate).

print_report() is called once at the end of main() for the whole import.

Real-run code path unchanged — reporter stays None, all calls behave as
before.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- ✅ Goal 1 (`ensure_part_for_order_line` respects --dry-run) → Task 1.
- ✅ Goal 2 (CREATE/REUSE records per line) → Task 1 steps 3.
- ✅ Goal 3 (CLI threads reporter + print_report) → Task 2.
- ✅ Goal 4 (real-run unchanged, default `reporter=None`) → Task 1 signature + Task 2 second test.
- ✅ Goal 5 (≥4 new resolution tests) → Task 1 has 5.
- ✅ Pfad A/B/C of `upsert_purchase_order` unchanged → no task needed; documented in spec as "already correct".
- ✅ Logging behavior (initial INFO stays, per-line INFO replaced by reporter) → Task 2 swaps the `log.info` with reporter.record in the PO branch.

**Placeholder scan:** No TBD/TODO/implement-later anywhere. All code blocks are concrete.

**Type consistency:**
- `reporter` is `Optional[DryRunReporter]` everywhere it appears (function signature in Task 1, kwarg in Task 2 call sites, instance variable in `main()` in Task 2).
- Return type `tuple[Optional[Part], Optional[SupplierPart]]` matches between Task 1's signature change and Task 2's `if sp is not None:` guard.
- `reporter.record(...)` arguments match `DryRunRecord(action, category, target, detail)` from `inventree_sync/dry_run.py`.

No gaps found.
