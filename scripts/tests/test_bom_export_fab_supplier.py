"""Unit tests for bom_export.py's fab-supplier-attachment helpers."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bom_export import main  # noqa: E402 (used by dry-run test below)

from bom_export import _ensure_fab_supplier_part  # noqa: E402


def _part(pk=100, name="v0.1 BusBoard PCB"):
    p = MagicMock()
    p.pk = pk
    p.name = name
    return p


def _supplier(pk=381, name="JLCPCB"):
    s = MagicMock()
    s.pk = pk
    s.name = name
    return s


def _supplier_part_mock(pk, sku):
    sp = MagicMock()
    sp.pk = pk
    sp.SKU = sku
    return sp


def test_ensure_fab_supplier_part_creates_when_missing():
    """No existing SupplierPart for (part, supplier) → create new."""
    api = MagicMock()
    part = _part(pk=1291)
    supplier = _supplier(pk=381)
    sku = "v0.1 BusBoard PCB rev 0.1"

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = []
        _ensure_fab_supplier_part(api, part, supplier, sku)

    SP.list.assert_called_once_with(api, part=1291, supplier=381)
    SP.create.assert_called_once()
    payload = SP.create.call_args[0][1]
    assert payload["part"] == 1291
    assert payload["supplier"] == 381
    assert payload["SKU"] == sku


def test_ensure_fab_supplier_part_skips_when_existing():
    """Matching SKU already linked → no new SupplierPart created."""
    api = MagicMock()
    part = _part(pk=1291)
    supplier = _supplier(pk=381)
    sku = "v0.1 BusBoard PCB rev 0.1"

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = [_supplier_part_mock(pk=500, sku=sku)]
        _ensure_fab_supplier_part(api, part, supplier, sku)

    SP.create.assert_not_called()


def test_ensure_fab_supplier_part_creates_when_existing_different_sku():
    """SP exists but for a different SKU (= different design rev) → create."""
    api = MagicMock()
    part = _part(pk=1291)
    supplier = _supplier(pk=381)

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = [
            _supplier_part_mock(pk=500, sku="OLD-DESIGN-PCB rev 0.0")
        ]
        _ensure_fab_supplier_part(api, part, supplier,
                                  "v0.1 BusBoard PCB rev 0.1")

    SP.create.assert_called_once()


def test_ensure_fab_supplier_part_list_failure_skips_gracefully(caplog):
    """API failure during list → no exception escapes, no create attempted."""
    caplog.set_level(logging.WARNING, logger="bom_export")
    api = MagicMock()
    part = _part()
    supplier = _supplier()

    with patch("bom_export.SupplierPart") as SP:
        SP.list.side_effect = ConnectionError("server down")
        _ensure_fab_supplier_part(api, part, supplier, "X")

    SP.create.assert_not_called()
    # Operator's only signal that fab linkage failed must survive future refactors
    assert any(
        r.levelno == logging.WARNING and "SupplierPart lookup" in r.message
        for r in caplog.records
    )


def test_ensure_fab_supplier_part_create_failure_logged_not_raised(caplog):
    """API failure during create → logged, no exception escapes."""
    caplog.set_level(logging.WARNING, logger="bom_export")
    api = MagicMock()
    part = _part()
    supplier = _supplier()

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = []
        SP.create.side_effect = RuntimeError("conflict")
        # Must not raise:
        _ensure_fab_supplier_part(api, part, supplier, "X")

    assert any(
        r.levelno == logging.WARNING and "SupplierPart create failed" in r.message
        for r in caplog.records
    )


def test_ensure_fab_supplier_part_returns_early_on_first_match():
    """Existing list has a non-matching SP followed by a matching one →
    helper still skips create (loop continues to find the match)."""
    api = MagicMock()
    part = _part()
    supplier = _supplier()
    sku = "v0.1 BusBoard PCB rev 0.1"

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = [
            _supplier_part_mock(pk=500, sku="OLD-DESIGN-PCB rev 0.0"),
            _supplier_part_mock(pk=501, sku=sku),  # match
        ]
        _ensure_fab_supplier_part(api, part, supplier, sku)

    SP.create.assert_not_called()


def test_ensure_fab_supplier_part_tolerates_none_sku_on_existing():
    """An existing SP with SKU=None → coerced to '' by the `or` chain, no match → create."""
    api = MagicMock()
    part = _part()
    supplier = _supplier()
    target_sku = "v0.1 BusBoard PCB rev 0.1"

    with patch("bom_export.SupplierPart") as SP:
        sp_with_none = MagicMock()
        sp_with_none.pk = 999
        sp_with_none.SKU = None
        SP.list.return_value = [sp_with_none]
        _ensure_fab_supplier_part(api, part, supplier, target_sku)

    # SKU=None coerced to "", doesn't match target → create proceeds
    SP.create.assert_called_once()


from bom_export import create_pcb_part, create_stencil_part, create_assembly_part  # noqa: E402


def _category(pk=10):
    c = MagicMock()
    c.pk = pk
    return c


def test_create_pcb_part_attaches_fab_supplier_on_new_part():
    """New PCB Part → helper called with SKU derived from name+revision."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1291, name="v0.1 BusBoard PCB")
    supplier = _supplier(pk=381, name="JLCPCB")

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)

        result = create_pcb_part(api, cat, "v0.1 BusBoard", "0.1",
                                 "/tmp/img.png", fab_supplier=supplier)

    assert result is new_part
    helper.assert_called_once_with(
        api, new_part, supplier, "v0.1 BusBoard PCB rev 0.1",
    )


