"""Mock-based tests for ensure_part_for_order_line."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.models import PartData  # noqa: E402
from inventree_sync.order_import import (  # noqa: E402
    SupplierOrderLine,
    ensure_part_for_order_line,
)


def _line(sku="C1739", supplier="LCSC"):
    line = SupplierOrderLine(
        sku=sku, qty=100, unit_price=0.01, currency="USD",
        mpn="0805B333K500NT", mfr_name="FH", description="33nF",
        package="0805",
    )
    return line, supplier


def _supplier_part_mock(pk=42, sku="C1739", part_pk=101):
    sp = MagicMock()
    sp.pk = pk
    sp.SKU = sku
    sp.part = part_pk
    return sp


def _part_mock(pk=101):
    p = MagicMock()
    p.pk = pk
    return p


def test_existing_part_via_sku_returns_part_and_supplier_part():
    api = MagicMock()
    lcsc_fetcher = MagicMock()
    mouser_fetcher = MagicMock()
    lcsc_supplier = MagicMock(); lcsc_supplier.pk = 1; lcsc_supplier.name = "LCSC"
    mouser_supplier = MagicMock(); mouser_supplier.pk = 2; mouser_supplier.name = "Mouser"

    line, supplier_kind = _line()

    with patch("inventree_sync.order_import.find_existing_part") as find_exist, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        find_exist.return_value = _part_mock(pk=101)
        SP.list.return_value = [_supplier_part_mock(pk=42, sku="C1739", part_pk=101)]
        part, supplier_part = ensure_part_for_order_line(
            api, line, supplier_kind, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map={},
        )
    assert part.pk == 101
    assert supplier_part.pk == 42
    # Fetcher must NOT be called when SKU lookup already hit
    lcsc_fetcher.fetch_by_sku.assert_not_called()
    mouser_fetcher.fetch.assert_not_called()


def test_routes_to_lcsc_fetcher_for_lcsc_line():
    api = MagicMock()
    lcsc_fetcher = MagicMock()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    mouser_fetcher = MagicMock()
    line, supplier_kind = _line(supplier="LCSC")

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        rcat.return_value = MagicMock(pk=9)
        created = _part_mock(pk=202)
        create.return_value = created
        SP.list.return_value = [_supplier_part_mock(pk=55, sku="C1739", part_pk=202)]

        part, sp = ensure_part_for_order_line(
            api, line, supplier_kind,
            lcsc_fetcher, MagicMock(),
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    lcsc_fetcher.fetch_by_sku.assert_called_once_with("C1739")
    create.assert_called_once()
    # Verify SKU lists routed correctly:
    kwargs = create.call_args.kwargs
    assert kwargs["lcsc_skus"] == ["C1739"]
    assert kwargs["mouser_skus"] == []
    assert part.pk == 202
    assert sp.pk == 55


def test_routes_to_mouser_fetcher_for_mouser_line():
    line = SupplierOrderLine(
        sku="576-0297003.L", qty=10, unit_price=0.381, currency="EUR",
        mpn="0297003.L", mfr_name="", description="Fuse",
    )
    mouser_fetcher = MagicMock()
    mouser_fetcher.fetch.return_value = PartData(
        mpn="0297003.L", manufacturer="Littelfuse",
        description="Fuse 3A", mouser_sku="576-0297003.L",
    )

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        rcat.return_value = MagicMock(pk=9)
        create.return_value = _part_mock(pk=303)
        SP.list.return_value = [_supplier_part_mock(pk=66, sku="576-0297003.L", part_pk=303)]

        part, sp = ensure_part_for_order_line(
            MagicMock(), line, "Mouser",
            MagicMock(), mouser_fetcher,
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    mouser_fetcher.fetch.assert_called_once_with("576-0297003.L")
    kwargs = create.call_args.kwargs
    assert kwargs["lcsc_skus"] == []
    assert kwargs["mouser_skus"] == ["576-0297003.L"]


def test_existing_via_mpn_links_supplier_part():
    line, supplier_kind = _line(supplier="LCSC")
    lcsc_fetcher = MagicMock()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    existing = _part_mock(pk=404)

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = existing
        SP.list.return_value = [_supplier_part_mock(pk=77, sku="C1739", part_pk=404)]

        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, MagicMock(),
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    esp.assert_called_once()
    assert part.pk == 404
    assert sp.pk == 77


def test_fetcher_failure_falls_back_to_file_data():
    """If both LCSC and Mouser APIs return None, build PartData from the line."""
    line, supplier_kind = _line(supplier="LCSC")
    lcsc_fetcher = MagicMock()
    lcsc_fetcher.fetch_by_sku.return_value = None

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        rcat.return_value = MagicMock(pk=9)
        create.return_value = _part_mock(pk=505)
        SP.list.return_value = [_supplier_part_mock(pk=88, sku="C1739", part_pk=505)]

        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, MagicMock(),
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    # create called with PartData built from line, not None
    args, kwargs = create.call_args[0], create.call_args.kwargs
    part_data = args[2] if len(args) > 2 else kwargs["part_data"]
    assert part_data.mpn == "0805B333K500NT"
    assert part_data.manufacturer == "FH"


def test_lcsc_line_requires_lcsc_fetcher_and_supplier():
    """Passing None for the active side raises a clear ValueError up-front."""
    import pytest
    line, supplier_kind = _line(supplier="LCSC")
    with pytest.raises(ValueError, match="LCSC line requires"):
        ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            None, MagicMock(),               # lcsc_fetcher=None
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )
    with pytest.raises(ValueError, match="LCSC line requires"):
        ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            MagicMock(), MagicMock(),
            None, MagicMock(pk=2),           # lcsc_supplier=None
            category_map={},
        )


def test_mouser_line_requires_mouser_fetcher_and_supplier():
    """Same for Mouser side — opposite mismatch."""
    import pytest
    line = SupplierOrderLine(
        sku="576-0297003.L", qty=10, unit_price=0.381, currency="EUR",
        mpn="0297003.L", mfr_name="", description="Fuse",
    )
    with pytest.raises(ValueError, match="Mouser line requires"):
        ensure_part_for_order_line(
            MagicMock(), line, "Mouser",
            MagicMock(), None,               # mouser_fetcher=None
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )
    with pytest.raises(ValueError, match="Mouser line requires"):
        ensure_part_for_order_line(
            MagicMock(), line, "Mouser",
            MagicMock(), MagicMock(),
            MagicMock(pk=1), None,           # mouser_supplier=None
            category_map={},
        )


def test_unused_side_may_be_none():
    """An LCSC line accepts None for the *unused* Mouser fetcher/supplier."""
    line, supplier_kind = _line(supplier="LCSC")
    lcsc_fetcher = MagicMock()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        rcat.return_value = MagicMock(pk=9)
        create.return_value = _part_mock(pk=606)
        SP.list.return_value = [_supplier_part_mock(pk=99, sku="C1739", part_pk=606)]
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, None,              # mouser_fetcher unused → None
            MagicMock(pk=1), None,           # mouser_supplier unused → None
            category_map={},
        )
    assert part.pk == 606
    assert sp.pk == 99


# ---------------------------------------------------------------------------
# Dry-run path tests (reporter passed in → no writes, only records)
# ---------------------------------------------------------------------------

from inventree_sync.dry_run import DryRunReporter  # noqa: E402


def _supplier_setup():
    """Fresh fetcher / supplier mocks used across dry-run tests."""
    lcsc_fetcher = MagicMock()
    mouser_fetcher = MagicMock()
    lcsc_supplier = MagicMock(); lcsc_supplier.pk = 1; lcsc_supplier.name = "LCSC"
    mouser_supplier = MagicMock(); mouser_supplier.pk = 2; mouser_supplier.name = "Mouser"
    return lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier


def test_dry_run_sku_hit_records_reuse_and_no_writes():
    """SKU lookup hit → REUSE record, no ensure_supplier_parts/create_part call."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as find_exist, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat:
        find_exist.return_value = _part_mock(pk=101)
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    lookup_sp.assert_not_called()
    esp.assert_not_called()
    create.assert_not_called()
    rcat.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "REUSE"
    assert r.category == "Parts"
    assert r.target == line.sku
    assert "pk=101" in r.detail


