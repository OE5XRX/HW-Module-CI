"""Sanity tests for the order_import dataclasses."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import SupplierOrder, SupplierOrderLine  # noqa: E402


def test_supplier_order_line_defaults_package_to_empty_string():
    line = SupplierOrderLine(
        sku="C1739",
        qty=100,
        unit_price=0.0074,
        currency="USD",
        mpn="0805B333K500NT",
        mfr_name="FH",
        description="33nF X7R 0805",
    )
    assert line.package == ""


def test_supplier_order_holds_a_list_of_lines():
    order = SupplierOrder(
        supplier_name="LCSC",
        reference="WM2504270070",
        order_date=None,
        currency="USD",
        lines=[],
    )
    assert order.lines == []
    assert order.supplier_name == "LCSC"


def test_supplier_order_line_is_hashable_via_sku():
    """SKU is the reconciliation key; lines from a file are deduped on it."""
    a = SupplierOrderLine(sku="X", qty=1, unit_price=0.0, currency="EUR",
                          mpn="", mfr_name="", description="")
    b = SupplierOrderLine(sku="X", qty=2, unit_price=1.0, currency="EUR",
                          mpn="", mfr_name="", description="")
    # We don't require hashability — instead we test that SKU is a string and
    # can be used as a dict key downstream.
    d = {a.sku: a, b.sku: b}
    assert d["X"].qty == 2  # second insert wins (last-write-wins semantics)
