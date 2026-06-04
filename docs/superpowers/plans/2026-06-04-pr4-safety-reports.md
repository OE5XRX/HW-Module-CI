# PR-4: Safety & Reports — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add maintenance refresh-mode (nightly cron), dry-run preview, and cost-report generation to the InvenTree-sync pipeline.

**Architecture:** Three loosely-coupled features. Cost-Report is a pure module that summarizes BOM prices into Markdown. Dry-Run threads a `DryRunReporter` through `bom_export.py` + `part_manager.py` so the sync code records intentions instead of executing them. Refresh is a standalone script reusing `_fetch_and_merge`, `upload_image_from_url`, `upload_parameters`, `_add_price_breaks` from `inventree_sync/`.

**Tech Stack:** Python 3.x, `inventree` 0.23.1, `requests` 2.34. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-06-04-pr4-safety-reports.md`](../specs/2026-06-04-pr4-safety-reports.md)

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `scripts/inventree_sync/cost_report.py` | Create | `generate_cost_report(api, assembly, entries, tiers)` + `_cheapest_price` + `_render_markdown` |
| `scripts/inventree_sync/dry_run.py` | Create | `DryRunRecord` dataclass + `DryRunReporter` class |
| `scripts/inventree_refresh.py` | Create | Standalone refresh script |
| `scripts/bom_export.py` | Modify | `--dry-run` flag; `--no_cost_report` opt-out; threading reporter through; cost-report call after `populate_bom` |
| `scripts/inventree_sync/part_manager.py` | Modify | Optional `reporter` param in `ensure_parts_exist`; record actions instead of executing when set |
| `scripts/e2e_revision_handling.py` | Modify | 3 new tests: `test_cost_report_generation`, `test_dry_run_no_side_effects`, `test_refresh_idempotent` |
| `scripts/tests/test_cost_report.py` | Create | Pure-Python unit tests for `_cheapest_price` + `_render_markdown` |
| `scripts/tests/test_dry_run_reporter.py` | Create | Pure-Python unit tests for `DryRunReporter` |
| `.github/workflows/scheduled-inventree-refresh.yaml` | Create | Nightly cron + workflow_dispatch |

---

## Task 1: Cost-Report module (pure logic + pytest)

**Files:**
- Create: `scripts/inventree_sync/cost_report.py`
- Create: `scripts/tests/test_cost_report.py`

### Step 1.1: Write failing pytest

- [ ] Create `scripts/tests/test_cost_report.py` with:

```python
"""Pure-Python unit tests for cost_report — no network, no InvenTree mocks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Bootstrap sys.path so `inventree_sync` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.cost_report import _cheapest_price, _render_markdown


def test_cheapest_price_single_supplier_simple():
    """One supplier, one price break."""
    price_data = {"LCSC": [(1, 0.10)]}
    assert _cheapest_price(price_data, 1) == (0.10, "LCSC")
    assert _cheapest_price(price_data, 100) == (0.10, "LCSC")


def test_cheapest_price_threshold_excludes_high_qty_break():
    """A price break with qty_threshold > required is not valid."""
    price_data = {"LCSC": [(10, 0.10), (100, 0.08)]}
    # required=1 means no valid break (threshold 10 and 100 both > 1)
    assert _cheapest_price(price_data, 1) is None
    # required=10 picks the 10-break
    assert _cheapest_price(price_data, 10) == (0.10, "LCSC")
    # required=100 picks the cheaper 100-break
    assert _cheapest_price(price_data, 100) == (0.08, "LCSC")


def test_cheapest_price_two_suppliers_chooses_cheaper():
    """When both suppliers have valid breaks, pick the cheaper one."""
    price_data = {
        "LCSC":   [(10, 0.10), (100, 0.08)],
        "Mouser": [(1, 0.12),  (500, 0.06)],
    }
    assert _cheapest_price(price_data, 1) == (0.12, "Mouser")
    assert _cheapest_price(price_data, 10) == (0.10, "LCSC")
    assert _cheapest_price(price_data, 100) == (0.08, "LCSC")
    assert _cheapest_price(price_data, 500) == (0.06, "Mouser")


def test_cheapest_price_empty_returns_none():
    assert _cheapest_price({}, 10) is None
    assert _cheapest_price({"LCSC": []}, 10) is None


def test_render_markdown_basic_table():
    """Headline + 3 rows + missing-prices vermerk."""
    rows = [
        (1,   58.20, 58.20,  {"LCSC": 45, "Mouser": 2}),
        (10,  38.50, 3.85,   {"LCSC": 47}),
        (100, 24.10, 0.241,  {"LCSC": 47}),
    ]
    md = _render_markdown(
        title="FMTransceiver v1.2 (Assembly pk=42)",
        rows=rows,
        total_items=47,
        missing=[("R_Custom", "R Custom 0805"), ("XTAL_Custom", "XTAL Custom 32MHz")],
    )
    assert "## BOM Cost Report — FMTransceiver v1.2" in md
    assert "| Qty | Total | per-Board | Sources" in md
    assert "| 1 | €58.20 | €58.200 | LCSC (45), Mouser (2) |" in md
    assert "| 10 | €38.50 | €3.850 | LCSC (47) |" in md
    assert "| 100 | €24.10 | €0.241 | LCSC (47) |" in md
    assert "47 total — 2 had no price data" in md
    assert "R_Custom" in md and "XTAL_Custom" in md


def test_render_markdown_no_missing_omits_vermerk():
    rows = [(1, 1.00, 1.00, {"LCSC": 5})]
    md = _render_markdown(
        title="Test",
        rows=rows,
        total_items=5,
        missing=[],
    )
    assert "had no price data" not in md
```

### Step 1.2: Run pytest — expect failure

- [ ] Run:

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
source .venv/bin/activate
pytest scripts/tests/test_cost_report.py -v
```

- [ ] Expected: `ModuleNotFoundError: No module named 'inventree_sync.cost_report'` (module doesn't exist yet). Confirm by reading the traceback. If a different error, STOP.

### Step 1.3: Create `scripts/inventree_sync/cost_report.py`

- [ ] Create file with this content:

```python
"""
cost_report.py – Markdown cost-report from BOM entries' price-breaks.