def test_dry_run_mpn_hit_records_reuse_via_mpn_and_no_writes():
    """MPN+Mfr hit → REUSE record mentioning MPN, no ensure_supplier_parts call."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp:
        fe.return_value = None
        fmpn.return_value = _part_mock(pk=202)
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    fname.assert_not_called()         # MPN-Hit short-circuits before name lookup
    esp.assert_not_called()
    create.assert_not_called()
    rcat.assert_not_called()
    lookup_sp.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "REUSE"
    assert "MPN+Mfr" in r.detail
    assert "pk=202" in r.detail


def test_dry_run_name_hit_records_reuse_via_name_and_no_writes():
    """Name lookup hit → REUSE record mentioning name, no ensure_supplier_parts call."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = _part_mock(pk=303)
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    esp.assert_not_called()
    create.assert_not_called()
    rcat.assert_not_called()
    lookup_sp.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "REUSE"
    assert "name" in r.detail.lower()
    assert "pk=303" in r.detail


def test_dry_run_create_records_create_and_no_writes():
    """No lookup hit → CREATE record with planned name, no actual create."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    rcat.assert_not_called()
    create.assert_not_called()
    esp.assert_not_called()
    lookup_sp.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "CREATE"
    assert r.category == "Parts"
    assert r.target == line.sku
    assert "0805B333K500NT" in r.detail   # planned name = part_data.mpn


def test_dry_run_fetcher_failure_still_records_create_using_file_data():
    """Supplier API None → fallback PartData → CREATE record from file row mpn."""
    line, supplier_kind = _line()
    lcsc_fetcher, mouser_fetcher, lcsc_supplier, mouser_supplier = _supplier_setup()
    lcsc_fetcher.fetch_by_sku.return_value = None   # supplier API down
    reporter = DryRunReporter()

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import._lookup_supplier_part") as lookup_sp:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier,
            category_map={},
            reporter=reporter,
        )

    assert part is None and sp is None
    rcat.assert_not_called()
    create.assert_not_called()
    esp.assert_not_called()
    lookup_sp.assert_not_called()
    assert len(reporter.records) == 1
    r = reporter.records[0]
    assert r.action == "CREATE"
    # Name fallback: part_data.mpn (None) → line.mpn ("0805B333K500NT")
    assert "0805B333K500NT" in r.detail
