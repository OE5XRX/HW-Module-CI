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


# ---------------------------------------------------------------------------
# find_previous_release
# ---------------------------------------------------------------------------

def test_find_previous_empty_list():
    assert apm.find_previous_release([], current_tag="v1.0") is None


def test_find_previous_only_current():
    assert apm.find_previous_release(["v1.0"], current_tag="v1.0") is None


def test_find_previous_one_older():
    assert apm.find_previous_release(
        ["v1.0", "v2.0"], current_tag="v2.0"
    ) == (1, 0, "v1.0")


def test_find_previous_multiple_older():
    assert apm.find_previous_release(
        ["v1.0", "v1.5", "v2.0"], current_tag="v2.0"
    ) == (1, 5, "v1.5")


def test_find_previous_out_of_order():
    assert apm.find_previous_release(
        ["v2.0", "v1.0", "v1.5"], current_tag="v2.0"
    ) == (1, 5, "v1.5")


def test_find_previous_filters_malformed():
    assert apm.find_previous_release(
        ["release/v1.2", "v1.0-rc1", "v0.9", "v1.0", "v2.0"],
        current_tag="v2.0",
    ) == (1, 0, "v1.0")


# ---------------------------------------------------------------------------
# decide_archive
# ---------------------------------------------------------------------------

def test_decide_no_previous():
    assert apm.decide_archive(current_major=1, previous_major=None) == "noop-first-release"


def test_decide_same_major():
    assert apm.decide_archive(current_major=1, previous_major=1) == "noop-same-major"


def test_decide_major_bump():
    assert apm.decide_archive(current_major=2, previous_major=1) == "archive"


def test_decide_two_step_major_bump():
    # v1.x → v3.0 (skipping v2): still an archive of v1 — only the immediate
    # predecessor gets archived. v2 was never deployed live anyway.
    assert apm.decide_archive(current_major=3, previous_major=1) == "archive"


def test_decide_downgrade_is_error():
    assert apm.decide_archive(current_major=1, previous_major=2) == "error-downgrade"