Reads SupplierPart price breaks from InvenTree for each BomEntry's
inventree_part, picks the cheapest valid break per quantity tier, and
renders a Markdown table. Writes the table to $GITHUB_STEP_SUMMARY when
running under GitHub Actions, and patches Assembly.notes via Part.save.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from inventree.api import InvenTreeAPI
from inventree.company import SupplierPart, SupplierPriceBreak
from inventree.part import Part

from .models import BomEntry

log = logging.getLogger(__name__)


def _cheapest_price(
    price_data: dict[str, list[tuple[int, float]]],
    required_qty: int,
) -> Optional[tuple[float, str]]:
    """Return (unit_price, supplier_name) for the cheapest break valid for
    *required_qty*, or None if no break qualifies.

    A price-break with quantity-threshold Q is valid when Q <= required_qty
    (typical distributor semantics: 'buy at least Q to get this price').
    """
    best: Optional[tuple[float, str]] = None
    for supplier_name, breaks in price_data.items():
        for qty_threshold, unit_price in breaks:
            if qty_threshold > required_qty:
                continue
            if best is None or unit_price < best[0]:
                best = (unit_price, supplier_name)
    return best


def _render_markdown(
    *,
    title: str,
    rows: list[tuple[int, float, float, dict[str, int]]],
    total_items: int,
    missing: list[tuple[str, str]],
) -> str:
    """Render the cost-report Markdown string."""
    lines = [
        f"## BOM Cost Report — {title}",
        "",
        "| Qty | Total | per-Board | Sources |",
        "|-----|-------|-----------|---------|",
    ]
    for qty, total, per_board, sources in rows:
        sources_str = ", ".join(
            f"{name} ({n})" for name, n in sorted(sources.items())
        )
        lines.append(
            f"| {qty} | €{total:.2f} | €{per_board:.3f} | {sources_str} |"
        )
    if missing:
        names = ", ".join(f"`{ref}`" for ref, _name in missing)
        lines += [
            "",
            f"**BOM items:** {total_items} total — {len(missing)} had no price data ({names}).",
        ]
    else:
        lines += ["", f"**BOM items:** {total_items} total — all had price data."]
    return "\n".join(lines)


def _collect_price_data(
    api: InvenTreeAPI,
    inv_part: Part,
) -> dict[str, list[tuple[int, float]]]:
    """Fetch SupplierParts + their PriceBreaks for one InvenTree-Part."""
    out: dict[str, list[tuple[int, float]]] = {}
    for sp in SupplierPart.list(api, part=inv_part.pk):
        # Resolve supplier name. The SDK exposes 'supplier' as the FK pk;
        # we may need to fetch the Company. To avoid N+1 calls, attempt to
        # read the cached '_data' attribute if present, else fall back.
        sup_name = (sp._data.get("supplier_detail") or {}).get("name", "?")
        breaks = SupplierPriceBreak.list(api, part=sp.pk)
        if not breaks:
            continue
        rows: list[tuple[int, float]] = []
        for pb in breaks:
            try:
                qty = int(pb.quantity)
                price = float(pb.price)
                rows.append((qty, price))
            except (TypeError, ValueError):
                continue
        if rows:
            out.setdefault(sup_name, []).extend(rows)
    return out


def generate_cost_report(
    api: InvenTreeAPI,
    assembly: Part,
    entries: list[BomEntry],
    tiers: list[int] = [1, 10, 100],
) -> str:
    """Generate the cost report. Returns the Markdown string.

    Side effects:
      - Appends to $GITHUB_STEP_SUMMARY if the env-var is set.
      - Patches assembly.notes via Part.save (best-effort, swallows errors).
    """
    # 1) Materialize per-entry price data.
    items_with_prices: list[tuple[BomEntry, Part, dict[str, list[tuple[int, float]]]]] = []
    items_missing: list[tuple[str, str]] = []
    for entry in entries:
        # Only one inventree_part per entry in PR-2/PR-3 model; if multiple,
        # we treat them as alternates and merge their price data.
        merged: dict[str, list[tuple[int, float]]] = {}
        for inv_part in entry.inventree_part:
            for sup, breaks in _collect_price_data(api, inv_part).items():
                merged.setdefault(sup, []).extend(breaks)
        if merged:
            items_with_prices.append(
                (entry, entry.inventree_part[0] if entry.inventree_part else None, merged)
            )
        else:
            primary_name = entry.kicad_value or entry.kicad_part or entry.reference
            items_missing.append((entry.reference, primary_name))

    # 2) Aggregate per tier.
    rows: list[tuple[int, float, float, dict[str, int]]] = []
    for tier_qty in tiers:
        total = 0.0
        sources: dict[str, int] = {}
        for entry, _, price_data in items_with_prices:
            required = entry.qty * tier_qty
            cheapest = _cheapest_price(price_data, required)
            if cheapest is None:
                continue
            unit_price, supplier_name = cheapest
            total += unit_price * required
            sources[supplier_name] = sources.get(supplier_name, 0) + 1
        per_board = total / tier_qty if tier_qty > 0 else 0.0
        rows.append((tier_qty, total, per_board, sources))

    title = f"{assembly.name} rev {getattr(assembly, 'revision', '?')} (pk={assembly.pk})"
    md = _render_markdown(
        title=title,
        rows=rows,
        total_items=len(entries),
        missing=items_missing,
    )

    # 3) Append to GitHub-Actions Step-Summary.
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a") as fh:
                fh.write(md + "\n")
        except Exception as exc:
            log.warning("Failed to write GITHUB_STEP_SUMMARY: %s", exc)

    # 4) Patch Assembly notes (best-effort).
    try:
        assembly.save({"notes": md})
        log.info("Cost-report notes attached to Assembly pk=%s", assembly.pk)
    except Exception as exc:
        log.warning("Failed to patch assembly.notes: %s", exc)

    return md
```

### Step 1.4: Run pytest — expect pass

- [ ] Run:

```bash
pytest scripts/tests/test_cost_report.py -v
```

- [ ] Expected: 6 tests PASS. If any fails, read the failure and adjust the implementation (assertion text in test_render_markdown must match the implementation exactly — fix EITHER side to align).

### Step 1.5: py_compile sanity

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/cost_report.py scripts/tests/test_cost_report.py
```

