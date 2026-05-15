#!/usr/bin/env python3
"""
Compute the next semantic version tag for a HW-Module-* repo.

See docs at oe5xrx.org/docs/remote-station/hardware/versioning/ for the
full versioning model. In short:

  *.kicad_pcb changed  -> Major bump (v<X+1>.0)
  *.kicad_sch changed  -> Minor bump (v<X>.<Y+1>)
  Else                 -> No bump

If no managed release (v<MAJOR>.<MINOR> with MAJOR>=1) exists yet, the
script exits 2 with a bootstrap hint. The maintainer must create v1.0
manually before the auto-release can take over.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

TAG_RE = re.compile(r"^v(\d+)\.(\d+)$")


def find_baseline(tags: List[str]) -> Optional[Tuple[int, int, str]]:
    """Return (major, minor, tag) of the highest managed release, or None.

    A managed release is one whose tag matches v<MAJOR>.<MINOR> exactly,
    with MAJOR >= 1. Pre-baseline tags (v0.x) and malformed entries
    (release/v1.2, v1.0-rc1, ...) are filtered out — the Auto-Release
    workflow only counts from a tag it could itself have created.
    """
    candidates: List[Tuple[int, int, str]] = []
    for tag in tags:
        m = TAG_RE.match(tag)
        if not m:
            continue
        major = int(m.group(1))
        minor = int(m.group(2))
        if major < 1:
            continue
        candidates.append((major, minor, tag))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0]
