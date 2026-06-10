"""Unit tests for parse_lcsc_csv."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import parse_lcsc_csv  # noqa: E402


_FIXTURE_CSV = (
    "LCSC Part Number,Manufacture Part Number,Manufacturer,Customer NO.,"
    "Package,Description,RoHS,Quantity,Unit Price($),Ext.Price($),"
    "Estimated lead time (business days),Updated lead time,Date Code / Lot No.\n"
    "C1739,0805B333K500NT,FH (Guangdong Fenghua Advanced Tech),,0805,"
    "33nF +-10% 50V Ceramic Capacitor X7R 0805,YES,100,0.0074,0.74,,,\n"
    "C17513,0805W8F1001T5E,UNI-ROYAL(Uniroyal Elec),,0805,"
    "1kOhm +-1% 125mW 0805 Thick Film Resistor,YES,100,0.0017,0.17,,,\n"
)


def _write_fixture(tmp_path: Path, basename: str, content: str) -> Path:
    f = tmp_path / basename
    f.write_text(content, encoding="utf-8")
    return f


def test_parse_lcsc_derives_reference_from_filename(tmp_path):
    f = _write_fixture(tmp_path, "LCSC__WM2504270070_20260610043835.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert order.reference == "WM2504270070"


def test_parse_lcsc_sets_supplier_name_and_currency(tmp_path):
    f = _write_fixture(tmp_path, "LCSC__WM2504270070_20260610043835.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert order.supplier_name == "LCSC"
    assert order.currency == "USD"
    assert order.order_date is None


def test_parse_lcsc_two_lines_with_full_fields(tmp_path):
    f = _write_fixture(tmp_path, "LCSC__WM2504270070_20260610043835.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert len(order.lines) == 2

    first = order.lines[0]
    assert first.sku == "C1739"
    assert first.mpn == "0805B333K500NT"
    assert first.mfr_name == "FH (Guangdong Fenghua Advanced Tech)"
    assert first.qty == 100
    assert first.unit_price == 0.0074
    assert first.currency == "USD"
    assert first.package == "0805"
    assert "Ceramic Capacitor" in first.description

    second = order.lines[1]
    assert second.sku == "C17513"
    assert second.qty == 100
    assert second.unit_price == 0.0017


def test_parse_lcsc_falls_back_when_filename_does_not_match(tmp_path):
    f = _write_fixture(tmp_path, "random.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert order.reference == "lcsc-unknown"


def test_parse_lcsc_skips_rows_with_empty_sku(tmp_path):
    csv_with_blank = _FIXTURE_CSV + ",,,,,,,,,,,,\n"  # trailing empty data row
    f = _write_fixture(tmp_path, "LCSC__X_1.csv", csv_with_blank)
    order = parse_lcsc_csv(f)
    assert len(order.lines) == 2  # blank row dropped


def test_parse_lcsc_strips_whitespace_in_sku_and_mpn(tmp_path):
    padded = _FIXTURE_CSV.replace("C1739,0805B333K500NT", "  C1739  ,  0805B333K500NT  ")
    f = _write_fixture(tmp_path, "LCSC__X_1.csv", padded)
    order = parse_lcsc_csv(f)
    assert order.lines[0].sku == "C1739"
    assert order.lines[0].mpn == "0805B333K500NT"


def test_parse_lcsc_invalid_quantity_raises_value_error(tmp_path):
    bad = _FIXTURE_CSV.replace(",YES,100,0.0074", ",YES,not-a-number,0.0074")
    f = _write_fixture(tmp_path, "LCSC__X_1.csv", bad)
    import pytest
    with pytest.raises(ValueError):
        parse_lcsc_csv(f)