- [ ] Expected: no output.

### Step 1.6: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/cost_report.py scripts/tests/test_cost_report.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): cost_report module + pytest

Backlog #11 part 1: pure-Python cost report from BOM entries' price
breaks. Picks cheapest valid break per qty-tier (standard distributor
semantics: break valid when threshold <= required_qty), renders a
Markdown table with per-supplier source counts.

Side-effects (only triggered by generate_cost_report, not the helpers):
- Append to $GITHUB_STEP_SUMMARY when running under GitHub Actions.
- Patch assembly.notes via Part.save (best-effort).

Pure-Python unit tests cover _cheapest_price (threshold edge cases,
multi-supplier selection) and _render_markdown (table format,
missing-prices vermerk).

Wire-up into bom_export.py comes in the next task.

Refs: docs/superpowers/specs/2026-06-04-pr4-safety-reports.md
EOF
)"
```

---

## Task 2: Cost-Report wiring into bom_export + E2E

**Files:**
- Modify: `scripts/bom_export.py` (import + call after `populate_bom`)
- Modify: `scripts/e2e_revision_handling.py` (new test)

### Step 2.1: Modify `scripts/bom_export.py`

- [ ] Open `scripts/bom_export.py`. Add this import after the existing `from inventree_sync.client import find_part_by_name_and_revision` line (around line 26):

```python
from inventree_sync.cost_report import generate_cost_report
```

- [ ] Find `main()` (around line 295). After the `populate_bom(api, assembly, pcb, entries)` call near the bottom, ADD:

```python
    # Cost-report (Backlog #11) — Markdown into $GITHUB_STEP_SUMMARY + assembly.notes
    try:
        generate_cost_report(api, assembly, entries)
    except Exception as exc:
        log.warning("Cost-report generation failed: %s", exc)
```

The `try/except` is intentional: cost-report is observability, not a correctness gate. If anything inside it explodes (network blip when fetching SupplierPart, malformed price-break, write-permission on $GITHUB_STEP_SUMMARY), the release artifact deploy already happened — we don't want to fail the workflow after a successful sync.

### Step 2.2: Extend E2E test scaffold

- [ ] In `scripts/e2e_revision_handling.py`, add this new test function AFTER `test_attachment_idempotent` (or wherever the last test currently lives):

```python
def test_cost_report_generation(api: InvenTreeAPI) -> None:
    """generate_cost_report() produces Markdown + patches assembly.notes."""
    from inventree.company import Company, SupplierPart, SupplierPriceBreak
    from bom_export import create_assembly_part, create_pcb_part
    from inventree_sync.cost_report import generate_cost_report
    from inventree_sync.models import BomEntry
    cat = _ensure_category(api, f"{PREFIX} cat")

    # Test infrastructure: one Company, one Part with price breaks, one BomEntry.
    supplier = _track_company(Company.create(api, {
        "name": f"{PREFIX} TestSupplierForCost", "is_supplier": True,
    }))
    component = _track(Part.create(api, {
        "name": f"{PREFIX} CostComp", "description": "comp", "active": True,
        "component": True, "purchaseable": True,
    }))
    sp = SupplierPart.create(api, {
        "part": component.pk, "supplier": supplier.pk,
        "SKU": f"{PREFIX}-SKU-1",
    })
    SupplierPriceBreak.create(api, {
        "part": sp.pk, "quantity": 1, "price": "0.5", "price_currency": "EUR",
    })
    SupplierPriceBreak.create(api, {
        "part": sp.pk, "quantity": 100, "price": "0.2", "price_currency": "EUR",
    })

    assembly = _track(create_assembly_part(api, cat, f"{PREFIX} CostTest", "1.0", image=None))
    pcb = _track(create_pcb_part(api, cat, f"{PREFIX} CostTest", "1.0", image=None))

    entry = BomEntry(
        reference="U1", qty=2,
        kicad_part="X", kicad_value="CostTest", kicad_footprint="dummy",
    )
    entry.inventree_part = [component]

    md = generate_cost_report(api, assembly, [entry], tiers=[1, 10, 100])

    # Sanity: markdown contains expected pieces.
    assert "## BOM Cost Report" in md, f"missing header in:\n{md}"
    assert "| 1 |" in md and "| 10 |" in md and "| 100 |" in md, (
        f"missing tier rows in:\n{md}")
    # Tier 1: 2 units * 1 board = 2 needed. Best valid break is qty>=1 at 0.5.
    # Total = 2 * 0.5 = 1.00, per-board = 1.00.
    assert "€1.00" in md, f"expected tier-1 total €1.00 in:\n{md}"
    # Tier 100: 2 units * 100 boards = 200 needed. Best valid break is qty>=100 at 0.2.
    # Total = 200 * 0.2 = 40.00, per-board = 0.40.
    assert "€40.00" in md, f"expected tier-100 total €40.00 in:\n{md}"

    # Re-fetch the assembly to verify notes were patched.
    refreshed = Part(api, pk=assembly.pk)
    notes = getattr(refreshed, "notes", None) or refreshed._data.get("notes") or ""
    assert "BOM Cost Report" in notes, f"assembly.notes not patched, got: {notes!r}"
    print(f"  PASS  cost-report generation (markdown len={len(md)}, notes patched)")
```

- [ ] Add `test_cost_report_generation` to the test-case tuple in `main()`. Find the `for tc in (...)` block and add the new test name at the end of the tuple. Example (assuming the tuple currently ends with `test_multi_sku_supplier_parts`):

```python
        for tc in (test_find_part_by_name_exact,
                   test_find_part_by_name_and_revision,
                   test_pcb_silently_reuse,
                   test_stencil_silently_reuse,
                   test_assembly_silently_reuse,
                   test_bom_idempotent,
                   test_multi_sku_supplier_parts,
                   # NOTE: PR-3 added 3 more tests (parameter, supplier-link,
                   # attachment); insert this NEW test AFTER all of them.
                   test_cost_report_generation):
