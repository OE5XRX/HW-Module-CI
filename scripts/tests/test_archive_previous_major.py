"""Tests for scripts/archive_previous_major.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archive_previous_major as apm  # noqa: E402


# ---------------------------------------------------------------------------
# parse_tag
# ---------------------------------------------------------------------------

def test_parse_tag_v1_0():
    assert apm.parse_tag("v1.0") == (1, 0)


def test_parse_tag_v10_3():
    assert apm.parse_tag("v10.3") == (10, 3)


def test_parse_tag_v0_9_returns_none():
    # MAJOR < 1 is pre-baseline, not a managed release tag
    assert apm.parse_tag("v0.9") is None


def test_parse_tag_v1_0_rc1_returns_none():
    assert apm.parse_tag("v1.0-rc1") is None


def test_parse_tag_release_prefix_returns_none():
    assert apm.parse_tag("release/v1.2") is None


def test_parse_tag_no_v_prefix_returns_none():
    assert apm.parse_tag("1.0") is None


def test_parse_tag_empty_returns_none():
    assert apm.parse_tag("") is None
