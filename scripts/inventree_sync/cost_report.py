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
    tiers: tuple[int, ...] = (1, 10, 100),
) -> str:
    """Generate the cost report. Returns the Markdown string.

    Side effects:
      - Appends to $GITHUB_STEP_SUMMARY if the env-var is set.
      - Patches assembly.notes via the SDK save() (best-effort, swallows errors).
    """
    # 1) Materialize per-entry price data.
    items_with_prices: list[tuple[BomEntry, dict[str, list[tuple[int, float]]]]] = []
    items_missing: list[tuple[str, str]] = []
    for entry in entries:
        # Multiple inventree_part entries are alternates (PR-3 Multi-SKU);
        # merge their price data.
        merged: dict[str, list[tuple[int, float]]] = {}
        for inv_part in entry.inventree_part:
            for sup, breaks in _collect_price_data(api, inv_part).items():
                merged.setdefault(sup, []).extend(breaks)
        if merged:
            items_with_prices.append((entry, merged))
        else:
            primary_name = entry.kicad_value or entry.kicad_part or entry.reference
            items_missing.append((entry.reference, primary_name))

    # 2) Aggregate per tier.
    rows: list[tuple[int, float, float, dict[str, int]]] = []
    for tier_qty in tiers:
        total = 0.0
        sources: dict[str, int] = {}
        for entry, price_data in items_with_prices:
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
            # encoding=utf-8 — markdown contains €.
            with open(summary_path, "a", encoding="utf-8") as fh:
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
