"""Mock-based tests for upsert_purchase_order (Pfad A/B/C)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import (  # noqa: E402
    SupplierOrder,
    SupplierOrderLine,
    upsert_purchase_order,
)


def _line(sku, qty, price):
    return SupplierOrderLine(
        sku=sku, qty=qty, unit_price=price, currency="EUR",
        mpn=sku, mfr_name="x", description="",
    )


def _supplier_part(pk, sku):
    sp = MagicMock(); sp.pk = pk; sp.SKU = sku
    return sp


def _po(status=10, lines=None, supplier_reference="275708282"):
    po = MagicMock()
    po.pk = 999
    po.status = status
    po.supplier = 1
    po.supplier_reference = supplier_reference
    po.getLineItems.return_value = lines or []
    return po


def _make_order():
    return SupplierOrder(
        supplier_name="Mouser", reference="275708282",
        order_date="2025-07-07", currency="EUR",
        lines=[_line("A", 10, 1.0), _line("B", 5, 2.0)],
    )


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


def test_path_b_adds_missing_line():
    """PO exists PLACED with 1 line; file has 2 → add the missing one."""
    order = _make_order()
    supplier = MagicMock(); supplier.pk = 1; supplier.name = "Mouser"
    receive_loc = MagicMock(); receive_loc.pk = 7
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")

    existing_li = MagicMock()
    existing_li.pk = 500
    existing_li.reference = "A"
    existing_li.quantity = 10
    existing_li.purchase_price = 1.0
    existing_li.part = 101

    existing_po = _po(status=20, lines=[existing_li])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=supplier,
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=receive_loc,
        )

    existing_po.addLineItem.assert_called_once()
    add_kwargs = existing_po.addLineItem.call_args.kwargs
    assert add_kwargs["part"] == 102  # supplier_part pk for B
    assert add_kwargs["quantity"] == 5
    assert add_kwargs["reference"] == "B"
    existing_po.receiveAll.assert_called_once()
    assert report.action == "RECONCILED"
    assert report.lines_added == 1


def test_path_b_updates_qty_change():
    """PO exists PLACED, file has different qty → save() called."""
    order = SupplierOrder(
        supplier_name="Mouser", reference="X", order_date=None,
        currency="EUR", lines=[_line("A", 15, 1.0)],
    )
    sp_a = _supplier_part(pk=101, sku="A")
    existing_li = MagicMock()
    existing_li.pk = 500; existing_li.reference = "A"
    existing_li.quantity = 10; existing_li.purchase_price = 1.0
    existing_li.part = 101

    existing_po = _po(status=20, lines=[existing_li], supplier_reference="X")

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a},
            receive_location=MagicMock(pk=7),
        )

    existing_li.save.assert_called_once()
    save_data = existing_li.save.call_args[0][0]
    assert save_data["quantity"] == 15


def test_path_b_deletes_extra_line():
    """PO has line C that file no longer has → delete it."""
    order = SupplierOrder(
        supplier_name="Mouser", reference="X", order_date=None,
        currency="EUR", lines=[_line("A", 10, 1.0)],
    )
    sp_a = _supplier_part(pk=101, sku="A")
    li_a = MagicMock()
    li_a.pk = 1; li_a.reference = "A"; li_a.quantity = 10
    li_a.purchase_price = 1.0; li_a.part = 101; li_a.received = 0
    li_c = MagicMock()
    li_c.pk = 2; li_c.reference = "C"; li_c.quantity = 3
    li_c.purchase_price = 0.5; li_c.part = 103; li_c.received = 0

    existing_po = _po(status=20, lines=[li_a, li_c], supplier_reference="X")

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a},
            receive_location=MagicMock(pk=7),
        )

    li_c.delete.assert_called_once()
    li_a.delete.assert_not_called()


def test_path_b_refuses_to_delete_partially_received_line():
    """PO has line C with received stock; file no longer lists C → fail loud."""
    order = SupplierOrder(
        supplier_name="Mouser", reference="X", order_date=None,
        currency="EUR", lines=[_line("A", 10, 1.0)],
    )
    sp_a = _supplier_part(pk=101, sku="A")
    li_a = MagicMock()
    li_a.pk = 1; li_a.reference = "A"; li_a.quantity = 10
    li_a.purchase_price = 1.0; li_a.part = 101; li_a.received = 0
    li_c = MagicMock()
    li_c.pk = 2; li_c.reference = "C"; li_c.quantity = 3
    li_c.purchase_price = 0.5; li_c.part = 103; li_c.received = 2  # partial

    existing_po = _po(status=20, lines=[li_a, li_c], supplier_reference="X")

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        with pytest.raises(RuntimeError) as exc:
            upsert_purchase_order(
                api=MagicMock(),
                order=order,
                supplier=MagicMock(pk=1, name="Mouser"),
                sku_to_supplier_part={"A": sp_a},
                receive_location=MagicMock(pk=7),
            )

    msg = str(exc.value)
    assert "C" in msg
    assert "received=2" in msg
    li_c.delete.assert_not_called()
    existing_po.receiveAll.assert_not_called()


def test_path_b_no_op_when_in_sync():
    """PENDING/PLACED PO with identical lines → no mutation, just receive."""
    order = _make_order()
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    li_a = MagicMock(); li_a.pk = 1; li_a.reference = "A"
    li_a.quantity = 10; li_a.purchase_price = 1.0; li_a.part = 101
    li_b = MagicMock(); li_b.pk = 2; li_b.reference = "B"
    li_b.quantity = 5; li_b.purchase_price = 2.0; li_b.part = 102

    existing_po = _po(status=10, lines=[li_a, li_b])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
        )

    existing_po.addLineItem.assert_not_called()
    li_a.save.assert_not_called(); li_b.save.assert_not_called()
    li_a.delete.assert_not_called(); li_b.delete.assert_not_called()
    existing_po.receiveAll.assert_called_once()


def test_path_c_in_sync_logs_and_exits_clean():
    """PO COMPLETE matching file → no-op, no exception."""
    order = _make_order()
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    li_a = MagicMock(); li_a.pk = 1; li_a.reference = "A"
    li_a.quantity = 10; li_a.purchase_price = 1.0; li_a.part = 101
    li_b = MagicMock(); li_b.pk = 2; li_b.reference = "B"
    li_b.quantity = 5; li_b.purchase_price = 2.0; li_b.part = 102

    existing_po = _po(status=30, lines=[li_a, li_b])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
        )

    existing_po.addLineItem.assert_not_called()
    existing_po.receiveAll.assert_not_called()
    assert report.action == "IN_SYNC"


def test_path_c_drift_raises_runtime_error():
    """PO COMPLETE diverging from file → RuntimeError, no writes."""
    order = _make_order()  # file has A qty=10, B qty=5
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    li_a = MagicMock(); li_a.pk = 1; li_a.reference = "A"
    li_a.quantity = 7  # qty drift
    li_a.purchase_price = 1.0; li_a.part = 101

    existing_po = _po(status=30, lines=[li_a])  # missing B too

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        with pytest.raises(RuntimeError) as exc:
            upsert_purchase_order(
                api=MagicMock(),
                order=order,
                supplier=MagicMock(pk=1, name="Mouser"),
                sku_to_supplier_part={"A": sp_a, "B": sp_b},
                receive_location=MagicMock(pk=7),
            )

    msg = str(exc.value)
    assert "275708282" in msg
    assert "ADD" in msg or "UPDATE" in msg
    existing_po.addLineItem.assert_not_called()
    li_a.save.assert_not_called()


def test_dry_run_paths_no_writes():
    """dry_run=True must skip every InvenTree mutation."""
    order = _make_order()
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = []
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
            dry_run=True,
        )

    PO.create.assert_not_called()
    assert report.action == "DRY_RUN_CREATE"
    assert report.lines_added == 2


def test_path_a_dedups_duplicate_sku_rows_last_wins():
    """Duplicate SKUs in the file must collapse to one PO LineItem.

    Otherwise compute_po_line_diff would silently ignore the surplus
    items on a later reconciliation run, leaving them un-reconcilable
    (Copilot Round 3 finding).
    """
    order = SupplierOrder(
        supplier_name="Mouser", reference="275708282",
        order_date=None, currency="EUR",
        # Same SKU "A" twice with conflicting qty/price: last wins.
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

    # Exactly two addLineItem calls — one per unique SKU
    assert new_po.addLineItem.call_count == 2
    calls = new_po.addLineItem.call_args_list
    skus_added = {c.kwargs["reference"] for c in calls}
    assert skus_added == {"A", "B"}
    # Last-wins: A is added with qty=99 / price=9.9, not the first row's 10/1.0
    a_call = next(c for c in calls if c.kwargs["reference"] == "A")
    assert a_call.kwargs["quantity"] == 99
    assert a_call.kwargs["purchase_price"] == 9.9
    assert report.lines_added == 2


def test_path_a_dry_run_dedup_reports_unique_count():
    """Dry-run report's lines_added must reflect the deduped count, not raw rows."""
    order = SupplierOrder(
        supplier_name="Mouser", reference="X",
        order_date=None, currency="EUR",
        lines=[_line("A", 1, 0.1), _line("A", 2, 0.2)],  # duplicate SKU
    )
    sp_a = _supplier_part(pk=101, sku="A")

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = []
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a},
            receive_location=MagicMock(pk=7),
            dry_run=True,
        )

    assert report.action == "DRY_RUN_CREATE"
    assert report.lines_added == 1  # deduped, not 2


