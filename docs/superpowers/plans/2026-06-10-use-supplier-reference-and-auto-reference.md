# Use `supplier_reference` + Server-Assigned `reference` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop crashing with HTTP 400 on the first real `import_supplier_order.py` run. Use InvenTree's `supplier_reference` field for the Mouser/LCSC order ID and let the server assign the pattern-compliant `reference` (`PO-XXXX`).

**Architecture:** Three focused changes in `scripts/inventree_sync/order_import.py`: (1) a new `_next_po_reference` helper that reads the server-suggested next reference from the OPTIONS endpoint, (2) `_find_po` rewritten to look up by `supplier_reference` with the established post-filter defensive pattern, (3) `upsert_purchase_order` Pfad A passes both `reference` (server) and `supplier_reference` (supplier-side) on POST. Dry-run path skips the OPTIONS probe and reports `"(server-assigned)"` as the planned reference.

**Tech Stack:** Python 3.13, `inventree==0.23.1`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-10-use-supplier-reference-and-auto-reference-design.md`

**Working directory for all commands:** `/home/pbuchegger/OE5XRX/HW-Module-CI`

---

## Conventions

- Tests use `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` at the top.
- Activate venv before pytest: `source .venv/bin/activate && pytest <path>`.
- Commit messages: `fix(import-orders): <subject>`.
- TDD strict: failing test → confirm fail → implement → confirm pass → commit.

---

## File Structure

| File | Change |
|---|---|
| `scripts/inventree_sync/order_import.py` | New `_next_po_reference` helper; `_find_po` rewritten; `upsert_purchase_order` Pfad A updated; `UpsertReport.po_reference` docstring updated |
| `scripts/tests/test_order_import_upsert.py` | 8 new tests; 3 existing tests adjusted to match the new lookup + create call signature |

---

## Task 1: `_next_po_reference` helper

**Files:**
- Modify: `scripts/inventree_sync/order_import.py` (add helper near `_find_po`)
- Test: `scripts/tests/test_order_import_upsert.py` (append tests)

- [ ] **Step 1: Write 3 failing tests**

Open `scripts/tests/test_order_import_upsert.py` and append after the existing tests (at the bottom of the file):

```python
# ---------------------------------------------------------------------------
# Server-side reference auto-assignment (PR fix/use-supplier-reference)
# ---------------------------------------------------------------------------

import pytest as _pytest_for_next_ref  # avoid shadow if pytest already imported


def test_next_po_reference_reads_default_from_options():
    """OPTIONS /api/order/po/ → actions.POST.reference.default is the next sequence value."""
    from inventree_sync.order_import import _next_po_reference

    api = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {
        "actions": {
            "POST": {
                "reference": {"default": "PO-0006"},
            },
        },
    }
    api.request.return_value = resp

    result = _next_po_reference(api)

    assert result == "PO-0006"
    # Verify the OPTIONS request shape — exact URL is whatever
    # PurchaseOrder.URL is (set by the inventree client).
    api.request.assert_called_once()
    args, kwargs = api.request.call_args
    assert kwargs.get("method") == "OPTIONS"


def test_next_po_reference_raises_when_default_missing():
    """Missing nested actions.POST.reference.default → RuntimeError with context."""
    from inventree_sync.order_import import _next_po_reference

    api = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"actions": {"POST": {}}}  # no reference key
    api.request.return_value = resp

    with _pytest_for_next_ref.raises(RuntimeError, match="reference"):
        _next_po_reference(api)


