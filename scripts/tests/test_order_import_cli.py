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
    # action_kind mapping: DRY_RUN_CREATE → CREATE
    reporter_instance.record.assert_called_once_with(
        "CREATE", "PurchaseOrder", "WM",
        "CREATE added=1 updated=0 deleted=0",
    )


def test_dry_run_in_sync_records_reuse(tmp_path, monkeypatch):
    """Pfad C in-sync (PO COMPLETE + matches file) → reporter records REUSE."""
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
        epfol.return_value = (None, None)
        upsert.return_value = MagicMock(
            action="IN_SYNC", po_reference="WM",
            lines_added=0, lines_updated=0, lines_deleted=0,
        )
        rc = cli.main(["--lcsc-csv", str(csv_file), "--dry-run"])

    assert rc == 0
    reporter_instance.record.assert_called_once_with(
        "REUSE", "PurchaseOrder", "WM",
        "IN_SYNC added=0 updated=0 deleted=0",
    )


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


def test_dry_run_derives_upsert_dry_run_from_reporter(tmp_path, monkeypatch):
    """upsert_purchase_order receives dry_run=True iff a reporter exists.

    Single source of truth: ``_import_one_order`` derives ``dry_run`` from
    ``reporter is not None``; no separate ``dry_run`` parameter exists to
    drift from the reporter state.
    """
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
        epfol.return_value = (None, None)
        upsert.return_value = MagicMock(
            action="DRY_RUN_CREATE", po_reference="WM",
            lines_added=1, lines_updated=0, lines_deleted=0,
        )
        rc = cli.main(["--lcsc-csv", str(csv_file), "--dry-run"])

    assert rc == 0
    assert upsert.call_args.kwargs["dry_run"] is True


def test_real_run_invokes_upsert_with_dry_run_false(tmp_path, monkeypatch):
    """Without --dry-run, upsert_purchase_order receives dry_run=False."""
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
        upsert.return_value = MagicMock(
            action="CREATED", po_reference="WM",
            lines_added=1, lines_updated=0, lines_deleted=0,
        )
        rc = cli.main(["--lcsc-csv", str(csv_file)])

    assert rc == 0
    assert upsert.call_args.kwargs["dry_run"] is False


def test_dry_run_resolution_exception_records_fail(tmp_path, monkeypatch):
    """A resolution-side exception in dry-run records FAIL so the printed
    EXIT line matches the process exit code."""
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
        epfol.side_effect = RuntimeError("boom")
        rc = cli.main(["--lcsc-csv", str(csv_file), "--dry-run"])

    assert rc == 1
    upsert.assert_not_called()
    reporter_instance.record.assert_called_once_with(
        "FAIL", "Parts", "C1",
        "resolution failed: boom",
    )
    reporter_instance.print_report.assert_called_once()


def test_dry_run_upsert_runtime_error_records_fail(tmp_path, monkeypatch):
    """RuntimeError from upsert_purchase_order in dry-run mode records FAIL."""
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
        epfol.return_value = (None, None)
        upsert.side_effect = RuntimeError("drift!")
        rc = cli.main(["--lcsc-csv", str(csv_file), "--dry-run"])

    assert rc == 1
    reporter_instance.record.assert_called_once_with(
        "FAIL", "PurchaseOrder", "WM",
        "upsert failed: drift!",
    )
    reporter_instance.print_report.assert_called_once()


def test_dry_run_parse_exception_records_fail(tmp_path, monkeypatch):
    """A parse failure for the LCSC/Mouser file in dry-run records FAIL."""
    csv_file = tmp_path / "LCSC__WM_1.csv"
    csv_file.write_text(
        "LCSC Part Number,Manufacture Part Number,Manufacturer,Customer NO.,"
        "Package,Description,RoHS,Quantity,Unit Price($),Ext.Price($),"
        "Estimated lead time (business days),Updated lead time,"
        "Date Code / Lot No.\n"
        "C1,MPN1,M1,,0805,desc,YES,bad-qty,0.1,0.5,,,\n"
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
        rc = cli.main(["--lcsc-csv", str(csv_file), "--dry-run"])

    assert rc == 1
    epfol.assert_not_called()
    upsert.assert_not_called()
    # FAIL record uses "Parse" category and the file path as target
    assert reporter_instance.record.call_count == 1
    args = reporter_instance.record.call_args
    assert args[0][0] == "FAIL"
    assert args[0][1] == "Parse"
    assert str(csv_file) in args[0][2]
    assert "LCSC parse failed" in args[0][3]
    reporter_instance.print_report.assert_called_once()