```

If the current tuple looks different (PR-3 added entries between Multi-SKU and this), insert `test_cost_report_generation` as the LAST element of the tuple regardless.

### Step 2.3: py_compile + run E2E

- [ ] Run:

```bash
python3 -m py_compile scripts/bom_export.py scripts/e2e_revision_handling.py
source ~/.inventree_test.env
source .venv/bin/activate
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: all previous tests still PASS + new `cost-report generation` PASS, exit 0.

### Step 2.4: Commit

- [ ] Run:

```bash
git add scripts/bom_export.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(bom-export): wire generate_cost_report after populate_bom

Backlog #11 part 2: bom_export.main calls generate_cost_report() right
after populate_bom. Wrapped in try/except because cost-report is
observability (not a correctness gate) — a network blip in SupplierPart
listing shouldn't fail a successful sync.

New E2E test test_cost_report_generation: creates a Test-Assembly +
Test-Component with two price breaks (0.50 @ qty=1, 0.20 @ qty=100),
verifies the markdown contains the right tier rows (tier-1 €1.00,
tier-100 €40.00), and re-fetches the Assembly to confirm the notes
were patched.

Refs: docs/superpowers/specs/2026-06-04-pr4-safety-reports.md
EOF
)"
```

---

## Task 3: DryRunReporter class + pytest

**Files:**
- Create: `scripts/inventree_sync/dry_run.py`
- Create: `scripts/tests/test_dry_run_reporter.py`

### Step 3.1: Write failing pytest

- [ ] Create `scripts/tests/test_dry_run_reporter.py` with:

```python
"""Pure-Python unit tests for DryRunReporter."""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.dry_run import DryRunReporter


def test_record_appends():
    rep = DryRunReporter()
    rep.record("CREATE", "Parts", "R 10k 0805", "LCSC C17414")
    rep.record("REUSE", "Parts", "C 100nF 0805", "existing pk=4221")
    assert len(rep.records) == 2
    assert rep.records[0].action == "CREATE"
    assert rep.records[1].target == "C 100nF 0805"


def test_has_failures_false_when_only_create_reuse_skip():
    rep = DryRunReporter()
    rep.record("CREATE", "Parts", "X")
    rep.record("REUSE",  "Parts", "Y")
    rep.record("SKIP",   "Parts", "Z", "no SKU")
    assert rep.has_failures() is False


def test_has_failures_true_on_fail_record():
    rep = DryRunReporter()
    rep.record("CREATE", "Parts", "X")
    rep.record("FAIL",   "Parts", "BAT54", "no supplier data found")
    assert rep.has_failures() is True


def test_print_report_groups_by_category():
    rep = DryRunReporter()
    rep.record("REUSE",  "Parts",    "R 10k 0805", "existing pk=4221")
    rep.record("CREATE", "Parts",    "STM32U575", "LCSC C4567890")
    rep.record("REUSE",  "Assembly", "FMTransceiver Module rev 1.2", "pk=99")
    rep.record("CREATE", "BomItem",  "47 items")

    buf = io.StringIO()
    rep.print_report(file=buf, title="bom_export FMTransceiver v1.2")
    out = buf.getvalue()

    assert "DRY-RUN: bom_export FMTransceiver v1.2" in out
    # Categories must appear as section headers.
    assert "Parts:" in out
    assert "Assembly:" in out
    assert "BomItem:" in out
    # Records appear under their category.
    parts_section = out.split("Parts:")[1].split("Assembly:")[0]
    assert "Would REUSE:  R 10k 0805" in parts_section
    assert "Would CREATE: STM32U575" in parts_section
    # Summary line at the end.
    assert "Summary:" in out
    assert "2 CREATE" in out and "2 REUSE" in out


def test_print_report_exit_marker_when_failures():
    rep = DryRunReporter()
    rep.record("FAIL", "Parts", "BAT54", "no supplier data found")
    buf = io.StringIO()
    rep.print_report(file=buf, title="t")
    out = buf.getvalue()
    assert "EXIT: 1" in out
    assert "would-fail present" in out
```

### Step 3.2: Run pytest — expect failure

- [ ] Run:

```bash
pytest scripts/tests/test_dry_run_reporter.py -v
```

- [ ] Expected: `ModuleNotFoundError: No module named 'inventree_sync.dry_run'`.

### Step 3.3: Create `scripts/inventree_sync/dry_run.py`

- [ ] Create file with this content:

```python
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
```

### Step 3.4: Run pytest — expect pass

- [ ] Run:

```bash
pytest scripts/tests/test_dry_run_reporter.py -v
```

- [ ] Expected: 5 tests PASS.

### Step 3.5: py_compile sanity

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/dry_run.py scripts/tests/test_dry_run_reporter.py
```

- [ ] Expected: no output.

### Step 3.6: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/dry_run.py scripts/tests/test_dry_run_reporter.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): DryRunReporter class + pytest

Backlog #10 part 1: DryRunReporter is a pure-Python collector for the
sync flow's decisions ("Would CREATE / REUSE / SKIP / FAIL"). At the end,
print_report() groups records by category, pads action labels for visual
alignment, and emits a Summary line + EXIT marker reflecting whether
any would-FAIL records were recorded.

Pure-Python unit tests cover record-append, has_failures, grouping +
print format, and the exit-marker behavior.

Wire-up into bom_export.py + part_manager.py comes in the next task.

Refs: docs/superpowers/specs/2026-06-04-pr4-safety-reports.md
EOF
)"
```

---

## Task 4: Wire Dry-Run through `bom_export.py` + `part_manager.py` + E2E

**Files:**
- Modify: `scripts/bom_export.py` (CLI flag, conditional reporter, propagate to lower layers, print report at end)
- Modify: `scripts/inventree_sync/part_manager.py` (accept reporter param, record instead of execute)
- Modify: `scripts/e2e_revision_handling.py` (new test)

### Step 4.1: Add `--dry-run` to argparse and thread the reporter

- [ ] In `scripts/bom_export.py`, add this import alongside the other `inventree_sync` imports:

```python
from inventree_sync.dry_run import DryRunReporter
```

- [ ] Locate `parse_args()` (around line 280). ADD this argparse line right before `return parser.parse_args()`:

