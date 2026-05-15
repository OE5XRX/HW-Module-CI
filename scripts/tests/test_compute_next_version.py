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


# ---------------------------------------------------------------------------
# decide_bump
# ---------------------------------------------------------------------------

def test_decide_bump_no_files_is_none():
    assert cnv.decide_bump([]) == "none"


def test_decide_bump_only_docs_is_none():
    assert cnv.decide_bump(["doc/index.md", "README.md"]) == "none"


def test_decide_bump_only_sch_is_minor():
    assert cnv.decide_bump(["foo.kicad_sch"]) == "minor"


def test_decide_bump_only_pcb_is_major():
    assert cnv.decide_bump(["foo.kicad_pcb"]) == "major"


def test_decide_bump_pcb_wins_over_sch():
    assert cnv.decide_bump(["foo.kicad_pcb", "bar.kicad_sch"]) == "major"


def test_decide_bump_nested_paths_match():
    assert cnv.decide_bump(["subdir/board.kicad_pcb"]) == "major"


# ---------------------------------------------------------------------------
# write_outputs
# ---------------------------------------------------------------------------

def test_write_outputs_to_github_output(monkeypatch, tmp_path):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    cnv.write_outputs(next_tag="v1.6", bump_type="minor")
    content = out_file.read_text()
    assert "next_tag=v1.6" in content
    assert "bump_type=minor" in content


def test_write_outputs_appends_not_overwrites(monkeypatch, tmp_path):
    out_file = tmp_path / "github_output"
    out_file.write_text("preset=keep\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    cnv.write_outputs(bump_type="none")
    content = out_file.read_text()
    assert "preset=keep" in content
    assert "bump_type=none" in content


def test_write_outputs_without_env_falls_back_to_stdout(
    monkeypatch, capsys
):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    cnv.write_outputs(bump_type="major")
    captured = capsys.readouterr()
    assert "bump_type=major" in captured.out
