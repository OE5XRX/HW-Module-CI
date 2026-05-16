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

import re
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
