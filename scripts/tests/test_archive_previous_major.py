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


# ---------------------------------------------------------------------------
# add_nav_exclude_to_front_matter
# ---------------------------------------------------------------------------

def test_add_nav_exclude_fresh_front_matter(tmp_path):
    p = tmp_path / "index.md"
    p.write_text(
        "---\n"
        "title: Power\n"
        "nav_order: 3\n"
        "parent: Hardware\n"
        "---\n"
        "\n"
        "# Power PCB\n"
        "Some body content.\n"
    )
    modified = apm.add_nav_exclude_to_front_matter(p)
    assert modified is True
    txt = p.read_text()
    assert "nav_exclude: true" in txt
    # Front-matter still bounded by exactly one --- pair at start
    assert txt.count("\n---\n") >= 1
    # Body untouched
    assert "# Power PCB" in txt
    assert "Some body content." in txt


def test_add_nav_exclude_already_present(tmp_path):
    p = tmp_path / "index.md"
    original = (
        "---\n"
        "title: Power\n"
        "nav_exclude: true\n"
        "---\n"
        "\n"
        "Body.\n"
    )
    p.write_text(original)
    modified = apm.add_nav_exclude_to_front_matter(p)
    assert modified is False
    assert p.read_text() == original


def test_add_nav_exclude_no_front_matter(tmp_path):
    p = tmp_path / "index.md"
    original = "# Just a heading\nNo front-matter at all.\n"
    p.write_text(original)
    modified = apm.add_nav_exclude_to_front_matter(p)
    assert modified is False
    assert p.read_text() == original


def test_add_nav_exclude_body_has_dashes(tmp_path):
    """A `---` inside a markdown body (horizontal rule, code block, etc.)
    must not confuse the front-matter parser."""
    p = tmp_path / "index.md"
    p.write_text(
        "---\n"
        "title: Power\n"
        "---\n"
        "\n"
        "# Power PCB\n"
        "\n"
        "Some intro text.\n"
        "\n"
        "---\n"
        "\n"
        "More content after a horizontal rule.\n"
    )
    modified = apm.add_nav_exclude_to_front_matter(p)
    assert modified is True
    txt = p.read_text()
    # nav_exclude landed in the FIRST front-matter block, not the body rule
    head, rest = txt.split("\n---\n", 1)
    assert "nav_exclude: true" in head
    # Horizontal rule still in the body
    assert "More content after a horizontal rule." in rest


def test_add_nav_exclude_idempotent(tmp_path):
    """Running twice produces the same file as running once."""
    p = tmp_path / "index.md"
    p.write_text(
        "---\n"
        "title: Power\n"
        "---\n"
        "Body.\n"
    )
    apm.add_nav_exclude_to_front_matter(p)
    after_first = p.read_text()
    apm.add_nav_exclude_to_front_matter(p)
    after_second = p.read_text()
    assert after_first == after_second
    assert after_first.count("nav_exclude: true") == 1


# ---------------------------------------------------------------------------
# list_release_tags
# ---------------------------------------------------------------------------

def test_list_release_tags_parses_gh_json(monkeypatch):
    import json
    import subprocess

    captured = {}

    def fake(args, *a, **kw):
        captured["args"] = list(args)
        return json.dumps([
            {"tagName": "v2.0"},
            {"tagName": "v1.5"},
            {"tagName": "v0.9"},
        ])

    monkeypatch.setattr(subprocess, "check_output", fake)
    result = apm.list_release_tags()
    assert result == ["v2.0", "v1.5", "v0.9"]
    assert captured["args"][:2] == ["gh", "release"]


def test_list_release_tags_empty(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: "[]")
    assert apm.list_release_tags() == []


# ---------------------------------------------------------------------------
# existing_archive_path
# ---------------------------------------------------------------------------

def test_existing_archive_path_present(tmp_path):
    consumer_dir = tmp_path / "HW-Module-PowerBoard"
    (consumer_dir / "v1").mkdir(parents=True)
    assert apm.existing_archive_path(consumer_dir, previous_major=1) is True


def test_existing_archive_path_absent(tmp_path):
    consumer_dir = tmp_path / "HW-Module-PowerBoard"
    consumer_dir.mkdir(parents=True)
    assert apm.existing_archive_path(consumer_dir, previous_major=1) is False


def test_existing_archive_path_consumer_dir_missing(tmp_path):
    """If consumer dir doesn't exist at all yet (truly first deploy),
    the check just returns False — caller handles 'no docs to archive'."""
    missing = tmp_path / "does-not-exist"
    assert apm.existing_archive_path(missing, previous_major=1) is False