def test_next_po_reference_raises_when_options_request_fails():
    """Network/HTTP failure on OPTIONS → RuntimeError, not a silent fallback."""
    from inventree_sync.order_import import _next_po_reference

    api = MagicMock()
    api.request.side_effect = ConnectionError("server unreachable")

    with _pytest_for_next_ref.raises(RuntimeError, match="OPTIONS"):
        _next_po_reference(api)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v -k "next_po_reference"`
Expected: 3 fails with `ImportError: cannot import name '_next_po_reference'`.

- [ ] **Step 3: Implement the helper**

Open `scripts/inventree_sync/order_import.py`. Find the existing `_find_po` definition (around line 622). Insert the new helper *before* it:

```python
def _next_po_reference(api: InvenTreeAPI) -> str:
    """Read the next valid PurchaseOrder.reference from the server.

    InvenTree's OPTIONS response for /api/order/po/ includes a computed
    ``actions.POST.reference.default`` that is the next reference matching
    the server's configured pattern (e.g. ``"PO-0006"`` for the default
    ``PO-{ref:04d}`` pattern). This is the same mechanism the React UI
    uses to pre-fill the Create-PO form.

    Raises RuntimeError if the OPTIONS response is missing the field or
    the request fails — the importer can't reliably create POs without
    knowing the next valid reference, so we fail loud rather than guess.
    """
    try:
        resp = api.request(PurchaseOrder.URL, method="OPTIONS")
        body = resp.json()
        return body["actions"]["POST"]["reference"]["default"]
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read next PurchaseOrder.reference from "
            f"OPTIONS {PurchaseOrder.URL}: {exc}"
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v -k "next_po_reference"`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_upsert.py
git commit -m "fix(import-orders): _next_po_reference helper

Read InvenTree's next pattern-compliant PurchaseOrder.reference from
the OPTIONS endpoint's actions.POST.reference.default. Same mechanism
the React UI uses to pre-fill the Create-PO form. Fail-loud
RuntimeError on missing field or request failure — guessing the
sequence would leak into duplicate-reference HTTP 400s later in Pfad A.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Rewrite `_find_po` to use `supplier_reference`

**Files:**
- Modify: `scripts/inventree_sync/order_import.py:622-646` (replace `_find_po`)
- Test: `scripts/tests/test_order_import_upsert.py` (append tests)

The existing `_find_po(api, supplier_pk, reference)` filters on `reference`. After this task it becomes `_find_po(api, supplier_pk, supplier_reference)` and post-filters on `supplier_reference`. Caller `upsert_purchase_order` already passes `order.reference` (the supplier-side ID) — the parameter rename in `_find_po` is internal only.

- [ ] **Step 1: Write 2 failing tests**

Append to `scripts/tests/test_order_import_upsert.py`:

```python
def test_find_po_post_filters_by_supplier_reference():
    """Server-side supplier_reference= filter is ignored; we post-filter."""
    from inventree_sync.order_import import _find_po

    # Three POs returned (server ignored the filter)
    po_match = MagicMock()
    po_match.pk = 2
    po_match.supplier = 259
    po_match.supplier_reference = "275708282"
    po_other_supplier_ref = MagicMock()
    po_other_supplier_ref.pk = 3
    po_other_supplier_ref.supplier = 259
    po_other_supplier_ref.supplier_reference = "WM2504270070"
    po_other_supplier = MagicMock()
    po_other_supplier.pk = 1
    po_other_supplier.supplier = 258  # different supplier
    po_other_supplier.supplier_reference = "275708282"

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [
            po_match, po_other_supplier_ref, po_other_supplier,
        ]
        result = _find_po(MagicMock(), supplier_pk=259,
                          supplier_reference="275708282")

    assert result is po_match


def test_find_po_returns_none_when_no_match():
    """No PO has matching supplier_reference → None."""
    from inventree_sync.order_import import _find_po

    po1 = MagicMock(); po1.supplier = 259; po1.supplier_reference = "OTHER"
    po2 = MagicMock(); po2.supplier = 259; po2.supplier_reference = "SOMETHING-ELSE"

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [po1, po2]
        result = _find_po(MagicMock(), supplier_pk=259,
                          supplier_reference="275708282")

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v -k "find_po"`
Expected: 2 fails because the current `_find_po` filters on `reference`, not `supplier_reference`.

- [ ] **Step 3: Replace `_find_po`**

Open `scripts/inventree_sync/order_import.py`. Find and replace the existing `_find_po` (around lines 622-646) with:

```python
def _find_po(api: InvenTreeAPI, supplier_pk: int, supplier_reference: str):
    """Locate a PurchaseOrder by supplier + supplier_reference.

    The server-side ``?supplier_reference=`` filter is silently ignored
    (verified empirically against InvenTree 1.3.4 — all POs for the
    supplier come back regardless of the filter value), so we list all
    POs for the supplier and post-filter on supplier_reference. Same
    defensive pattern as ``find_part_by_name`` in ``client.py``.

    The supplier= filter is also post-filtered: some InvenTree versions
    serialize FKs as strings, so we coerce to int before comparing and
    skip the check when the value isn't numeric (test mocks treat the
    server-side filter as authoritative in that case).

    Returns the first match (or None). Multiple matches are not
    expected — supplier_reference is the operational identifier — but
    when they happen a ``logger.warning`` is emitted naming the
    supplier, supplier_reference, match count, and the picked PO's pk
    so the data drift surfaces immediately. (This warning was added
    during the final review round before the PR opened.)
    """
    matches = PurchaseOrder.list(api, supplier=supplier_pk)
    for po in matches:
        # Supplier post-filter (defensive, same as before).
        po_supplier = getattr(po, "supplier", None)
        if po_supplier is not None and not isinstance(po_supplier, bool):
            try:
                po_supplier_pk = int(po_supplier)
            except (TypeError, ValueError):
                po_supplier_pk = None
            if po_supplier_pk is not None and po_supplier_pk != supplier_pk:
                continue
        # supplier_reference post-filter (the actual identifier).
        po_supplier_ref = str(getattr(po, "supplier_reference", "") or "")
        if po_supplier_ref == supplier_reference:
            return po
    return None
