"""Pure-Python unit tests for cost_report — no network, no InvenTree mocks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Bootstrap sys.path so `inventree_sync` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.cost_report import _cheapest_price, _render_markdown


def test_cheapest_price_single_supplier_simple():
    """One supplier, one price break."""
    price_data = {"LCSC": [(1, 0.10)]}
    assert _cheapest_price(price_data, 1) == (0.10, "LCSC")
    assert _cheapest_price(price_data, 100) == (0.10, "LCSC")


def test_cheapest_price_threshold_excludes_high_qty_break():
    """A price break with qty_threshold > required is not valid."""
    price_data = {"LCSC": [(10, 0.10), (100, 0.08)]}
    # required=1 means no valid break (threshold 10 and 100 both > 1)
    assert _cheapest_price(price_data, 1) is None
    # required=10 picks the 10-break
    assert _cheapest_price(price_data, 10) == (0.10, "LCSC")
    # required=100 picks the cheaper 100-break
    assert _cheapest_price(price_data, 100) == (0.08, "LCSC")


def test_cheapest_price_two_suppliers_chooses_cheaper():
    """When both suppliers have valid breaks, pick the cheaper one."""
    price_data = {
        "LCSC":   [(10, 0.10), (100, 0.08)],
        "Mouser": [(1, 0.12),  (500, 0.06)],
    }
    assert _cheapest_price(price_data, 1) == (0.12, "Mouser")
    assert _cheapest_price(price_data, 10) == (0.10, "LCSC")
    assert _cheapest_price(price_data, 100) == (0.08, "LCSC")
    assert _cheapest_price(price_data, 500) == (0.06, "Mouser")


def test_cheapest_price_empty_returns_none():
    assert _cheapest_price({}, 10) is None
    assert _cheapest_price({"LCSC": []}, 10) is None


def test_render_markdown_basic_table():
    """Headline + 3 rows + missing-prices vermerk."""
    rows = [
        (1,   58.20, 58.20,  {"LCSC": 45, "Mouser": 2}),
        (10,  38.50, 3.85,   {"LCSC": 47}),
        (100, 24.10, 0.241,  {"LCSC": 47}),
    ]
    md = _render_markdown(
        title="FMTransceiver v1.2 (Assembly pk=42)",
        rows=rows,
        total_items=47,
        missing=[("R_Custom", "R Custom 0805"), ("XTAL_Custom", "XTAL Custom 32MHz")],
    )
    assert "## BOM Cost Report — FMTransceiver v1.2" in md
    assert "| Qty | Total | per-Board | Sources" in md
    assert "| 1 | €58.20 | €58.200 | LCSC (45), Mouser (2) |" in md
    assert "| 10 | €38.50 | €3.850 | LCSC (47) |" in md
    assert "| 100 | €24.10 | €0.241 | LCSC (47) |" in md
    assert "47 total — 2 had no price data" in md
    assert "R_Custom" in md and "XTAL_Custom" in md


def test_render_markdown_no_missing_omits_vermerk():
    rows = [(1, 1.00, 1.00, {"LCSC": 5})]
    md = _render_markdown(
        title="Test",
        rows=rows,
        total_items=5,
        missing=[],
    )
    assert "had no price data" not in md
