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
"""
from __future__ import annotations

import json
import re
import subprocess
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