# ---------------------------------------------------------------------------
# rewrite_archived_markdown
# ---------------------------------------------------------------------------

def test_rewrite_archived_markdown_multiple_files(tmp_path):
    archive = tmp_path / "v1"
    archive.mkdir()
    (archive / "index.md").write_text("---\ntitle: A\n---\nbody A\n")
    (archive / "extra.md").write_text("---\ntitle: B\n---\nbody B\n")
    (archive / "nested" / "sub.md").parent.mkdir()
    (archive / "nested" / "sub.md").write_text("---\ntitle: C\n---\nbody C\n")
    # Non-markdown file should be ignored
    (archive / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    modified_count = apm.rewrite_archived_markdown(archive)
    assert modified_count == 3
    assert "nav_exclude: true" in (archive / "index.md").read_text()
    assert "nav_exclude: true" in (archive / "extra.md").read_text()
    assert "nav_exclude: true" in (archive / "nested" / "sub.md").read_text()
    # PNG unchanged
    assert (archive / "image.png").read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_rewrite_archived_markdown_skips_already_excluded(tmp_path):
    archive = tmp_path / "v1"
    archive.mkdir()
    (archive / "a.md").write_text("---\ntitle: A\nnav_exclude: true\n---\nbody\n")
    (archive / "b.md").write_text("---\ntitle: B\n---\nbody\n")
    modified_count = apm.rewrite_archived_markdown(archive)
    assert modified_count == 1


def test_rewrite_archived_markdown_no_md_files(tmp_path):
    archive = tmp_path / "v1"
    archive.mkdir()
    (archive / "image.png").write_bytes(b"\x89PNG")
    assert apm.rewrite_archived_markdown(archive) == 0


# ---------------------------------------------------------------------------
# snapshot_consumer_dir
# ---------------------------------------------------------------------------

def test_snapshot_copies_top_level_files(tmp_path):
    src = tmp_path / "HW-Module-PowerBoard"
    src.mkdir()
    (src / "index.md").write_text("---\ntitle: Power\n---\nbody\n")
    (src / "schematic.pdf").write_bytes(b"%PDF-1.4 fake\n")
    dst = src / "v1"

    apm.snapshot_consumer_dir(src, dst)

    assert (dst / "index.md").read_text() == "---\ntitle: Power\n---\nbody\n"
    assert (dst / "schematic.pdf").read_bytes() == b"%PDF-1.4 fake\n"


def test_snapshot_excludes_existing_v_archive_subdirs(tmp_path):
    """Older archives must NOT end up nested inside the new archive
    (which would produce /v2/v1/, /v3/v2/v1/, …)."""
    src = tmp_path / "HW-Module-PowerBoard"
    src.mkdir()
    (src / "index.md").write_text("live\n")
    # An existing /v0/ archive from a long-ago Major bump
    (src / "v0").mkdir()
    (src / "v0" / "index.md").write_text("ancient\n")
    dst = src / "v1"

    apm.snapshot_consumer_dir(src, dst)

    assert (dst / "index.md").read_text() == "live\n"
    # v0 NOT copied into the new v1 archive
    assert not (dst / "v0").exists()


def test_snapshot_copies_nested_dirs(tmp_path):
    src = tmp_path / "HW-Module-PowerBoard"
    (src / "JLCPCB").mkdir(parents=True)
    (src / "index.md").write_text("live\n")
    (src / "JLCPCB" / "bom.csv").write_text("part,qty\n")
    dst = src / "v1"

    apm.snapshot_consumer_dir(src, dst)

    assert (dst / "JLCPCB" / "bom.csv").read_text() == "part,qty\n"


def test_snapshot_excludes_any_v_prefixed_subdir(tmp_path):
    """Pattern is `v[0-9]*`, not literal `v0/v1/v2`. v10 must also be
    excluded for forward-compat with double-digit majors."""
    src = tmp_path / "HW-Module-PowerBoard"
    src.mkdir()
    (src / "index.md").write_text("live\n")
    (src / "v0").mkdir()
    (src / "v0" / "x.txt").write_text("v0 archive\n")
    (src / "v10").mkdir()
    (src / "v10" / "x.txt").write_text("v10 archive\n")
    dst = src / "v11"

    apm.snapshot_consumer_dir(src, dst)

    assert not (dst / "v0").exists()
    assert not (dst / "v10").exists()


# ---------------------------------------------------------------------------
# main — end-to-end orchestration (subprocess stubbed)
# ---------------------------------------------------------------------------

def _stub_subprocess_for_main(monkeypatch, tags, target_repo_files=None):
    """Stub subprocess.check_output and check_call for main().

    `tags` is the gh release list payload.
    `target_repo_files` is a dict {relative_path: content} for files in
    the cloned target repo's consumer directory. If None, the consumer
    directory is created empty inside the fake clone.
    """
    import json
    import shutil
    import subprocess

    payload = json.dumps([{"tagName": t} for t in tags])
    clone_dir_holder = {}

    def fake_check_output(args, *a, **kw):
        if args[:2] == ["gh", "release"]:
            return payload
        raise AssertionError(f"unexpected check_output: {args}")

    def fake_check_call(args, *a, **kw):
        # Recognise: git clone, rsync, git -C cwd add/commit/push
        if args[0] == "git" and args[1] == "clone":
            # last arg is the destination dir
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(parents=True, exist_ok=True)  # fake git dir
            clone_dir_holder["path"] = dest
            consumer = dest / "docs" / "remote-station" / "hardware" / "HW-Module-PowerBoard"
            consumer.mkdir(parents=True, exist_ok=True)
            for rel, content in (target_repo_files or {}).items():
                f = consumer / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    f.write_bytes(content)
                else:
                    f.write_text(content)
            return 0
        if args[0] == "rsync":
            # Re-use real rsync for the test — it's a small tmp dir
            return subprocess.run(list(args), check=True).returncode
        if args[0] == "git" and "-C" in args:
            # Treat all in-clone git ops as success
            return 0
        raise AssertionError(f"unexpected check_call: {args}")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(subprocess, "check_call", fake_check_call)
    # Preserve the clone dir so test assertions can inspect it after main()
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **kw: None)
    return clone_dir_holder