```

- [ ] **Step 4: Run the 2 new tests + the existing upsert tests**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v`
Expected: the 2 new tests pass. **Some existing Pfad-B/C tests may now fail** because they pre-populated `po.reference="X"` on a mock and expected it to match — the new `_find_po` doesn't look at `reference` anymore. Skip ahead to Task 3 which updates those tests.

- [ ] **Step 5: Update existing tests that relied on the old `reference`-based lookup**

In `scripts/tests/test_order_import_upsert.py`, search for the helper `_po(...)` and any test that mocked `PO.list.return_value = [existing_po]`. For Pfad B/C tests, the existing PO mock needs `existing_po.supplier_reference` set to whatever the test's `SupplierOrder.reference` is — typically `"275708282"` per `_make_order()` or `"X"` per the simpler tests.

Concrete changes (look at the existing tests and add `existing_po.supplier_reference = ...`):

In `_po()` helper (around line 31-36), extend it:

```python
def _po(status=10, lines=None, supplier_reference="275708282"):
    po = MagicMock()
    po.pk = 999
    po.status = status
    po.supplier = 1
    po.supplier_reference = supplier_reference
    po.getLineItems.return_value = lines or []
    return po
```

Tests that use a non-`275708282` order reference (e.g. `_make_order()` with `reference="X"`) need to pass `supplier_reference="X"` to `_po(...)` — find each call and update. Specifically:

- `test_path_b_updates_qty_change`: order has `reference="X"`. Update `existing_po = _po(status=20, lines=[existing_li], supplier_reference="X")`.
- `test_path_b_deletes_extra_line`: same — `supplier_reference="X"`.
- All other Pfad B/C tests use `_make_order()` which has `reference="275708282"` — the new default in `_po()` covers them.

Also: the existing `test_path_a_creates_po_and_lines_and_receives` test (around line 47) will fail because Pfad A now calls `_next_po_reference(api)` which our test doesn't mock. Patch it:

```python
def test_path_a_creates_po_and_lines_and_receives():
    """No existing PO → create, add 2 lines, issue, receive."""
    order = _make_order()
    supplier = MagicMock(); supplier.pk = 1; supplier.name = "Mouser"
    receive_loc = MagicMock(); receive_loc.pk = 7
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    new_po = _po(status=10)
    new_po.addLineItem.return_value = MagicMock()

    with patch("inventree_sync.order_import.PurchaseOrder") as PO, \
         patch("inventree_sync.order_import._next_po_reference",
               return_value="PO-0006"):
        PO.list.return_value = []
        PO.create.return_value = new_po
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=supplier,
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=receive_loc,
        )

    PO.create.assert_called_once()
    create_kwargs = PO.create.call_args[0][1]
    assert create_kwargs["supplier"] == 1
    # reference is now server-assigned, not the supplier-side order ID
    assert create_kwargs["reference"] == "PO-0006"
    # supplier-side order ID lives in supplier_reference
    assert create_kwargs["supplier_reference"] == "275708282"

    assert new_po.addLineItem.call_count == 2
    new_po.issue.assert_called_once()
    new_po.receiveAll.assert_called_once_with(location=7, status=10)
    assert report.action == "CREATED"
    assert report.lines_added == 2
```

Similarly update `test_path_a_dedups_duplicate_sku_rows_last_wins` (around line 316) to patch `_next_po_reference`:

```python
def test_path_a_dedups_duplicate_sku_rows_last_wins():
    """Duplicate SKUs in the file must collapse to one PO LineItem."""
    order = SupplierOrder(
        supplier_name="Mouser", reference="275708282",
        order_date=None, currency="EUR",
        lines=[_line("A", 10, 1.0), _line("A", 99, 9.9), _line("B", 5, 2.0)],
    )
    supplier = MagicMock(); supplier.pk = 1; supplier.name = "Mouser"
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    new_po = _po(status=10)
    new_po.addLineItem.return_value = MagicMock()

    with patch("inventree_sync.order_import.PurchaseOrder") as PO, \
         patch("inventree_sync.order_import._next_po_reference",
               return_value="PO-0006"):
        PO.list.return_value = []
        PO.create.return_value = new_po
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=supplier,
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
        )

    assert new_po.addLineItem.call_count == 2
    calls = new_po.addLineItem.call_args_list
    skus_added = {c.kwargs["reference"] for c in calls}
    assert skus_added == {"A", "B"}
    a_call = next(c for c in calls if c.kwargs["reference"] == "A")
    assert a_call.kwargs["quantity"] == 99
    assert a_call.kwargs["purchase_price"] == 9.9
    assert report.lines_added == 2
```

- [ ] **Step 6: Run full upsert test file to verify pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v`
Expected: all tests pass (existing 11 + 2 new `find_po` + 3 from Task 1 = 16).

- [ ] **Step 7: Run the whole suite for regression check**

Run: `source .venv/bin/activate && pytest scripts/tests/`
Expected: 201 + 2 from Task 2 + 3 from Task 1 + adjustments = 206 passed. (Existing 201 pre-fix, then Task 1 added 3 = 204, Task 2 added 2 + adjusted existing = 206.)

If anything outside `test_order_import_upsert.py` failed, investigate: likely a stray test still pre-populating `po.reference` and relying on the old lookup.

- [ ] **Step 8: Commit**

```bash
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_upsert.py
git commit -m "fix(import-orders): look up POs by supplier_reference

PurchaseOrder.reference is server-assigned (sequence number like
PO-0001); supplier_reference is the operational identifier holding
the Mouser/LCSC order ID. _find_po now post-filters on
supplier_reference (server-side filter empirically ignored — same
defensive pattern as find_part_by_name).

Updated existing Pfad-B/C test fixtures to set
existing_po.supplier_reference and patch _next_po_reference for the
Pfad-A create tests. Two new tests cover the post-filter logic.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: `upsert_purchase_order` Pfad A — server-assigned reference + supplier_reference

**Files:**
- Modify: `scripts/inventree_sync/order_import.py` (Pfad A branch in `upsert_purchase_order`)
- Modify: `scripts/inventree_sync/order_import.py` (`UpsertReport` docstring)
- Test: `scripts/tests/test_order_import_upsert.py` (append tests)

- [ ] **Step 1: Write 3 failing tests**

Append to `scripts/tests/test_order_import_upsert.py`:

