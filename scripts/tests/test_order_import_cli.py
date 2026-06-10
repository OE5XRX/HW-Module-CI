"""Smoke tests for the CLI argparse + main() wiring (no real InvenTree)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import import_supplier_order as cli  # noqa: E402


def test_parse_args_requires_at_least_one_input():
    with pytest.raises(SystemExit):
        cli.parse_args([])  # neither flag


def test_parse_args_accepts_lcsc_only():
    args = cli.parse_args(["--lcsc-csv", "/tmp/x.csv"])
    assert args.lcsc_csv == "/tmp/x.csv"
    assert args.mouser_xls is None


def test_parse_args_dry_run_default_off():
    args = cli.parse_args(["--mouser-xls", "/tmp/x.xls"])
    assert args.dry_run is False


def test_parse_args_location_default():
    args = cli.parse_args(["--mouser-xls", "/tmp/x.xls"])
    assert args.location == "Lager"


def test_main_imports_lcsc_when_flag_given(tmp_path, monkeypatch):
    """main() should call upsert_purchase_order once for the LCSC file."""
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
    monkeypatch.setenv("MOUSER_API_KEY", "x")

    with patch("import_supplier_order.InvenTreeAPI") as API, \
         patch("import_supplier_order.LCSCFetcher"), \
         patch("import_supplier_order.MouserFetcher"), \
         patch("import_supplier_order.get_or_create_supplier") as gos, \
         patch("import_supplier_order.get_receive_location") as grl, \
         patch("import_supplier_order.ensure_part_for_order_line") as epfol, \
         patch("import_supplier_order.upsert_purchase_order") as upsert, \
         patch("import_supplier_order.load_category_map", return_value={}):
        API.return_value = MagicMock()
        gos.return_value = MagicMock(pk=1, name="LCSC")
        grl.return_value = MagicMock(pk=7)
        epfol.return_value = (MagicMock(pk=100), MagicMock(pk=200, SKU="C1"))
        upsert.return_value = MagicMock(action="CREATED", po_reference="WM",
                                        lines_added=1, lines_updated=0,
                                        lines_deleted=0)
        rc = cli.main(["--lcsc-csv", str(csv_file)])
    assert rc == 0
    upsert.assert_called_once()


def test_main_returns_nonzero_on_drift(tmp_path, monkeypatch):
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
         patch("import_supplier_order.load_category_map", return_value={}):
        gos.return_value = MagicMock(pk=1)
        grl.return_value = MagicMock(pk=7)
        epfol.return_value = (MagicMock(pk=100), MagicMock(pk=200, SKU="C1"))
        upsert.side_effect = RuntimeError("drift!")
        rc = cli.main(["--lcsc-csv", str(csv_file)])
    assert rc == 1
