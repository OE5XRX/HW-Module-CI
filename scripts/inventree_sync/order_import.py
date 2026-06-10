"""
order_import.py – Import historical supplier orders into InvenTree.

Parses Mouser-XLS and LCSC-CSV order files, resolves each line to an
InvenTree Part (reusing the `inventree_sync` BOM-export primitives) and
upserts a PurchaseOrder per supplier with received StockItems.

File is source-of-truth on re-runs:
  - PO not yet present       → CREATE + receive
  - PO PENDING/PLACED        → reconcile line-items against file → receive
  - PO COMPLETE, in-sync     → no-op
  - PO COMPLETE, drift       → loud-fail, no writes
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SupplierOrderLine:
    """One line in a Mouser-XLS or LCSC-CSV supplier order.

    Attributes
    ----------
    sku            distributor SKU (Mouser-No or LCSC product code) — the
                   reconciliation key across re-runs
    qty            ordered quantity
    unit_price     price per unit in the order's currency
    currency       ISO currency code ("EUR" for Mouser, "USD" for LCSC)
    mpn            manufacturer part number from the file
    mfr_name       manufacturer name from the file (empty for Mouser files)
    description    free-text description from the file
    package        package hint (LCSC has a dedicated column; Mouser doesn't)
    """
    sku: str
    qty: int
    unit_price: float
    currency: str
    mpn: str
    mfr_name: str
    description: str
    package: str = ""


@dataclass
class SupplierOrder:
    """A complete supplier order parsed from a single XLS or CSV file."""
    supplier_name: str    # "Mouser" or "LCSC"
    reference: str        # PO reference visible in InvenTree
    order_date: str | None  # ISO date "YYYY-MM-DD" (Mouser only; None for LCSC)
    currency: str         # default currency for all lines
    lines: list[SupplierOrderLine] = field(default_factory=list)


_LCSC_FILENAME_RE = re.compile(r"^LCSC__([A-Za-z0-9]+)_\d+\.csv$")


def _parse_lcsc_reference(filename: str) -> str:
    """Extract the LCSC order reference from the canonical filename.

    Canonical pattern: ``LCSC__<reference>_<timestamp>.csv``.
    Returns ``"lcsc-unknown"`` when the pattern doesn't match — caller
    should override via CLI flag in that case.
    """
    m = _LCSC_FILENAME_RE.match(filename)
    return m.group(1) if m else "lcsc-unknown"


def parse_lcsc_csv(path: Path) -> SupplierOrder:
    """Parse an LCSC-exported order CSV into a SupplierOrder.

    The reference is derived from the filename; LCSC's CSV body has no
    canonical order ID column.  Currency is hard-coded "USD" because LCSC
    always exports prices in dollars (column name is ``Unit Price($)``).
    """
    lines: list[SupplierOrderLine] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sku = (row.get("LCSC Part Number") or "").strip()
            if not sku:
                continue  # skip blank/footer rows
            lines.append(SupplierOrderLine(
                sku=sku,
                qty=int((row.get("Quantity") or "0").strip()),
                unit_price=float((row.get("Unit Price($)") or "0").strip()),
                currency="USD",
                mpn=(row.get("Manufacture Part Number") or "").strip(),
                mfr_name=(row.get("Manufacturer") or "").strip(),
                description=(row.get("Description") or "").strip(),
                package=(row.get("Package") or "").strip(),
            ))
    return SupplierOrder(
        supplier_name="LCSC",
        reference=_parse_lcsc_reference(path.name),
        order_date=None,
        currency="USD",
        lines=lines,
    )