```python
def test_upsert_path_a_uses_server_assigned_reference():
    """Pfad A POSTs the server-suggested reference and the supplier-side
    order ID as supplier_reference."""
    order = _make_order()  # reference="275708282"
    supplier = MagicMock(); supplier.pk = 1; supplier.name = "Mouser"
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    new_po = _po(status=10)
    new_po.addLineItem.return_value = MagicMock()

    with patch("inventree_sync.order_import.PurchaseOrder") as PO, \
         patch("inventree_sync.order_import._next_po_reference",
               return_value="PO-0006") as next_ref:
        PO.list.return_value = []
        PO.create.return_value = new_po
        upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=supplier,
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
        )

    next_ref.assert_called_once()
    create_payload = PO.create.call_args[0][1]
    assert create_payload["reference"] == "PO-0006"
    assert create_payload["supplier_reference"] == "275708282"
    assert "Imported from Mouser order 275708282" in create_payload["description"]


def test_upsert_path_a_dry_run_does_not_probe_options():
    """Dry-run Pfad A must not call _next_po_reference (no read-only or
    not, the reported reference is "(server-assigned)" instead)."""
    order = _make_order()

    with patch("inventree_sync.order_import.PurchaseOrder") as PO, \
         patch("inventree_sync.order_import._next_po_reference") as next_ref:
        PO.list.return_value = []
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={
                "A": _supplier_part(pk=101, sku="A"),
                "B": _supplier_part(pk=102, sku="B"),
            },
            receive_location=MagicMock(pk=7),
            dry_run=True,
        )

    PO.create.assert_not_called()
    next_ref.assert_not_called()
    assert report.action == "DRY_RUN_CREATE"
    assert report.po_reference == "(server-assigned)"
    assert report.lines_added == 2


def test_upsert_path_b_finds_existing_via_supplier_reference():
    """Pfad B reconcile is exercised when an existing PO matches by
    supplier_reference, even though its server-assigned reference is
    something like PO-0001."""
    order = _make_order()  # supplier-side ref="275708282"
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")

    # Existing PO with server-side reference "PO-0001", supplier-side
    # matches our order
    li_a = MagicMock(); li_a.pk = 1; li_a.reference = "A"
    li_a.quantity = 10; li_a.purchase_price = 1.0; li_a.part = 101
    li_b = MagicMock(); li_b.pk = 2; li_b.reference = "B"
    li_b.quantity = 5; li_b.purchase_price = 2.0; li_b.part = 102

    existing_po = _po(status=20, lines=[li_a, li_b],
                      supplier_reference="275708282")
    existing_po.reference = "PO-0001"  # server-side identifier, different

    with patch("inventree_sync.order_import.PurchaseOrder") as PO, \
         patch("inventree_sync.order_import._next_po_reference") as next_ref:
        PO.list.return_value = [existing_po]
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
        )

    # Pfad A wasn't entered → no OPTIONS probe + no create
    next_ref.assert_not_called()
    PO.create.assert_not_called()
    existing_po.addLineItem.assert_not_called()  # in-sync diff
    existing_po.receiveAll.assert_called_once()
    assert report.action == "RECONCILED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v -k "supplier_reference or server_assigned or path_a_dry_run_does_not_probe"`
