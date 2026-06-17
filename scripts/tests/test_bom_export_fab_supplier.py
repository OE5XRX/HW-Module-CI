"""Unit tests for bom_export.py's fab-supplier-attachment helpers."""
from __future__ import annotations

import logging
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
