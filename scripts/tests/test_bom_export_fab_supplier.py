"""Unit tests for bom_export.py's fab-supplier-attachment helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_ensure_fab_supplier_part_list_failure_skips_gracefully():
    """API failure during list → no exception escapes, no create attempted."""
    api = MagicMock()
    part = _part()
    supplier = _supplier()

    with patch("bom_export.SupplierPart") as SP:
        SP.list.side_effect = ConnectionError("server down")
        _ensure_fab_supplier_part(api, part, supplier, "X")

    SP.create.assert_not_called()


def test_ensure_fab_supplier_part_create_failure_logged_not_raised():
    """API failure during create → logged, no exception escapes."""
    api = MagicMock()
    part = _part()
    supplier = _supplier()

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = []
        SP.create.side_effect = RuntimeError("conflict")
        # Must not raise:
        _ensure_fab_supplier_part(api, part, supplier, "X")