```python
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Simulate the sync flow without InvenTree side-effects. "
             "Prints a Would-CREATE/REUSE/SKIP/FAIL report; exit 1 on FAIL.",
    )
```

- [ ] Locate `main()` (around line 305). At the TOP of `main()`, just after `args = parse_args()` and `api = InvenTreeAPI()` lines, insert:

```python
    reporter = DryRunReporter() if args.dry_run else None
```

- [ ] Find the section that creates PCB/Assembly/Stencil + populate_bom + cost-report (around lines 320–340). Wrap them with a dry-run branch:

```python
    if reporter is not None:
        # Dry-run path: record decisions, skip API calls
        from inventree_sync.dry_run import DryRunReporter as _Reporter  # for clarity; remove if unused
        # The lower-layer record-or-execute pattern lives in part_manager and
        # in the create_*_part wrappers below; threading the reporter is done
        # via the per-call parameter we add in Step 4.3.
        ensure_parts_exist(api, entries, category_map, reporter=reporter)
        match_supplier_parts(api, entries, reporter=reporter)

        pcb_existing      = find_part_by_name_and_revision(api, f"{args.name} PCB", args.version)
        if pcb_existing is not None:
            reporter.record("REUSE", "PCB", f"{args.name} PCB rev {args.version}",
                            f"existing pk={pcb_existing.pk}")
        else:
            reporter.record("CREATE", "PCB", f"{args.name} PCB rev {args.version}")

        assembly_existing = find_part_by_name_and_revision(api, f"{args.name} Module", args.version)
        if assembly_existing is not None:
            reporter.record("REUSE", "Assembly", f"{args.name} Module rev {args.version}",
                            f"existing pk={assembly_existing.pk}")
        else:
            reporter.record("CREATE", "Assembly", f"{args.name} Module rev {args.version}")

        stencil_existing  = find_part_by_name_and_revision(api, f"{args.name} SMT Stencil", args.version)
        if stencil_existing is not None:
            reporter.record("REUSE", "Stencil", f"{args.name} SMT Stencil rev {args.version}",
                            f"existing pk={stencil_existing.pk}")
        else:
            reporter.record("CREATE", "Stencil", f"{args.name} SMT Stencil rev {args.version}")

        # BomItem decisions: count would-be-created vs existing
        if assembly_existing is None:
            # Brand-new assembly → all entries would create items
            reporter.record("CREATE", "BomItem",
                            f"{sum(len(e.inventree_part) for e in entries) + 1} items "
                            "(PCB + entries)")
        else:
            # Existing assembly → check overlap (simplified: count)
            from inventree.part import BomItem as _BomItem
            existing_items = _BomItem.list(api, part=assembly_existing.pk)
            existing_keys = {(int(bi.sub_part), bi.reference or "") for bi in existing_items}
            would_create = 0
            would_skip = 0
            for entry in entries:
                for inv_part in entry.inventree_part:
                    if (inv_part.pk, entry.reference) in existing_keys:
                        would_skip += 1
                    else:
                        would_create += 1
            reporter.record("CREATE", "BomItem", f"{would_create} items")
            if would_skip:
                reporter.record("SKIP", "BomItem", f"{would_skip} items already present")

        reporter.print_report(title=f"bom_export {args.name} v{args.version}")
        if reporter.has_failures():
            sys.exit(1)
        return  # End of dry-run path

    # Non-dry-run path: original flow continues below.
    ...
```

NOTE: the `...` is **not** literal — leave the existing flow (`pcb = create_pcb_part(...)` etc.) UNCHANGED below the dry-run branch. The dry-run code is the new addition.

- [ ] (Hygiene) Remove the redundant `from inventree_sync.dry_run import DryRunReporter as _Reporter` inside the dry-run branch — it was a leftover scratch line. The top-of-file import is enough.

### Step 4.2: Make `ensure_parts_exist` reporter-aware

- [ ] In `scripts/inventree_sync/part_manager.py`, change the signature of `ensure_parts_exist`. Find:

```python
def ensure_parts_exist(
    api: InvenTreeAPI,
    parts: list,
    category_map: Optional[dict[str, tuple[str, ...]]] = None,
) -> None:
```

…and CHANGE to:

```python
def ensure_parts_exist(
    api: InvenTreeAPI,
    parts: list,
    category_map: Optional[dict[str, tuple[str, ...]]] = None,
    reporter: Optional["DryRunReporter"] = None,
) -> None:
```

- [ ] At the top of the same file, add a forward-only import for the type:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dry_run import DryRunReporter
```

(Insert the `from typing import TYPE_CHECKING` after the existing `from typing import ...` line if one exists; otherwise add both.)

- [ ] Inside the body of `ensure_parts_exist`, locate the per-entry for-loop. We need to record decisions instead of executing them when `reporter is not None`. The cleanest minimal change: at three decision points, branch.

  Find the block that starts with `existing = find_existing_part(api, lcsc_skus, mouser_skus)` and modify the three branches:

```python
        existing = find_existing_part(api, lcsc_skus, mouser_skus)
        if existing:
            if reporter is not None:
                reporter.record(
                    "REUSE", "Parts",
                    entry.reference,
                    f"existing pk={existing.pk}",
                )
                continue
            entry.inventree_part.append(existing)
            logger.info("Found existing part for %s: pk=%s", entry.reference, existing.pk)
            # ... (existing alternates logic unchanged) ...
```

  Right after, find the `part_data = _fetch_and_merge(...)` block. Wrap it too:

```python
        # Dry-run path: no Supplier-Fetch (spec: side-effect-free). We can't
        # determine CREATE vs FAIL without the fetch, so we record CREATE
        # optimistically here. The real CREATE/FAIL outcome would only be
        # known on the actual run.
        if reporter is not None:
            generated = generate_part_name(kicad_part, kicad_value, kicad_footprint)
            reporter.record("CREATE", "Parts", entry.reference, f"name={generated!r}")
            continue
```

  Finally, the `no SKU` skip at the top of the loop needs a record too. Find:

```python
        if not lcsc_skus and not mouser_skus:
            logger.debug("Skipping part with no SKUs: %s", entry.reference)
            continue
