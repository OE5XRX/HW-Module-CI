"""
dry_run.py – Decision-recording layer for bom_export.py's --dry-run mode.

Side-effect-free: instead of calling Part.create / BomItem.create /
SupplierPart.create, the bom_export and part_manager code paths call
reporter.record(...) and continue.  At the end of the run, print_report()
emits a Markdown-ish summary on stdout (or any IO).
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import IO, Literal

Action = Literal["CREATE", "REUSE", "SKIP", "FAIL"]

# Field widths so categories' "Would ACTION:  target" lines align in the output.
_ACTION_WIDTH = 7


@dataclass
class DryRunRecord:
    action: Action
    category: str
    target: str
    detail: str = ""


class DryRunReporter:
    """Collects decisions during a --dry-run; prints a grouped summary."""

    def __init__(self) -> None:
        self.records: list[DryRunRecord] = []

    def record(
        self,
        action: Action,
        category: str,
        target: str,
        detail: str = "",
    ) -> None:
        self.records.append(DryRunRecord(action, category, target, detail))

    def has_failures(self) -> bool:
        return any(r.action == "FAIL" for r in self.records)

    def print_report(self, *, file: IO[str] = sys.stdout, title: str = "") -> None:
        if title:
            print(f"DRY-RUN: {title}\n", file=file)

        # Group records by category, preserving first-seen order.
        groups: OrderedDict[str, list[DryRunRecord]] = OrderedDict()
        for rec in self.records:
            groups.setdefault(rec.category, []).append(rec)

        for category, recs in groups.items():
            print(f"{category}:", file=file)
            for rec in recs:
                detail_suffix = f" — {rec.detail}" if rec.detail else ""
                # action_str padded so "Would CREATE:" and "Would REUSE:" align.
                action_padded = (f"Would {rec.action}:").ljust(_ACTION_WIDTH + 7)
                print(f"  {action_padded}{rec.target}{detail_suffix}", file=file)
            print(file=file)

        # Summary: count per action.
        counts = {"CREATE": 0, "REUSE": 0, "SKIP": 0, "FAIL": 0}
        for rec in self.records:
            counts[rec.action] += 1
        summary_bits = [
            f"{counts['CREATE']} CREATE",
            f"{counts['REUSE']} REUSE",
            f"{counts['SKIP']} SKIP",
            f"{counts['FAIL']} would-fail",
        ]
        print("Summary: " + ", ".join(summary_bits), file=file)

        if self.has_failures():
            print("EXIT: 1 (would-fail present)", file=file)
        else:
            print("EXIT: 0", file=file)
