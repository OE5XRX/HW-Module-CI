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
from datetime import datetime
from pathlib import Path
from typing import Optional


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


_MOUSER_DATE_RE = re.compile(r"^(\d{2})-([A-Za-z]{3})-(\d{2})$")
_MOUSER_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_mouser_price(price_str: Optional[str]) -> float:
    """Parse a Mouser-style price cell ("€ 0,381", "$ 1.23", "0.0074").

    Mirrors the format logic in ``fetchers.MouserFetcher._parse_price`` but
    accepts the wider variety of strings the XLS export emits (with or
    without currency glyph, sometimes a non-breaking space).  Returns 0.0
    on empty/None input.
    """
    if price_str is None:
        return 0.0
    cleaned = re.sub(r"[^\d,.]", "", str(price_str).strip())
    if not cleaned:
        return 0.0
    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")
    if last_comma > last_dot:
        # European format: 0,381 or 1.234,56
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")  # remove US thousands separators
    return float(cleaned)


def _parse_mouser_date(date_str: Optional[str]) -> Optional[str]:
    """Convert a Mouser XLS date like "07-Jul-25" to ISO "2025-07-07".

    Returns None for empty / unrecognised strings — callers tolerate the
    missing date and proceed.
    """
    if not date_str:
        return None
    m = _MOUSER_DATE_RE.match(date_str.strip())
    if not m:
        return None
    day_s, mon_s, yr_s = m.groups()
    month = _MOUSER_MONTHS.get(mon_s.capitalize())
    if not month:
        return None
    try:
        year = 2000 + int(yr_s)
        day = int(day_s)
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _rows_to_mouser_order(rows: list[dict]) -> SupplierOrder:
    """Pure transformation: list of row-dicts → SupplierOrder.

    Each row-dict carries the column names from the Mouser XLS export
    (``"Sales Order No:"``, ``"Mouser No:"``, …) as keys.  Empty rows
    (no Mouser-No) are skipped — Excel sometimes pads with blank lines.
    """
    reference = "mouser-unknown"
    order_date: Optional[str] = None
    lines: list[SupplierOrderLine] = []
    for row in rows:
        sku = str(row.get("Mouser No:") or "").strip()
        if not sku:
            continue
        if reference == "mouser-unknown":
            sales_no = str(row.get("Sales Order No:") or "").strip()
            if sales_no:
                reference = sales_no
        if order_date is None:
            order_date = _parse_mouser_date(str(row.get("Order Date:") or "").strip())
        lines.append(SupplierOrderLine(
            sku=sku,
            qty=int(float(str(row.get("Order Qty.") or "0").strip() or "0")),
            unit_price=_parse_mouser_price(row.get("Price (EUR)")),
            currency="EUR",
            mpn=str(row.get("Mfr. No:") or "").strip(),
            mfr_name="",  # not in file; populated later by MouserFetcher
            description=str(row.get("Desc.:") or "").strip(),
            package="",   # Mouser XLS has no dedicated package column
        ))
    return SupplierOrder(
        supplier_name="Mouser",
        reference=reference,
        order_date=order_date,
        currency="EUR",
        lines=lines,
    )


def _read_mouser_rows(path: Path) -> list[dict]:
    """Read a Mouser .xls into a list of row-dicts keyed by header name.

    Uses xlrd 2.0 which supports legacy BIFF .xls (Mouser's export format).
    Sheet 0 is assumed to be ``Order Details`` — Mouser hasn't varied this
    in years.  Row 0 is the header.
    """
    import xlrd  # lazy import so test_*_parsers.py doesn't need xlrd installed
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    if sheet.nrows < 2:
        return []
    headers = [str(c).strip() for c in sheet.row_values(0)]
    out: list[dict] = []
    for r in range(1, sheet.nrows):
        cells = sheet.row_values(r)
        out.append({headers[i]: cells[i] for i in range(min(len(headers), len(cells)))})
    return out


def parse_mouser_xls(path: Path) -> SupplierOrder:
    """Parse a Mouser-XLS order file into a SupplierOrder."""
    return _rows_to_mouser_order(_read_mouser_rows(path))