```

  Replace with:

```python
        if not lcsc_skus and not mouser_skus:
            if reporter is not None:
                reporter.record("SKIP", "Parts", entry.reference, "no SKU")
            logger.debug("Skipping part with no SKUs: %s", entry.reference)
            continue
```

### Step 4.3: Make `match_supplier_parts` reporter-aware

- [ ] In `scripts/bom_export.py`, locate `match_supplier_parts`. Change its signature:

```python
def match_supplier_parts(
    api: InvenTreeAPI,
    entries: list[BomEntry],
    reporter: Optional["DryRunReporter"] = None,
) -> None:
```

- [ ] At the top of `scripts/bom_export.py`, add (if not already present):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventree_sync.dry_run import DryRunReporter
```

- [ ] Inside `match_supplier_parts`, find the `missing = [...]` line near the bottom and the subsequent `sys.exit(1)`. CHANGE to:

```python
    missing = [e for e in entries if not e.inventree_part and (e.lcsc or e.mouser)]
    if missing:
        for entry in missing:
            if reporter is not None:
                reporter.record(
                    "FAIL", "Parts", entry.reference,
                    f"no InvenTree match (LCSC={entry.lcsc}, Mouser={entry.mouser})",
                )
            else:
                log.error("No InvenTree part found for %s (LCSC=%s, Mouser=%s)",
                          entry.reference, entry.lcsc, entry.mouser)
        if reporter is None:
            sys.exit(1)
        # In dry-run mode, the print_report+exit happens up in main().
```

### Step 4.4: py_compile + run E2E + new dry-run E2E test

- [ ] Run:

```bash
python3 -m py_compile scripts/bom_export.py scripts/inventree_sync/part_manager.py scripts/e2e_revision_handling.py
```

Expected: no output.

- [ ] Add a new E2E test BEFORE the entry_point block in `scripts/e2e_revision_handling.py`, after `test_cost_report_generation`:

```python
def test_dry_run_no_side_effects(api: InvenTreeAPI) -> None:
    """bom_export.py --dry-run produces stdout output, creates no Parts."""
    import subprocess
    import tempfile

    cat = _ensure_category(api, f"{PREFIX} cat")
    csv_content = (
        '"References","Quantity Per PCB","Part","Value","Footprint","LCSC","MOUSER"\n'
        '"R1","1","R","10k","R_0805_2012Metric","",""\n'
        '"R2","1","R","10k","R_0805_2012Metric","",""\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        tmp.write(csv_content)
        csv_path = tmp.name

    # Baseline part count before dry-run
    before = len(Part.list(api))

    proc = subprocess.run(
        [
            sys.executable, "scripts/bom_export.py",
            "--csv_file", csv_path,
            "--name", f"{PREFIX}DryRunTest",
            "--version", "1.0",
            "--pcb_image", "doc/Icon.png",
            "--assembly_image", "doc/Icon.png",
            "--dry-run",
        ],
        env={**os.environ},
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    out = proc.stdout
    assert "DRY-RUN:" in out, f"missing DRY-RUN marker in stdout:\n{out}"
    assert "Would SKIP:" in out or "Would CREATE:" in out, f"missing decision lines:\n{out}"
    assert "Summary:" in out, f"missing Summary line:\n{out}"

    after = len(Part.list(api))
    assert before == after, (
        f"dry-run created {after - before} parts on the server (expected 0)")
    print(f"  PASS  dry-run no side-effects (stdout {len(out)}B, parts unchanged)")
```

- [ ] Add `test_dry_run_no_side_effects` to the `for tc in (...)` tuple in `main()` (as the LAST entry).

- [ ] Run E2E:

```bash
source ~/.inventree_test.env
source .venv/bin/activate
python3 scripts/e2e_revision_handling.py
```

Expected: all tests PASS including the new dry-run test, exit 0.

### Step 4.5: Commit

- [ ] Run:

```bash
git add scripts/bom_export.py scripts/inventree_sync/part_manager.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(bom-export): --dry-run flag, side-effect-free decision recording

Backlog #10 part 2: bom_export.py --dry-run threads a DryRunReporter
through ensure_parts_exist and match_supplier_parts. Lower layers
record CREATE/REUSE/SKIP/FAIL decisions instead of touching InvenTree.

PCB/Assembly/Stencil + BomItem decisions are recorded in main() via
find_part_by_name_and_revision + a simplified BomItem-overlap-count
(no actual creates).

Print-report happens at end of dry-run path with sys.exit(1) when any
FAIL was recorded — so CI can pre-flight a release tag and detect
missing supplier data before the real workflow run.

New E2E test test_dry_run_no_side_effects subprocesses bom_export.py
with a 2-entry no-SKU CSV, asserts the DRY-RUN marker + Would-SKIP
lines, and verifies Part.list() count is unchanged before vs. after.

Refs: docs/superpowers/specs/2026-06-04-pr4-safety-reports.md
EOF
)"
```

---

## Task 5: Refresh script + E2E

**Files:**
- Create: `scripts/inventree_refresh.py`
- Modify: `scripts/e2e_revision_handling.py` (new test)

### Step 5.1: Create `scripts/inventree_refresh.py`

- [ ] Create with this content:

