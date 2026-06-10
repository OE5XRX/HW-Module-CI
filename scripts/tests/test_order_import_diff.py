"""Pure-function tests for compute_po_line_diff."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import (  # noqa: E402
    SupplierOrderLine,
    compute_po_line_diff,
    LineItemAction,
)


def _line(sku, qty, price):
    return SupplierOrderLine(
        sku=sku, qty=qty, unit_price=price, currency="EUR",
        mpn=sku, mfr_name="X", description="",
    )


def _po_line(sku, qty, price, pk):
    """Mock a PurchaseOrderLineItem. Reference holds the SKU."""
    li = MagicMock()
    li.pk = pk
    li.reference = sku
    li.quantity = qty
    li.purchase_price = price
    return li


def test_no_diff_when_identical():
    file_lines = [_line("A", 10, 1.0), _line("B", 5, 2.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1), _po_line("B", 5, 2.0, pk=2)]
    sku_to_sp = {"A": 100, "B": 101}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert diff.to_add == []
    assert diff.to_update == []
    assert diff.to_delete == []
    assert diff.is_empty is True


def test_add_when_file_has_extra_line():
    file_lines = [_line("A", 10, 1.0), _line("B", 5, 2.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1)]
    sku_to_sp = {"A": 100, "B": 101}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_add) == 1
    assert diff.to_add[0].sku == "B"
    assert diff.to_delete == []
    assert diff.to_update == []
    assert diff.is_empty is False


def test_delete_when_po_has_extra_line():
    file_lines = [_line("A", 10, 1.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1), _po_line("B", 5, 2.0, pk=2)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_delete) == 1
    assert diff.to_delete[0].pk == 2


def test_update_when_qty_differs():
    file_lines = [_line("A", 15, 1.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_update) == 1
    upd: LineItemAction = diff.to_update[0]
    assert upd.line_item.pk == 1
    assert upd.new_quantity == 15
    assert upd.new_price == 1.0


def test_update_when_price_differs():
    file_lines = [_line("A", 10, 1.5)]
    po_lines = [_po_line("A", 10, 1.0, pk=1)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_update) == 1
    assert diff.to_update[0].new_price == 1.5


def test_price_compared_with_epsilon():
    """Floating-point near-equality must not trigger a spurious update."""
    file_lines = [_line("A", 10, 0.1 + 0.2)]  # 0.30000000000000004
    po_lines = [_po_line("A", 10, 0.3, pk=1)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert diff.to_update == []


def test_indexes_po_by_part_when_reference_missing():
    """Older POs created without `reference` need fallback indexing."""
    file_lines = [_line("A", 10, 1.0)]
    po = MagicMock()
    po.pk = 99
    po.reference = ""    # no SKU here
    po.part = 100        # SupplierPart pk
    po.quantity = 10
    po.purchase_price = 1.0
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, [po], sku_to_sp)
    assert diff.to_update == [] and diff.to_add == [] and diff.to_delete == []


def test_human_readable_report():
    file_lines = [_line("A", 15, 1.5), _line("B", 5, 2.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1), _po_line("C", 3, 0.5, pk=3)]
    sku_to_sp = {"A": 100, "B": 101}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    report = diff.format_report()
    assert "ADD" in report and "B" in report
    assert "REMOVE" in report and "C" in report
    assert "UPDATE" in report and "A" in report