# ---------------------------------------------------------------------------
# Server-side reference auto-assignment (PR fix/use-supplier-reference)
# ---------------------------------------------------------------------------

def test_next_po_reference_reads_default_from_options():
    """OPTIONS /api/order/po/ → actions.POST.reference.default is the next sequence value."""
    from inventree_sync.order_import import _next_po_reference

    api = MagicMock()
    api.base_url = "http://test.example/"
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
    api.request.assert_called_once()
    args, kwargs = api.request.call_args
    assert kwargs.get("method") == "OPTIONS"


def test_next_po_reference_raises_when_default_missing():
    """Missing nested actions.POST.reference.default → RuntimeError with context."""
    from inventree_sync.order_import import _next_po_reference

    api = MagicMock()
    api.base_url = "http://test.example/"
    resp = MagicMock()
    resp.json.return_value = {"actions": {"POST": {}}}
    api.request.return_value = resp

    with pytest.raises(RuntimeError, match=r"actions\.POST\.reference\.default"):
        _next_po_reference(api)


def test_next_po_reference_raises_when_options_request_fails():
    """Network/HTTP failure on OPTIONS → RuntimeError, not a silent fallback."""
    from inventree_sync.order_import import _next_po_reference

    api = MagicMock()
    api.base_url = "http://test.example/"
    api.request.side_effect = ConnectionError("server unreachable")

    with pytest.raises(RuntimeError) as exc_info:
        _next_po_reference(api)
    # Host + relative path both present so operators chasing alerts can
    # tell which InvenTree instance failed.
    assert "http://test.example/order/po/" in str(exc_info.value)
    assert "Failed to read next PurchaseOrder.reference" in str(exc_info.value)


def test_find_po_post_filters_by_supplier_reference():
    """Server-side supplier_reference= AND supplier= filters are both ignored; we post-filter both."""
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


# ---------------------------------------------------------------------------
# Task 3: Pfad A server-assigned reference + supplier_reference
# ---------------------------------------------------------------------------

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
    """Dry-run Pfad A must not call _next_po_reference; reports "(server-assigned)"."""
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
    existing_po.addLineItem.assert_not_called()
    existing_po.receiveAll.assert_called_once()
    assert report.action == "RECONCILED"
    assert report.po_reference == "PO-0001"
