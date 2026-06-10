"""Unit tests for the Mouser-XLS parser (pure transformation layer)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import (  # noqa: E402
    _parse_mouser_price,
    _parse_mouser_date,
    _rows_to_mouser_order,
)


def test_parse_mouser_price_european_format():
    assert _parse_mouser_price("€ 0,381") == 0.381
    assert _parse_mouser_price("€ 0,02") == 0.02
    assert _parse_mouser_price("€ 1,16") == 1.16
    assert _parse_mouser_price("€ 1.234,56") == 1234.56  # thousands sep


def test_parse_mouser_price_us_format():
    assert _parse_mouser_price("$ 1.23") == 1.23
    assert _parse_mouser_price("0.0074") == 0.0074


def test_parse_mouser_price_unambiguous_thousands_groups():
    """Multi-group separators (>=2 thousand-groups) are unambiguously thousands.

    Distinguishes from single-group forms like "0,381" where comma is
    decimal under the European-format default — see _parse_mouser_price
    docstring for the rationale.
    """
    assert _parse_mouser_price("1,234,567") == 1234567.0
    assert _parse_mouser_price("1.234.567") == 1234567.0
    assert _parse_mouser_price("€ 12,345,678") == 12345678.0
    # Sanity: single-group form keeps the existing European-decimal interpretation
    assert _parse_mouser_price("0,381") == 0.381


def test_parse_mouser_price_empty_returns_zero():
    assert _parse_mouser_price("") == 0.0
    assert _parse_mouser_price(None) == 0.0


def test_parse_mouser_date_to_iso():
    # XLS file uses "07-Jul-25" — 2-digit year, English month abbreviation.
    assert _parse_mouser_date("07-Jul-25") == "2025-07-07"
    assert _parse_mouser_date("31-Dec-99") == "2099-12-31"


def test_parse_mouser_date_invalid_returns_none():
    assert _parse_mouser_date("") is None
    assert _parse_mouser_date("not a date") is None


def _row(**overrides):
    base = {
        "Sales Order No:": "275708282",
        "Order Date:": "07-Jul-25",
        "Mouser No:": "576-0297003.L",
        "Mfr. No:": "0297003.L",
        "Desc.:": "Automotive Fuses 32V 3A MINI",
        "Order Qty.": 10,
        "Price (EUR)": "€ 0,381",
    }
    base.update(overrides)
    return base


def test_rows_to_mouser_order_basic_header():
    rows = [_row(), _row(**{
        "Mouser No:": "667-ERA-6AEB221V",
        "Mfr. No:": "ERA-6AEB221V",
        "Desc.:": "Thin Film Resistors 0805 220ohm",
        "Order Qty.": 20,
        "Price (EUR)": "€ 0,052",
    })]
    order = _rows_to_mouser_order(rows)
    assert order.supplier_name == "Mouser"
    assert order.reference == "275708282"
    assert order.order_date == "2025-07-07"
    assert order.currency == "EUR"
    assert len(order.lines) == 2


def test_rows_to_mouser_order_line_fields():
    rows = [_row()]
    order = _rows_to_mouser_order(rows)
    line = order.lines[0]
    assert line.sku == "576-0297003.L"
    assert line.mpn == "0297003.L"
    assert line.mfr_name == ""  # not in file, filled later by API
    assert line.qty == 10
    assert line.unit_price == 0.381
    assert line.currency == "EUR"
    assert line.package == ""
    assert "Automotive Fuses" in line.description


def test_rows_to_mouser_order_skips_empty_rows():
    rows = [_row(), _row(**{"Mouser No:": "", "Mfr. No:": ""})]  # second is blank
    order = _rows_to_mouser_order(rows)
    assert len(order.lines) == 1


def test_rows_to_mouser_order_qty_as_string_coerces():
    rows = [_row(**{"Order Qty.": "15"})]
    order = _rows_to_mouser_order(rows)
    assert order.lines[0].qty == 15


def test_rows_to_mouser_order_empty_input_uses_unknown_reference():
    order = _rows_to_mouser_order([])
    assert order.reference == "mouser-unknown"
    assert order.order_date is None
    assert order.lines == []
