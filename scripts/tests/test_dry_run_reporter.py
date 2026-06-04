"""Pure-Python unit tests for DryRunReporter."""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.dry_run import DryRunReporter


def test_record_appends():
    rep = DryRunReporter()
    rep.record("CREATE", "Parts", "R 10k 0805", "LCSC C17414")
    rep.record("REUSE", "Parts", "C 100nF 0805", "existing pk=4221")
    assert len(rep.records) == 2
    assert rep.records[0].action == "CREATE"
    assert rep.records[1].target == "C 100nF 0805"


def test_has_failures_false_when_only_create_reuse_skip():
    rep = DryRunReporter()
    rep.record("CREATE", "Parts", "X")
    rep.record("REUSE",  "Parts", "Y")
    rep.record("SKIP",   "Parts", "Z", "no SKU")
    assert rep.has_failures() is False


def test_has_failures_true_on_fail_record():
    rep = DryRunReporter()
    rep.record("CREATE", "Parts", "X")
    rep.record("FAIL",   "Parts", "BAT54", "no supplier data found")
    assert rep.has_failures() is True


def test_print_report_groups_by_category():
    rep = DryRunReporter()
    rep.record("REUSE",  "Parts",    "R 10k 0805", "existing pk=4221")
    rep.record("CREATE", "Parts",    "STM32U575", "LCSC C4567890")
    rep.record("REUSE",  "Assembly", "FMTransceiver Module rev 1.2", "pk=99")
    rep.record("CREATE", "BomItem",  "47 items")

    buf = io.StringIO()
    rep.print_report(file=buf, title="bom_export FMTransceiver v1.2")
    out = buf.getvalue()

    assert "DRY-RUN: bom_export FMTransceiver v1.2" in out
    # Categories must appear as section headers.
    assert "Parts:" in out
    assert "Assembly:" in out
    assert "BomItem:" in out
    # Records appear under their category.
    parts_section = out.split("Parts:")[1].split("Assembly:")[0]
    assert "Would REUSE:  R 10k 0805" in parts_section
    assert "Would CREATE: STM32U575" in parts_section
    # Summary line at the end.
    assert "Summary:" in out
    assert "2 CREATE" in out and "2 REUSE" in out


def test_print_report_exit_marker_when_failures():
    rep = DryRunReporter()
    rep.record("FAIL", "Parts", "BAT54", "no supplier data found")
    buf = io.StringIO()
    rep.print_report(file=buf, title="t")
    out = buf.getvalue()
    assert "EXIT: 1" in out
    assert "would-fail present" in out


def test_record_rejects_unknown_action():
    """A typo'd action should fail-fast at the call site, not KeyError later."""
    import pytest
    rep = DryRunReporter()
    with pytest.raises(ValueError, match="must be one of"):
        rep.record("CRAETE", "Parts", "X")  # typo of CREATE


def test_print_report_empty_records():
    """Empty reporter still produces a clean Summary + EXIT marker."""
    buf = io.StringIO()
    DryRunReporter().print_report(file=buf)
    out = buf.getvalue()
    assert "Summary: 0 CREATE, 0 REUSE, 0 SKIP, 0 would-fail" in out
    assert "EXIT: 0" in out
