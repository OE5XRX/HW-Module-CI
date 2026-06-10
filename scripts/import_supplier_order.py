#!/usr/bin/env python3
"""
import_supplier_order.py – CLI to import Mouser-XLS and/or LCSC-CSV
supplier orders into InvenTree as PurchaseOrders with received StockItems.

Required environment variables:
    INVENTREE_API_HOST     – InvenTree server URL
    INVENTREE_API_TOKEN    – API token
    MOUSER_API_KEY         – Mouser API v2 key (only when --mouser-xls used)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# Allow the script to be invoked from any CWD by anchoring imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inventree.api import InvenTreeAPI

from inventree_sync.categories import load_category_map
from inventree_sync.client import get_or_create_supplier
from inventree_sync.dry_run import DryRunReporter
from inventree_sync.fetchers import LCSCFetcher, MouserFetcher
from inventree_sync.order_import import (
    SupplierOrder,
    ensure_part_for_order_line,
    get_receive_location,
    parse_lcsc_csv,
    parse_mouser_xls,
    upsert_purchase_order,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Import historical supplier orders into InvenTree.",
    )
    p.add_argument("--mouser-xls", help="Path to a Mouser order .xls export")
    p.add_argument("--lcsc-csv",   help="Path to an LCSC order .csv export")
    p.add_argument("--location",   default="Lager",
                   help="StockLocation name to receive into "
                        "(default: 'Lager'). Falls back to first top-level "
                        "location if name doesn't match.")
    p.add_argument("--categories", metavar="YAML",
                   help="Custom KiCad-symbol→category map "
                        "(defaults to inventree_sync/default_categories.yaml). "
                        "Mostly irrelevant for supplier imports — most parts "
                        "land in 'Miscellaneous' or supplier-Category. ")
    p.add_argument("--dry-run", action="store_true",
                   help="Skip InvenTree writes; print the planned actions.")
    args = p.parse_args(argv)
    if not args.mouser_xls and not args.lcsc_csv:
        p.error("provide at least one of --mouser-xls or --lcsc-csv")
    return args


def _suppress_category_warning() -> None:
    """Demote the 'KiCad symbol "" not in map' WARNING to DEBUG.

    Otherwise every part imported (50+ at typical run) emits one of these,
    drowning out actionable logs. We expect empty kicad_part for supplier
    imports — that's by design.

    Mutates the LogRecord level rather than dropping it, so the record
    stays in the stream as DEBUG. The CLI configures the root handler at
    INFO so the message is hidden by default; lifting it requires reaching
    in via ``logging.getLogger().setLevel(logging.DEBUG)`` (e.g. from a
    REPL session that imports the script as a module). No CLI flag exposes
    this — the WARNING is purely noise during the supplier-import flow.
    """
    class _F(logging.Filter):
        def filter(self, record):
            if "not found in category map" in record.getMessage():
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
            return True
    logging.getLogger("inventree_sync.categories").addFilter(_F())


def _import_one_order(
    api: InvenTreeAPI,
    order: SupplierOrder,
    lcsc_fetcher: Optional[LCSCFetcher],
    mouser_fetcher: Optional[MouserFetcher],
    lcsc_supplier,
    mouser_supplier,
    category_map: dict,
    receive_location,
    dry_run: bool,
    reporter: Optional[DryRunReporter] = None,
) -> int:
    """Process one parsed SupplierOrder. Returns exit-code (0 ok, 1 drift).

    *lcsc_fetcher* / *mouser_fetcher* are ``Optional`` because single-
    supplier runs (``--lcsc-csv`` xor ``--mouser-xls``) instantiate only
    the side they need. The side matching ``order.supplier_name`` MUST be
    non-None — ``ensure_part_for_order_line`` enforces that at the call
    site of every line.

    *reporter* is set when ``--dry-run`` is active. It's threaded into
    ``ensure_part_for_order_line`` so the resolution chain records
    decisions instead of executing writes. PO upsert decisions are
    recorded here using the ``UpsertReport.action`` from
    ``upsert_purchase_order`` (which is dry-run-aware on its own).
    """
    supplier_kind = order.supplier_name  # "LCSC" or "Mouser"
    supplier = lcsc_supplier if supplier_kind == "LCSC" else mouser_supplier

    log.info("Resolving %d parts from %s order %s…",
             len(order.lines), supplier_kind, order.reference)

    sku_to_sp: dict = {}
    for line in order.lines:
        try:
            _, sp = ensure_part_for_order_line(
                api, line, supplier_kind,
                lcsc_fetcher, mouser_fetcher,
                lcsc_supplier, mouser_supplier,
                category_map,
                reporter=reporter,
            )
        except Exception as exc:
            log.error("Failed to resolve %s line %s: %s",
                      supplier_kind, line.sku, exc)
            return 1
        # Real-run path: sp is a SupplierPart, add it to the mapping.
        # Dry-run path: sp is None, skip — upsert_purchase_order short-
        # circuits before touching the mapping in all three paths.
        if sp is not None:
            sku_to_sp[line.sku] = sp

    try:
        report = upsert_purchase_order(
            api=api, order=order, supplier=supplier,
            sku_to_supplier_part=sku_to_sp,
            receive_location=receive_location,
            dry_run=dry_run,
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if reporter is not None:
        # In dry-run, fold the PO decision into the report under a stable
        # category name. Action prefix "DRY_RUN_" is stripped so the
        # printed report reads cleanly.
        action_clean = report.action.removeprefix("DRY_RUN_")
        action_kind = "CREATE" if action_clean in ("CREATE", "RECONCILE") else "REUSE"
        reporter.record(
            action_kind, "PurchaseOrder", report.po_reference,
            f"{action_clean} added={report.lines_added} "
            f"updated={report.lines_updated} deleted={report.lines_deleted}",
        )
    else:
        log.info(
            "%s PO %s — added=%d updated=%d deleted=%d",
            report.action, report.po_reference,
            report.lines_added, report.lines_updated, report.lines_deleted,
        )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    _suppress_category_warning()
    log.info(
        "Importing supplier-order parts without KiCad context — "
        "categories will fall back to supplier-provided or 'Miscellaneous'.")

    reporter = DryRunReporter() if args.dry_run else None

    api = InvenTreeAPI()
    # Only instantiate the suppliers/fetchers actually needed — avoids
    # confusing error logs and avoidable permission failures on the unused
    # side. Downstream helpers (`ensure_part_for_order_line`) accept Optional
    # suppliers/fetchers and only touch the relevant one for each line.
    lcsc_fetcher = LCSCFetcher() if args.lcsc_csv else None
    mouser_fetcher = MouserFetcher() if args.mouser_xls else None
    lcsc_supplier = (
        get_or_create_supplier(api, name="LCSC") if args.lcsc_csv else None
    )
    mouser_supplier = (
        get_or_create_supplier(api, name="Mouser") if args.mouser_xls else None
    )
    if args.lcsc_csv and lcsc_supplier is None:
        log.error("Could not get or create LCSC supplier — aborting.")
        return 1
    if args.mouser_xls and mouser_supplier is None:
        log.error("Could not get or create Mouser supplier — aborting.")
        return 1
    category_map = load_category_map(args.categories)
    receive_location = get_receive_location(api, args.location)

    rc = 0
    if args.lcsc_csv:
        try:
            order = parse_lcsc_csv(Path(args.lcsc_csv))
        except Exception as exc:
            log.error("Failed to parse LCSC file %s: %s", args.lcsc_csv, exc)
            rc |= 1
        else:
            rc |= _import_one_order(
                api, order, lcsc_fetcher, mouser_fetcher,
                lcsc_supplier, mouser_supplier, category_map,
                receive_location, args.dry_run,
                reporter=reporter,
            )
    if args.mouser_xls:
        try:
            order = parse_mouser_xls(Path(args.mouser_xls))
        except Exception as exc:
            log.error("Failed to parse Mouser file %s: %s", args.mouser_xls, exc)
            rc |= 1
        else:
            rc |= _import_one_order(
                api, order, lcsc_fetcher, mouser_fetcher,
                lcsc_supplier, mouser_supplier, category_map,
                receive_location, args.dry_run,
                reporter=reporter,
            )
    if reporter is not None:
        reporter.print_report(title="Supplier Order Import (dry run)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
