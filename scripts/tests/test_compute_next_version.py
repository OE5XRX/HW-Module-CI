"""Tests for scripts/compute_next_version.py."""
from __future__ import annotations

import sys
from pathlib import Path

# Make compute_next_version importable as a top-level module.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compute_next_version as cnv  # noqa: E402


# ---------------------------------------------------------------------------
# find_baseline
# ---------------------------------------------------------------------------

def test_find_baseline_empty_returns_none():
    assert cnv.find_baseline([]) is None


def test_find_baseline_only_pre_baseline_returns_none():
    assert cnv.find_baseline(["v0.1", "v0.9"]) is None


def test_find_baseline_picks_highest_in_order():
    assert cnv.find_baseline(["v1.0", "v1.5"]) == (1, 5, "v1.5")


def test_find_baseline_picks_highest_out_of_order():
    assert cnv.find_baseline(["v1.0", "v2.0", "v1.5"]) == (2, 0, "v2.0")


def test_find_baseline_ignores_malformed_tags():
    tags = ["release/v1.2", "v1.0-rc1", "v1.0", "weird"]
    assert cnv.find_baseline(tags) == (1, 0, "v1.0")
