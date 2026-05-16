#!/usr/bin/env python3
"""
Before a Major-bump release deploys, snapshot the current /<repo>/
content on OE5XRX.github.io into /<repo>/v<old-major>/ so the previous
Major's docs remain reachable.

Decision logic uses the consumer-repo's release list (gh release list)
to find the highest managed v<MAJOR>.<MINOR> tag *other than* the one
currently being released. If that previous Major is lower than the
current one → archive. Otherwise → noop.

The archive itself is a git-clone + rsync + nav_exclude rewrite +
commit + push on OE5XRX.github.io, authenticated via DEPLOY_GH_TOKEN.

Back-port limitation:
The "previous release" is the highest-versioned tag other than the
current one, NOT the chronologically previous release. That means
publishing v1.6 (a back-port to an older Major) AFTER v2.0 has
already shipped would be flagged as "error-downgrade" and abort
the release pipeline. OE5XRX's current release cadence has at most
one Major active at a time, so this is intentional. If back-ports
ever become a requirement, switch find_previous_release() to a
chronological-previous lookup via `gh release list --created-at`
ordering.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

TAG_RE = re.compile(r"^v(\d+)\.(\d+)$")


def parse_tag(tag: str) -> Optional[Tuple[int, int]]:
    """Return (major, minor) for a managed-release tag, else None.

    A managed-release tag matches v<MAJOR>.<MINOR> exactly with MAJOR ≥ 1.
    Pre-baseline (v0.x), malformed (release/v1.2, v1.0-rc1, bare 1.0)
    and empty strings all return None.
    """
    m = TAG_RE.match(tag or "")
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    if major < 1:
        return None
    return major, minor


def find_previous_release(
    tags: List[str], current_tag: str
) -> Optional[Tuple[int, int, str]]:
    """Return (major, minor, tag) of the highest managed release *other
    than* current_tag, or None if no such release exists.

    Same parsing rules as parse_tag: only v<MAJOR>.<MINOR> with MAJOR ≥ 1.
    """
    candidates: List[Tuple[int, int, str]] = []
    for tag in tags:
        if tag == current_tag:
            continue
        parsed = parse_tag(tag)
        if parsed is None:
            continue
        major, minor = parsed
        candidates.append((major, minor, tag))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0]


def decide_archive(
    current_major: int, previous_major: Optional[int]
) -> str:
    """Return one of 'archive', 'noop-first-release', 'noop-same-major',
    'error-downgrade' based on whether a Major-bump archive is needed.

    The four outcomes:
      previous=None     -> noop-first-release  (no docs to archive yet)
      previous==current -> noop-same-major     (Minor bump; docs stay in root)
      previous<current  -> archive             (Major bump; snapshot needed)
      previous>current  -> error-downgrade     (release tag went backwards;
                                                maintainer mistake)
    """
    if previous_major is None:
        return "noop-first-release"
    if previous_major == current_major:
        return "noop-same-major"
    if previous_major < current_major:
        return "archive"
    return "error-downgrade"


def add_nav_exclude_to_front_matter(path: Path) -> bool:
    """Insert `nav_exclude: true` into the leading YAML front matter.

    Returns True if the file was modified, False if no front-matter
    was found OR nav_exclude was already present. Idempotent: running
    twice does not duplicate the line.

    The file is expected to start with:

        ---
        key: value
        ...
        ---

    Only the FIRST `---\n…\n---\n` block at the top of the file
    is treated as front matter. Any `---` appearing later (e.g. a
    markdown horizontal rule) is left alone.
    """
    txt = path.read_text(encoding="utf-8")
    if not txt.startswith("---\n"):
        return False
    # Find the closing --- of the front-matter block. Search starts at
    # the first character AFTER the opening "---\n" so we don't match
    # the opening as the closing.
    closing = txt.find("\n---\n", 4)
    if closing == -1:
        return False
    fm_body = txt[4:closing]  # the content between the --- markers
    if "nav_exclude:" in fm_body:
        return False
    new_fm_body = fm_body.rstrip("\n") + "\nnav_exclude: true"
    new_txt = "---\n" + new_fm_body + txt[closing:]
    path.write_text(new_txt, encoding="utf-8")
    return True


def list_release_tags() -> List[str]:
    """Return all release tag names from the current repo via the gh CLI.

    Runs `gh release list --json tagName --limit 100`. The current
    working directory must be inside a git checkout of the consumer
    repo so gh infers the right remote.
    """
    out = subprocess.check_output(
        ["gh", "release", "list", "--json", "tagName", "--limit", "100"],
        text=True,
    )
    return [entry["tagName"] for entry in json.loads(out)]


def existing_archive_path(consumer_dir: Path, previous_major: int) -> bool:
    """Return True if `<consumer_dir>/v<previous_major>/` already exists.

    Used as an existence guard before snapshotting — prevents accidental
    re-trigger or re-release from clobbering an existing archive.
    """
    return (consumer_dir / f"v{previous_major}").is_dir()


def rewrite_archived_markdown(archive_dir: Path) -> int:
    """Walk archive_dir recursively, apply add_nav_exclude_to_front_matter
    to every *.md file found, return count of files actually modified.
    """
    modified = 0
    for md in archive_dir.rglob("*.md"):
        if not md.is_file():
            continue
        if add_nav_exclude_to_front_matter(md):
            modified += 1
    return modified


def snapshot_consumer_dir(src: Path, dst: Path) -> None:
    """Copy `src/` contents into `dst/`, EXCLUDING any `v<digits>`
    subdirs (which are old archives from earlier Major bumps).

    Uses rsync with `--exclude='v[0-9]*'` so old archives don't end
    up nested inside the new archive (`v2/v1/v0/...`).

    `dst` must not exist beforehand — the caller checks this via
    existing_archive_path() and refuses to overwrite.

    Implementation note — self-recursion coupling:
    The caller passes `dst = src / "v<prev_major>"` (the archive lives
    INSIDE the source tree). rsync would otherwise recurse into the
    freshly-created `dst` dir and copy the snapshot into itself
    indefinitely. The `--exclude=v[0-9]*` filter is what prevents that
    — it MUST match the name the caller chose for `dst`. If anyone
    ever loosens the exclude pattern, also reconsider the src/dst
    layout, or rsync will copy `dst` into itself.
    """
    dst.mkdir(parents=True, exist_ok=False)
    subprocess.check_call(
        [
            "rsync",
            "-a",
            "--exclude=v[0-9]*",
            f"{src}/",
            f"{dst}/",
        ]
    )


CONSUMER_DOCS_BASE = Path("docs/remote-station/hardware")
EXIT_DOWNGRADE = 2


def _git(args: List[str], cwd: Path) -> None:
    """Run a git command inside `cwd` via subprocess.check_call."""
    subprocess.check_call(["git", "-C", str(cwd)] + args)


def main() -> int:
    """Entry point. Reads env, computes the decision, performs the
    archive if needed, and exits 0 on success/noop or non-zero on
    error.
    """
    token = os.environ["GH_TOKEN"]
    current_tag = os.environ["GITHUB_REF_NAME"]
    repo_name = os.environ["REPO_NAME"]
    target_repo = os.environ.get("TARGET_REPO", "OE5XRX/OE5XRX.github.io")

    parsed_current = parse_tag(current_tag)
    if parsed_current is None:
        print(
            f"::error::Current tag '{current_tag}' is not a managed "
            "release (v<MAJOR>.<MINOR> with MAJOR>=1). Skipping archive."
        )
        return 0  # Don't block the deploy — release-docs handles odd tags
    current_major, _current_minor = parsed_current

    tags = list_release_tags()
    prev = find_previous_release(tags, current_tag=current_tag)
    prev_major = prev[0] if prev else None
    decision = decide_archive(current_major, prev_major)

    if decision == "noop-first-release":
        print(f"::notice::archive skip: noop-first-release (current={current_tag})")
        return 0
    if decision == "noop-same-major":
        print(
            f"::notice::archive skip: noop-same-major "
            f"(current={current_tag}, previous={prev[2] if prev else 'None'})"
        )
        return 0
    if decision == "error-downgrade":
        print(
            f"::error::archive abort: current tag {current_tag} has lower "
            f"Major than previous release {prev[2] if prev else 'None'}. "
            "This usually means a maintainer mistake — investigate before retry."
        )
        return EXIT_DOWNGRADE
    # decision == "archive"
    assert prev is not None and prev_major is not None
    prev_tag = prev[2]
    print(
        f"::notice::archive triggered: previous={prev_tag} (Major={prev_major}) "
        f"current={current_tag} (Major={current_major})"
    )

    tmpdir = Path(tempfile.mkdtemp(prefix="hwci-archive-"))
    try:
        clone_dst = tmpdir / "target"
        subprocess.check_call(
            [
                "git",
                "clone",
                "--depth",
                "1",
                f"https://x-access-token:{token}@github.com/{target_repo}.git",
                str(clone_dst),
            ]
        )
        consumer_dir = clone_dst / CONSUMER_DOCS_BASE / repo_name
        if not consumer_dir.is_dir():
            print(
                f"::notice::archive skip: consumer dir "
                f"{CONSUMER_DOCS_BASE / repo_name} does not exist in "
                f"target repo yet — nothing to archive."
            )
            return 0

        if existing_archive_path(consumer_dir, prev_major):
            print(
                f"::notice::archive skip: "
                f"{consumer_dir / f'v{prev_major}'} already exists "
                "— preserving existing snapshot."
            )
            return 0

        archive_dst = consumer_dir / f"v{prev_major}"
        snapshot_consumer_dir(consumer_dir, archive_dst)
        modified = rewrite_archived_markdown(archive_dst)
        print(
            f"::notice::archive snapshot complete: "
            f"{archive_dst.relative_to(clone_dst)} "
            f"({modified} md files marked nav_exclude)"
        )

        _git(["config", "user.name", "OE5XRX archive bot"], cwd=clone_dst)
        _git(
            ["config", "user.email", "noreply@oe5xrx.org"],
            cwd=clone_dst,
        )
        _git(["add", str(archive_dst.relative_to(clone_dst))], cwd=clone_dst)
        _git(
            [
                "commit",
                "-m",
                f"archive: snapshot {repo_name} at v{prev_major} "
                f"before {current_tag} deploy",
            ],
            cwd=clone_dst,
        )
        _git(["push", "origin", "main"], cwd=clone_dst)
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
