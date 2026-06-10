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

from dataclasses import dataclass, field


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