def _set_env(monkeypatch, current_tag, repo_name="HW-Module-PowerBoard"):
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REF_NAME", current_tag)
    monkeypatch.setenv("REPO_NAME", repo_name)
    monkeypatch.setenv("TARGET_REPO", "OE5XRX/OE5XRX.github.io")


def test_main_first_release_noop(monkeypatch, capsys):
    # Only the current tag exists — no previous release to archive
    _stub_subprocess_for_main(monkeypatch, tags=["v1.0"])
    _set_env(monkeypatch, current_tag="v1.0")
    rc = apm.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "noop-first-release" in captured.out
    # No clone, no rsync was attempted (the stub would have AssertionError'd
    # on unexpected calls; check_output only got gh release list)


def test_main_same_major_noop(monkeypatch, capsys):
    _stub_subprocess_for_main(monkeypatch, tags=["v1.0", "v1.1"])
    _set_env(monkeypatch, current_tag="v1.1")
    rc = apm.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "noop-same-major" in captured.out


def test_main_downgrade_errors(monkeypatch, capsys):
    _stub_subprocess_for_main(monkeypatch, tags=["v1.0", "v2.0"])
    _set_env(monkeypatch, current_tag="v1.0")
    rc = apm.main()
    captured = capsys.readouterr()
    assert rc != 0
    assert "error-downgrade" in captured.out or "::error::" in captured.out


def test_main_major_bump_archives(monkeypatch, capsys):
    holder = _stub_subprocess_for_main(
        monkeypatch,
        tags=["v1.0", "v1.5", "v2.0"],
        target_repo_files={
            "index.md": "---\ntitle: Power\n---\nlive content\n",
            "schematic.pdf": b"%PDF-fake\n",
        },
    )
    _set_env(monkeypatch, current_tag="v2.0")
    rc = apm.main()
    captured = capsys.readouterr()
    assert rc == 0
    # Archive directory exists and has the snapshotted content
    archive = holder["path"] / "docs/remote-station/hardware/HW-Module-PowerBoard/v1"
    assert archive.is_dir()
    assert "nav_exclude: true" in (archive / "index.md").read_text()
    # PDF copied verbatim
    assert (archive / "schematic.pdf").read_bytes() == b"%PDF-fake\n"
    assert "archive" in captured.out


def test_main_existing_archive_guard(monkeypatch, capsys):
    holder = _stub_subprocess_for_main(
        monkeypatch,
        tags=["v1.0", "v2.0"],
        target_repo_files={
            "index.md": "live\n",
            "v1/index.md": "already archived previously\n",
        },
    )
    _set_env(monkeypatch, current_tag="v2.0")
    rc = apm.main()
    captured = capsys.readouterr()
    assert rc == 0
    # Existing archive was NOT overwritten
    archive_index = holder["path"] / "docs/remote-station/hardware/HW-Module-PowerBoard/v1/index.md"
    assert archive_index.read_text() == "already archived previously\n"
    assert "exists" in captured.out.lower() or "skip" in captured.out.lower()