def test_create_pcb_part_attaches_fab_supplier_on_reuse():
    """Existing PCB Part → helper STILL called (retroactive linkage)."""
    api = MagicMock()
    cat = _category()
    existing = _part(pk=999, name="v0.1 BusBoard PCB")
    supplier = _supplier()

    with patch("bom_export.find_part_by_name_and_revision",
               return_value=existing), \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        result = create_pcb_part(api, cat, "v0.1 BusBoard", "0.1",
                                 "/tmp/img.png", fab_supplier=supplier)

    assert result is existing
    helper.assert_called_once_with(
        api, existing, supplier, "v0.1 BusBoard PCB rev 0.1",
    )


def test_create_pcb_part_skips_fab_supplier_when_none():
    """fab_supplier=None → helper NOT called."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1291)

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)
        create_pcb_part(api, cat, "v0.1 BusBoard", "0.1",
                        "/tmp/img.png", fab_supplier=None)

    helper.assert_not_called()


def test_create_stencil_part_attaches_fab_supplier_on_new_part():
    """Stencil branch: SKU uses 'SMT Stencil' subtype."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1293, name="v0.1 BusBoard SMT Stencil")
    supplier = _supplier()

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)
        create_stencil_part(api, cat, "v0.1 BusBoard", "0.1",
                            "/tmp/img.png", fab_supplier=supplier)

    helper.assert_called_once_with(
        api, new_part, supplier, "v0.1 BusBoard SMT Stencil rev 0.1",
    )


def test_create_assembly_part_does_not_attach_fab_supplier():
    """Assembly Module never gets supplier (built in-house)."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1292, name="v0.1 BusBoard Module")

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)
        create_assembly_part(api, cat, "v0.1 BusBoard", "0.1", "/tmp/img.png")

    helper.assert_not_called()


def test_dry_run_does_not_resolve_fab_supplier(tmp_path, monkeypatch):
    """--dry-run must NOT call get_or_create_supplier (would auto-create Company).

    Same bug class as PR #31 for the order-importer: any write-on-missing
    helper called before the dry-run gate violates the contract.
    """
    csv_file = tmp_path / "test-bom.csv"
    csv_file.write_text(
        "Row,Description,Part,References,Value,Footprint,Quantity Per PCB,Status,Datasheet,LCSC,MOUSER\n"
        "1,Resistor,R,R1,10k,R_0805_2012Metric,1, ,~,C17414,\n"
    )
    pcb_img = tmp_path / "pcb.png"
    pcb_img.write_text("dummy")
    asm_img = tmp_path / "asm.png"
    asm_img.write_text("dummy")
    monkeypatch.setenv("INVENTREE_API_HOST", "http://localhost")
    monkeypatch.setenv("INVENTREE_API_TOKEN", "deadbeef")

    monkeypatch.setattr(sys, "argv", [
        "bom_export.py",
        "--csv_file", str(csv_file),
        "--name", "TestBoard",
        "--version", "1.0",
        "--pcb_image", str(pcb_img),
        "--assembly_image", str(asm_img),
        "--pcb-supplier", "NewFab",
        "--dry-run",
    ])

    with patch("bom_export.InvenTreeAPI"), \
         patch("bom_export.ensure_parts_exist"), \
         patch("bom_export.match_supplier_parts"), \
         patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.get_or_create_supplier") as gos, \
         patch("bom_export.load_category_map", return_value={}):
        try:
            main()
        except SystemExit:
            pass  # normal exit from main()

    # The dry-run path must NOT fetch the fab supplier (would create Company)
    gos.assert_not_called()