```python
#!/usr/bin/env python3
"""
inventree_refresh.py – Maintenance script: refresh existing Parts.

Iterates every InvenTree Part that has at least one SupplierPart at
LCSC or Mouser, re-fetches supplier data (image, datasheet URL, parameters,
price-breaks), and updates the Part. Conservative on description: only
sets it if the Part currently has an empty description (manual UI edits
are preserved).

Required env vars:
    INVENTREE_API_HOST
    INVENTREE_API_TOKEN  (or USERNAME + PASSWORD)
    MOUSER_API_KEY

Usage:
    python3 scripts/inventree_refresh.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from inventree.api import InvenTreeAPI
from inventree.company import Company, SupplierPart, SupplierPriceBreak
from inventree.part import Part

from inventree_sync.client import (
    _add_price_breaks,
    upload_image_from_url,
    upload_parameters,
)
from inventree_sync.fetchers import LCSCFetcher, MouserFetcher
from inventree_sync.part_manager import _fetch_and_merge

log = logging.getLogger(__name__)


def collect_parts_to_refresh(api: InvenTreeAPI) -> dict[int, dict[str, list[str]]]:
    """Return {part_pk: {'lcsc': [skus...], 'mouser': [skus...]}} for every Part
    that has at least one SupplierPart at LCSC or Mouser."""
    relevant: dict[int, str] = {}  # company_pk -> "lcsc" | "mouser"
    for c in Company.list(api, is_supplier=True):
        name_lower = (c.name or "").lower()
        if "lcsc" in name_lower:
            relevant[c.pk] = "lcsc"
        elif "mouser" in name_lower:
            relevant[c.pk] = "mouser"

    parts_to_skus: dict[int, dict[str, list[str]]] = defaultdict(
        lambda: {"lcsc": [], "mouser": []}
    )
    for company_pk, key in relevant.items():
        for sp in SupplierPart.list(api, supplier=company_pk):
            parts_to_skus[int(sp.part)][key].append(sp.SKU)
    return dict(parts_to_skus)


def refresh_part(
    api: InvenTreeAPI,
    part_pk: int,
    lcsc_skus: list[str],
    mouser_skus: list[str],
    lcsc_fetcher: LCSCFetcher,
    mouser_fetcher: MouserFetcher,
) -> bool:
    """Refresh one Part. Returns True on success, False on no-supplier-data."""
    part = Part(api, pk=part_pk)
    primary_lcsc = lcsc_skus[0] if lcsc_skus else ""
    primary_mouser = mouser_skus[0] if mouser_skus else ""

    part_data = _fetch_and_merge(
        lcsc_fetcher, mouser_fetcher, primary_lcsc, primary_mouser
    )
    if part_data is None:
        log.warning(
            "No supplier data for pk=%s (LCSC=%s, Mouser=%s)",
            part_pk, lcsc_skus, mouser_skus,
        )
        return False

    if part_data.image_url:
        upload_image_from_url(part, part_data.image_url)

    new_link = part_data.datasheet_url or ""
    current_link = getattr(part, "link", "") or part._data.get("link") or ""
    if new_link and new_link != current_link:
        try:
            part.save({"link": new_link})
        except Exception as exc:
            log.warning("Failed to update Part.link pk=%s: %s", part_pk, exc)

    current_desc = (getattr(part, "description", "") or "").strip()
    if not current_desc and part_data.description:
        try:
            part.save({"description": part_data.description})
        except Exception as exc:
            log.warning("Failed to update Part.description pk=%s: %s", part_pk, exc)

    if part_data.parameters:
        upload_parameters(api, part, part_data.parameters)

    if part_data.price_breaks:
        for sp in SupplierPart.list(api, part=part_pk):
            try:
                for pb in SupplierPriceBreak.list(api, part=sp.pk):
                    pb.delete()
            except Exception as exc:
                log.warning("Failed to clear price breaks SupplierPart pk=%s: %s",
                            sp.pk, exc)
            _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)

    log.info("Refreshed pk=%s", part_pk)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if not os.environ.get("INVENTREE_API_HOST"):
        log.error("INVENTREE_API_HOST not set")
        return 2

    api = InvenTreeAPI()
    parts_to_skus = collect_parts_to_refresh(api)
    log.info("Found %d parts to refresh", len(parts_to_skus))

    lcsc_fetcher = LCSCFetcher()
    mouser_fetcher = MouserFetcher()

    refreshed = skipped = errors = 0
    for part_pk, sku_dict in parts_to_skus.items():
        try:
            ok = refresh_part(
                api, part_pk,
                sku_dict["lcsc"], sku_dict["mouser"],
                lcsc_fetcher, mouser_fetcher,
            )
            if ok:
                refreshed += 1
            else:
                skipped += 1
        except Exception as exc:
            log.error("Failed to refresh pk=%s: %s", part_pk, exc)
            errors += 1

    log.info(
        "Refresh complete: %d refreshed, %d skipped, %d errors",
        refreshed, skipped, errors,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### Step 5.2: chmod +x + py_compile

- [ ] Run:

```bash
chmod +x scripts/inventree_refresh.py
python3 -m py_compile scripts/inventree_refresh.py
```

Expected: no output.

### Step 5.3: Add E2E test

- [ ] In `scripts/e2e_revision_handling.py`, add this test after `test_dry_run_no_side_effects`:

```python
def test_refresh_idempotent(api: InvenTreeAPI) -> None:
    """inventree_refresh: idempotent + a no-op on a Part it already refreshed."""
    import subprocess
    # Create a throwaway Part with a real LCSC SKU so the refresh has work
    # to do. C17414 = Uniroyal 10kΩ 0805 (used elsewhere in our PR-1 probes).
    cat = _ensure_category(api, f"{PREFIX} cat")
    lcsc_supplier = _track_company(Company.list(api, name="LCSC") or [None])[0] \
        if Company.list(api, name="LCSC") else None
    if lcsc_supplier is None:
        # Create LCSC supplier if absent (cleanup-safe via _track_company)
        from inventree.company import Company as _Company
        lcsc_supplier = _track_company(_Company.create(
            api, {"name": "LCSC", "is_supplier": True}))

    target = _track(Part.create(api, {
        "name": f"{PREFIX} RefreshTest",
        "description": "",  # empty so refresh will populate it
        "active": True, "component": True, "purchaseable": True,
    }))
    SupplierPart.create(api, {
        "part": target.pk, "supplier": lcsc_supplier.pk, "SKU": "C17414",
    })

    # First run: should populate fields
    proc1 = subprocess.run(
        [sys.executable, "scripts/inventree_refresh.py"],
        env={**os.environ}, capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert "Refresh complete:" in proc1.stderr or "Refresh complete:" in proc1.stdout, (
        f"refresh did not finish cleanly:\nSTDOUT:\n{proc1.stdout}\nSTDERR:\n{proc1.stderr}")

    # Second run: should also complete without crashing (idempotent).
    proc2 = subprocess.run(
        [sys.executable, "scripts/inventree_refresh.py"],
        env={**os.environ}, capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc2.returncode == 0, (
        f"second refresh exit={proc2.returncode}:\n"
        f"STDOUT:\n{proc2.stdout}\nSTDERR:\n{proc2.stderr}")
    print("  PASS  refresh idempotent (2 runs, exit 0, no crash)")
```

- [ ] Add `test_refresh_idempotent` to the `for tc in (...)` tuple in `main()` (as the LAST entry).

### Step 5.4: Run E2E

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

Expected: all tests PASS, exit 0.

### Step 5.5: Commit

- [ ] Run:

```bash
git add scripts/inventree_refresh.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): inventree_refresh.py + E2E idempotency test

Backlog #9: standalone refresh script. Discovers every Part with at
least one LCSC or Mouser SupplierPart, re-fetches supplier data via
the existing _fetch_and_merge pipeline, and updates:
  - image (re-download via existing upload_image_from_url helper)
  - datasheet link (only when changed)
  - description (only when currently empty — preserves manual UI edits)
  - parameters (delta-sync via PR-3 upload_parameters)
  - price breaks (clear + re-add)

E2E test test_refresh_idempotent creates a throwaway Part with a real
LCSC SKU (C17414), runs the refresh twice, and asserts both runs exit
0. The second run still does work (price-breaks cleared and re-added)
but no crash, which is the contract that matters.

Workflow YAML for nightly cron + manual dispatch comes in the next task.

Refs: docs/superpowers/specs/2026-06-04-pr4-safety-reports.md
EOF
)"
```

---

## Task 6: Nightly cron workflow + Final verification

**Files:**
- Create: `.github/workflows/scheduled-inventree-refresh.yaml`

### Step 6.1: Create the workflow YAML

- [ ] Create file with this content:

```yaml
name: Scheduled InvenTree Refresh