Expected: 3 fails — `assert create_payload["supplier_reference"] == "275708282"` fails (the field isn't set yet), `assert report.po_reference == "(server-assigned)"` fails (dry-run path still returns `order.reference`), and `next_ref.assert_called_once()` fails because Pfad A still uses `order.reference` directly.

- [ ] **Step 3: Update `upsert_purchase_order` Pfad A**

In `scripts/inventree_sync/order_import.py`, find the Pfad A branch (around lines 689-715). The current shape:

```python
    if existing is None:
        # Pfad A
        if dry_run:
            return UpsertReport(
                action="DRY_RUN_CREATE", po_reference=order.reference,
                lines_added=len(deduped_lines),
            )
        po = PurchaseOrder.create(api, {
            "supplier": supplier.pk,
            "reference": order.reference,
            "description": f"Imported from {order.supplier_name} order {order.reference}",
            **({"target_date": order.order_date} if order.order_date else {}),
        })
        for line in deduped_lines:
            ...
        po.issue()
        po.receiveAll(location=receive_location.pk, status=_STOCK_STATUS_OK)
        return UpsertReport(
            action="CREATED", po_reference=order.reference,
            lines_added=len(deduped_lines),
        )
```

Replace with:

```python
    if existing is None:
        # Pfad A
        if dry_run:
            # Dry-run: skip the OPTIONS probe — the reference value
            # would be reported but mutates between runs (each real POST
            # advances the sequence). "(server-assigned)" is honest about
            # what the operator will see.
            return UpsertReport(
                action="DRY_RUN_CREATE", po_reference="(server-assigned)",
                lines_added=len(deduped_lines),
            )
        next_ref = _next_po_reference(api)
        po = PurchaseOrder.create(api, {
            "supplier": supplier.pk,
            "reference": next_ref,
            "supplier_reference": order.reference,
            "description": f"Imported from {order.supplier_name} order {order.reference}",
            **({"target_date": order.order_date} if order.order_date else {}),
        })
        for line in deduped_lines:
            sp = sku_to_supplier_part[line.sku]
            po.addLineItem(
                part=sp.pk,
                quantity=line.qty,
                purchase_price=line.unit_price,
                purchase_price_currency=line.currency,
                reference=line.sku,
            )
        po.issue()
        po.receiveAll(location=receive_location.pk, status=_STOCK_STATUS_OK)
        return UpsertReport(
            action="CREATED", po_reference=next_ref,
            lines_added=len(deduped_lines),
        )
```

Now update the `UpsertReport` docstring (around lines 612-619) — add the `po_reference` semantic note:

```python
@dataclass
class UpsertReport:
    """Result of upsert_purchase_order — used by the CLI for summary print.

    *po_reference* holds the InvenTree PurchaseOrder.reference — i.e. the
    server-assigned sequence value (e.g. ``"PO-0006"``), not the supplier-
    side order ID (which lives in ``PurchaseOrder.supplier_reference``).
    For dry-run Pfad A this is ``"(server-assigned)"`` because we skip
    the OPTIONS probe in dry-run mode (the value mutates between runs).
    """
    action: str               # CREATED | RECONCILED | IN_SYNC | DRY_RUN_*
    po_reference: str
    lines_added: int = 0
    lines_updated: int = 0
    lines_deleted: int = 0
```

- [ ] **Step 4: Run the 3 new tests**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v -k "supplier_reference or server_assigned or path_a_dry_run_does_not_probe"`
Expected: all 3 pass.

- [ ] **Step 5: Run the whole upsert file + full suite**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v`
Expected: all 19 tests pass (11 existing + 3 from Task 1 + 2 from Task 2 + 3 from Task 3).

Then: `source .venv/bin/activate && pytest scripts/tests/`
Expected: 209 passed. (201 starting + 8 from this PR's tasks; existing-test adjustments don't change the total count.)

- [ ] **Step 6: Check that the CLI tests still work**

The CLI tests `test_order_import_cli.py` patch `import_supplier_order.upsert_purchase_order` directly and don't exercise `_next_po_reference`, so they should be unaffected. Confirm:
Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_cli.py -v`
Expected: 14 passed (unchanged).

- [ ] **Step 7: Sanity-check `--help` still works**

Run: `source .venv/bin/activate && python3 scripts/import_supplier_order.py --help`
Expected: usage text prints, no import errors.

- [ ] **Step 8: Commit**

```bash
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_upsert.py
git commit -m "fix(import-orders): Pfad A uses server-assigned reference

upsert_purchase_order's Pfad A now:
- Reads the next valid reference via _next_po_reference (OPTIONS)
- POSTs it as PurchaseOrder.reference (sequence number, e.g. PO-0006)
- Sets supplier_reference to the Mouser/LCSC order ID (the operational
  identifier the operator types into the UI)
- Reports the server-assigned value in UpsertReport.po_reference

Dry-run Pfad A skips the OPTIONS probe and reports
po_reference=\"(server-assigned)\" — the actual value mutates between
runs as each real POST advances the sequence.

Fixes the HTTP 400 ('Reference must match required pattern:
PO-{ref:04d}') crash from the first real-run attempt against
parts.oe5xrx.org.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- ✅ Goal 1 (lookup by supplier_reference) — Task 2.
- ✅ Goal 2 (server-assigned reference via OPTIONS) — Task 1 helper + Task 3 Pfad A.
- ✅ Goal 3 (recognize 3 manually-created POs) — Task 2's `_find_po` rewrite catches them via supplier_reference post-filter.
- ✅ Goal 4 (existing tests stay functional) — Task 2 Step 5 + Task 3 Step 4 update the fixtures.
- ✅ Goal 5 (`SupplierOrder.reference` semantics unchanged) — no dataclass change.
- ✅ Pfad A creates with both `reference` and `supplier_reference` — Task 3 Step 3.
- ✅ Dry-run doesn't probe OPTIONS — Task 3 Step 3 + test in Step 1.
- ✅ Error handling: RuntimeError on OPTIONS failure — Task 1 Step 3.

**Placeholder scan:** none.

**Type consistency:**
- `_next_po_reference(api: InvenTreeAPI) -> str` — used in Task 3 as `next_ref = _next_po_reference(api)`, treated as str throughout.
- `_find_po(api, supplier_pk, supplier_reference: str)` — caller `upsert_purchase_order` passes `order.reference` (the supplier-side ID, a str).
- `UpsertReport.po_reference: str` — assigned `"PO-0006"` (real run) or `"(server-assigned)"` (dry run) or the existing PO's reference (Pfad B/C — unchanged).
- Test fixture `_po(supplier_reference="X")` — added default kwarg, all call sites either use the default or pass explicit.

No gaps found.
