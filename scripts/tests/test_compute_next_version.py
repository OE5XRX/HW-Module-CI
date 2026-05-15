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


# ---------------------------------------------------------------------------
# list_release_tags
# ---------------------------------------------------------------------------

def test_list_release_tags_parses_gh_json(monkeypatch):
    import json
    import subprocess

    captured_args = {}

    def fake(args, *a, **kw):
        captured_args["args"] = list(args)
        return json.dumps([
            {"tagName": "v1.0"},
            {"tagName": "v0.9"},
            {"tagName": "v2.0"},
        ])

    monkeypatch.setattr(subprocess, "check_output", fake)
    result = cnv.list_release_tags()
    assert result == ["v1.0", "v0.9", "v2.0"]
    assert captured_args["args"][:2] == ["gh", "release"]


def test_list_release_tags_empty(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: "[]")
    assert cnv.list_release_tags() == []


# ---------------------------------------------------------------------------
# changed_files
# ---------------------------------------------------------------------------

def test_changed_files_parses_git_diff(monkeypatch):
    import subprocess

    captured_args = {}

    def fake(args, *a, **kw):
        captured_args["args"] = list(args)
        return "foo.kicad_pcb\nbar.kicad_sch\n"

    monkeypatch.setattr(subprocess, "check_output", fake)
    result = cnv.changed_files("v1.5")
    assert result == ["foo.kicad_pcb", "bar.kicad_sch"]
    assert "v1.5..HEAD" in captured_args["args"]


def test_changed_files_strips_empty_lines(monkeypatch):
    import subprocess
    monkeypatch.setattr(
        subprocess, "check_output",
        lambda *a, **kw: "foo.kicad_pcb\n\n   \nbar.kicad_sch\n",
    )
    assert cnv.changed_files("v1.5") == ["foo.kicad_pcb", "bar.kicad_sch"]


# ---------------------------------------------------------------------------
# main — end-to-end orchestration
# ---------------------------------------------------------------------------

def _stub_subprocess(monkeypatch, tags, changed):
    """Stub subprocess.check_output for both gh release list and git diff."""
    import json
    import subprocess

    payload = json.dumps([{"tagName": t} for t in tags])
    diff_out = "\n".join(changed) + ("\n" if changed else "")

    def fake(args, *a, **kw):
        if args[:2] == ["gh", "release"]:
            return payload
        if args[:2] == ["git", "diff"]:
            return diff_out
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(subprocess, "check_output", fake)


def test_main_no_releases_exits_2(monkeypatch, capsys):
    _stub_subprocess(monkeypatch, tags=[], changed=[])
    rc = cnv.main()
    captured = capsys.readouterr()
    assert rc == 2
    assert "::error::" in captured.out
    assert "gh release create v1.0 --generate-notes" in captured.out


def test_main_only_legacy_v0_exits_2(monkeypatch, capsys):
    _stub_subprocess(monkeypatch, tags=["v0.1", "v0.9"], changed=[])
    rc = cnv.main()
    assert rc == 2


def test_main_no_bump_writes_none(monkeypatch, tmp_path, capsys):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    _stub_subprocess(
        monkeypatch, tags=["v1.5"], changed=["doc/index.md"]
    )
    rc = cnv.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "::notice::" in captured.out
    assert "bump_type=none" in out_file.read_text()


def test_main_minor_bump(monkeypatch, tmp_path):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    _stub_subprocess(
        monkeypatch, tags=["v1.5"], changed=["board.kicad_sch"]
    )
    rc = cnv.main()
    content = out_file.read_text()
    assert rc == 0
    assert "bump_type=minor" in content
    assert "next_tag=v1.6" in content


def test_main_major_bump(monkeypatch, tmp_path):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    _stub_subprocess(
        monkeypatch, tags=["v1.5"], changed=["board.kicad_pcb"]
    )
    rc = cnv.main()
    content = out_file.read_text()
    assert rc == 0
    assert "bump_type=major" in content
    assert "next_tag=v2.0" in content


def test_main_out_of_order_tags_uses_highest(monkeypatch, tmp_path):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    _stub_subprocess(
        monkeypatch,
        tags=["v1.0", "v2.0", "v1.5"],
        changed=["board.kicad_sch"],
    )
    rc = cnv.main()
    content = out_file.read_text()
    assert rc == 0
    assert "next_tag=v2.1" in content