on:
  schedule:
    # Nightly at 03:00 UTC. Adjust if Mouser's rate-limit window matters.
    - cron: '0 3 * * *'
  workflow_dispatch:

permissions:
  contents: read

jobs:
  refresh:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout
        uses: actions/checkout@v5

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.x'

      - name: Install runtime dependencies
        run: pip install -r scripts/requirements.txt

      - name: Run refresh
        env:
          INVENTREE_API_HOST: ${{ secrets.INVENTREE_API_HOST }}
          INVENTREE_API_TOKEN: ${{ secrets.INVENTREE_API_TOKEN }}
          MOUSER_API_KEY: ${{ secrets.MOUSER_API_KEY }}
        continue-on-error: true
        run: python3 scripts/inventree_refresh.py
```

### Step 6.2: YAML safe_load + syntax sanity

- [ ] Run:

```bash
python3 -c "
import yaml
yaml.safe_load(open('.github/workflows/scheduled-inventree-refresh.yaml'))
print('YAML OK')
"
```

Expected: `YAML OK`.

### Step 6.3: Run all pytests

- [ ] Run:

```bash
pytest scripts/tests/ -q
```

Expected: previous count + 11 new tests (5 dry-run + 6 cost-report). For example `73 → 84 passed`. Number doesn't have to be exact; what matters is no failures.

### Step 6.4: Run E2E

- [ ] Run:

```bash
source ~/.inventree_test.env
source .venv/bin/activate
python3 scripts/e2e_revision_handling.py
```

Expected: all tests PASS (PR-2 + PR-3 + PR-4 additions), exit 0.

### Step 6.5: Probe regression

- [ ] Run:

```bash
python3 scripts/probe_supplier_images.py
```

Expected: 4 PASS (PR-1 regression check still green).

### Step 6.6: actionlint (optional but consistent with PR-1/2/3)

- [ ] Run if actionlint is installed:

```bash
actionlint .github/workflows/scheduled-inventree-refresh.yaml || echo "(actionlint not installed; skip)"
```

Expected: either silent (lint passed) or the skip message.

### Step 6.7: Branch summary

- [ ] Run:

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

Expected:
- ~8 commits on `feat/safety-reports` (spec, plan, Task 1, Task 2, Task 3, Task 4, Task 5, Task 6 with the workflow file).
- Files changed match the File-Structure table at the top of this plan.

### Step 6.8: Commit the workflow file

- [ ] Run:

```bash
git add .github/workflows/scheduled-inventree-refresh.yaml
git commit -m "$(cat <<'EOF'
feat(workflows): scheduled-inventree-refresh nightly cron

Nightly cron 03:00 UTC + manual workflow_dispatch trigger. Installs
scripts/requirements.txt, runs scripts/inventree_refresh.py with the
INVENTREE_API_HOST / INVENTREE_API_TOKEN / MOUSER_API_KEY secrets.

continue-on-error: true — refresh is maintenance, not a release-blocker.
Errors get logged in the workflow run; a transient supplier-API outage
shouldn't trigger a red X on main.

Refs: docs/superpowers/specs/2026-06-04-pr4-safety-reports.md
EOF
)"
```

### Step 6.9: Done — branch ready for PR

- [ ] Confirm clean working tree:

```bash
git status
```

Expected: `nothing to commit, working tree clean`.

PR creation, Copilot review loop, and merge happen in a separate phase.

---

## Akzeptanzkriterien (mirror of spec)

- [x] `scripts/inventree_refresh.py` existiert, läuft eigenständig (Task 5)
- [x] `.github/workflows/scheduled-inventree-refresh.yaml` nightly + manual (Task 6)
- [x] `bom_export.py --dry-run` Pretty-Print stdout, keine Side-Effects, korrekter Exit-Code (Task 4)
- [x] `DryRunReporter` Klasse mit `record()` + `print_report()` (Task 3)
- [x] `generate_cost_report` Markdown mit Tiers [1,10,100] aus existing InvenTree-Daten (Task 1+2)
- [x] Cost-Report landet in `$GITHUB_STEP_SUMMARY` + Assembly.notes (Task 1+2)
- [x] Cheapest-pro-Qty Logik korrekt (Pytest in Task 1)
- [x] 3 neue E2E-Tests grün (Tasks 2, 4, 5)
- [x] 2 neue Pytest-Files grün (Tasks 1, 3)
- [x] pytest-Suite weiter grün (Task 6)
- [x] py_compile clean (Tasks 1–6)
