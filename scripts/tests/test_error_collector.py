"""Pure-Python unit tests for ErrorCollector (bom_export.py)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Bootstrap sys.path so `bom_export` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bom_export import ErrorCollector


def test_empty_collector_has_no_errors():
    c = ErrorCollector()
    assert c.has_errors() is False
    assert c.errors == []


def test_add_one_error():
    c = ErrorCollector()
    c.add("Parts", "R1", "no InvenTree match")
    assert c.has_errors() is True
    assert len(c.errors) == 1
    assert c.errors[0] == ("Parts", "R1", "no InvenTree match")


def test_multiple_errors_preserved_in_order():
    """Insertion order matters for the summary output — preserve it."""
    c = ErrorCollector()
    c.add("Parts", "R1", "first")
    c.add("Parts", "R2", "second")
    c.add("BomItem", "X", "third")
    assert [e[1] for e in c.errors] == ["R1", "R2", "X"]


def test_print_summary_no_errors_is_quiet(caplog):
    """Empty collector → print_summary emits nothing at ERROR level."""
    c = ErrorCollector()
    with caplog.at_level(logging.ERROR):
        c.print_summary()
    assert caplog.records == []


def test_print_summary_with_errors_logs_each(caplog):
    """Each error appears in the ERROR-level log output."""
    c = ErrorCollector()
    c.add("Parts", "R1", "no InvenTree match")
    c.add("Parts", "R2", "no supplier data")
    with caplog.at_level(logging.ERROR):
        c.print_summary()
    # Header + 2 errors + footer = at least 4 records.
    assert len(caplog.records) >= 4
    text = "\n".join(r.message for r in caplog.records)
    assert "Sync completed with 2 error(s)" in text
    assert "[Parts] R1" in text and "no InvenTree match" in text
    assert "[Parts] R2" in text and "no supplier data" in text
