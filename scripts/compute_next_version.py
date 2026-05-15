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

import os
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


def decide_bump(files: List[str]) -> str:
    """Return 'major', 'minor', or 'none' based on which files changed.

    *.kicad_pcb (anywhere in the tree) wins over *.kicad_sch — a PCB
    change always implies a Gerber set change, even if a schematic
    change rode along on the same commit.
    """
    if any(f.endswith(".kicad_pcb") for f in files):
        return "major"
    if any(f.endswith(".kicad_sch") for f in files):
        return "minor"
    return "none"


def write_outputs(**kwargs: str) -> None:
    """Append KEY=VALUE pairs to $GITHUB_OUTPUT, or stdout fallback.

    The stdout fallback exists so the script is debuggable in a local
    shell — running it without GITHUB_OUTPUT set prints the same
    KEY=VALUE lines and doesn't silently swallow them.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        for k, v in kwargs.items():
            print(f"{k}={v}")
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kwargs.items():
            f.write(f"{k}={v}\n")
