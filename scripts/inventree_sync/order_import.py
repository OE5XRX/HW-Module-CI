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
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from inventree.api import InvenTreeAPI
from inventree.company import Company, SupplierPart
from inventree.part import Part
from inventree.purchase_order import PurchaseOrder
from inventree.stock import StockLocation

from .categories import resolve_part_category
from .client import (
    create_part_in_inventree,
    ensure_supplier_parts,
    find_existing_part,
    find_part_by_mpn_and_manufacturer,
    find_part_by_name,
)
from .dry_run import DryRunReporter
from .fetchers import LCSCFetcher, MouserFetcher
from .models import PartData

logger = logging.getLogger(__name__)


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
    """Parse a European-formatted Mouser price cell.

    Real input shape — the Mouser XLS ``Price (EUR)`` column is the only
    call site, always emitting European convention (comma = decimal, dot
    = thousands): ``"€ 0,381"``, ``"€ 1,16"``, ``"€ 1.234,56"``.

    Disambiguation rules:
      - Multi-group thousands separators (``"1,234,567"`` or
        ``"1.234.567"``) are detected explicitly — no locale uses two or
        more decimal separators, so these are unambiguously thousands.
      - Single-group forms like ``"0,381"`` or ``"1,234"`` are
        syntactically ambiguous; we interpret comma as decimal because
        that matches the Mouser-EUR column convention.

    Returns 0.0 for empty/None input. Mirrors
    ``fetchers.MouserFetcher._parse_price`` but tolerates more whitespace
    / glyph variants that show up in XLS exports.
    """
    if price_str is None:
        return 0.0
    cleaned = re.sub(r"[^\d,.]", "", str(price_str).strip())
    if not cleaned:
        return 0.0

    # Unambiguous multi-group thousands separators. Match these first so
    # that values like "1,234,567" are not misread as decimals by the
    # last-separator heuristic below.
    if re.fullmatch(r"\d{1,3}(,\d{3}){2,}", cleaned):
        return float(cleaned.replace(",", ""))
    if re.fullmatch(r"\d{1,3}(\.\d{3}){2,}", cleaned):
        return float(cleaned.replace(".", ""))

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


def get_receive_location(api: InvenTreeAPI, name: str) -> StockLocation:
    """Resolve the receive-into StockLocation.

    Order of attempts:
      1. Exact name match (case-sensitive — InvenTree's filter is exact).
      2. First top-level StockLocation (parent is None).
      3. Raise RuntimeError if no StockLocation exists at all.

    The fallback exists so a fresh InvenTree install with just a single
    default location can be imported into without forcing the user to
    rename it to "Lager" first.

    Unlike ``get_or_create_supplier`` in ``client.py``, this function
    *raises* on failure rather than returning ``None`` — a receive-into
    location is a hard prerequisite for the importer.
    """
    try:
        matches = StockLocation.list(api, name=name)
        for loc in matches:
            if loc.name == name:
                return loc
    except Exception as exc:
        logger.warning(
            "StockLocation lookup for %r failed (%s: %s); "
            "falling back to top-level location.",
            name, type(exc).__name__, exc,
        )

    try:
        all_locs = StockLocation.list(api)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot list StockLocations: {exc}. "
            "Verify INVENTREE_API_HOST/TOKEN and that the server is up."
        ) from exc

    top_level = [loc for loc in all_locs if getattr(loc, "parent", None) in (None, 0)]
    if top_level:
        chosen = top_level[0]
        logger.warning(
            "StockLocation %r not found; falling back to top-level %r (pk=%s)",
            name, chosen.name, chosen.pk)
        return chosen

    raise RuntimeError(
        "No StockLocation found in InvenTree. Create one (e.g. 'Lager') "
        "in the UI before running this importer."
    )


def _lookup_supplier_part(api: InvenTreeAPI, sku: str) -> SupplierPart:
    """Return the SupplierPart for *sku* or raise RuntimeError.

    Used after create_part_in_inventree to recover the SupplierPart PK
    (the create call returns only the Part).
    """
    sps = SupplierPart.list(api, SKU=sku)
    for sp in sps:
        if sp.SKU == sku:
            return sp
    raise RuntimeError(
        f"SupplierPart for SKU {sku!r} not found — expected after "
        "create_part_in_inventree or ensure_supplier_parts. "
        "Did a SupplierPart create fail silently?"
    )


def _partdata_from_line(line: SupplierOrderLine) -> PartData:
    """Build a minimal PartData from a SupplierOrderLine when supplier API fails."""
    return PartData(
        mpn=line.mpn,
        manufacturer=line.mfr_name,
        description=line.description,
        package=line.package,
        lcsc_sku="",
        mouser_sku="",
    )


def ensure_part_for_order_line(
    api: InvenTreeAPI,
    line: SupplierOrderLine,
    supplier_kind: str,                       # "LCSC" or "Mouser"
    lcsc_fetcher: Optional[LCSCFetcher],
    mouser_fetcher: Optional[MouserFetcher],
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
    category_map: dict,
    *,
    reporter: Optional[DryRunReporter] = None,
) -> tuple[Optional[Part], Optional[SupplierPart]]:
    """Resolve a line to (Part, SupplierPart), creating both if needed.

    Dedup chain (same priority as part_manager.ensure_parts_exist):
      1. find_existing_part via the line's SKU.
      2. Fetcher (LCSC or Mouser) → PartData.
      3. find_part_by_mpn_and_manufacturer.
      4. find_part_by_name (name = part_data.mpn or line.mpn or sku).
      5. create_part_in_inventree.

    On fetcher failure (3rd-party API down or SKU unknown), a minimal
    PartData is synthesised from the file row so the Part can still be
    created — just without datasheet/image/parameter enrichment.

    Fetcher / supplier arguments are ``Optional`` because a single-supplier
    import run (e.g. ``--lcsc-csv`` without ``--mouser-xls``) instantiates
    only the side it needs. The *unused* side may legally be ``None``; the
    side matching ``supplier_kind`` MUST be non-None — this is enforced at
    the top of the function so misuse fails loud at the call site rather
    than silently NPE-ing inside the dedup chain.

    Dry-run (``reporter is not None``): all read-only lookups still run
    (SKU → fetcher → MPN+Mfr → Name) but every dependent call
    (``ensure_supplier_parts``, ``resolve_part_category``,
    ``create_part_in_inventree``, ``_lookup_supplier_part``) is replaced by
    a ``reporter.record(...)`` call and the function returns ``(None, None)``.
    Note: ``_lookup_supplier_part`` is a read-only SKU→SupplierPart lookup,
    not a write — it is skipped in dry-run because the function returns
    ``None`` for the SupplierPart, making the lookup unnecessary.
    Caller MUST tolerate the nullable return.
    """
    if supplier_kind not in ("LCSC", "Mouser"):
        raise ValueError(
            f"supplier_kind must be 'LCSC' or 'Mouser', got {supplier_kind!r}"
        )
    is_lcsc = supplier_kind == "LCSC"
    if is_lcsc and (lcsc_fetcher is None or lcsc_supplier is None):
        raise ValueError(
            "LCSC line requires non-None lcsc_fetcher and lcsc_supplier "
            "(check the call site — single-supplier runs must pass both for "
            "the side actually being imported)."
        )
    if not is_lcsc and (mouser_fetcher is None or mouser_supplier is None):
        raise ValueError(
            "Mouser line requires non-None mouser_fetcher and mouser_supplier "
            "(check the call site — single-supplier runs must pass both for "
            "the side actually being imported)."
        )
    lcsc_skus = [line.sku] if is_lcsc else []
    mouser_skus = [line.sku] if not is_lcsc else []

    # 1. SKU lookup
    existing = find_existing_part(api, lcsc_skus, mouser_skus)
    if existing is not None:
        if reporter is not None:
            reporter.record(
                "REUSE", "Parts", line.sku,
                f"existing pk={existing.pk}",
            )
            return None, None
        return existing, _lookup_supplier_part(api, line.sku)

    # 2. Supplier fetch (read-only; runs in dry-run too)
    if is_lcsc:
        part_data = lcsc_fetcher.fetch_by_sku(line.sku)
    else:
        part_data = mouser_fetcher.fetch(line.sku)
    if part_data is None:
        logger.warning(
            "Supplier API returned no data for %s SKU %r — "
            "falling back to file row.", supplier_kind, line.sku)
        part_data = _partdata_from_line(line)
    # Always stamp the right SKU back onto part_data so downstream creates
    # have it.
    if is_lcsc:
        part_data.lcsc_sku = line.sku
    else:
        part_data.mouser_sku = line.sku

    # 3. MPN + Manufacturer
    mpn = (part_data.mpn or line.mpn or "").strip()
    mfr = (part_data.manufacturer or line.mfr_name or "").strip()
    if mpn and mfr:
        by_mpn = find_part_by_mpn_and_manufacturer(api, mpn, mfr)
        if by_mpn is not None:
            if reporter is not None:
                reporter.record(
                    "REUSE", "Parts", line.sku,
                    f"via MPN+Mfr pk={by_mpn.pk}",
                )
                return None, None
            ensure_supplier_parts(
                api, by_mpn, part_data,
                lcsc_supplier, mouser_supplier,
                lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
            )
            return by_mpn, _lookup_supplier_part(api, line.sku)

    # 4. Name lookup
    name = (part_data.mpn or line.mpn or line.sku).strip()
    by_name = find_part_by_name(api, name)
    if by_name is not None:
        if reporter is not None:
            reporter.record(
                "REUSE", "Parts", line.sku,
                f"via name {name!r} pk={by_name.pk}",
            )
            return None, None
        ensure_supplier_parts(
            api, by_name, part_data,
            lcsc_supplier, mouser_supplier,
            lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
        )
        return by_name, _lookup_supplier_part(api, line.sku)

    # 5. Create
    if reporter is not None:
        reporter.record(
            "CREATE", "Parts", line.sku,
            f"name={name!r}",
        )
        return None, None
    category = resolve_part_category(
        api, "", part_data, line.package, category_map,
    )
    created = create_part_in_inventree(
        api, name, part_data, category,
        lcsc_supplier, mouser_supplier,
        lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
    )
    if created is None:
        raise RuntimeError(
            f"create_part_in_inventree returned None for line {line.sku!r}"
        )
    return created, _lookup_supplier_part(api, line.sku)


@dataclass
class LineItemAction:
    """An update operation against an existing PurchaseOrderLineItem."""
    line_item: object     # PurchaseOrderLineItem (kept generic for test mocks)
    new_quantity: int
    new_price: float


@dataclass
class POLineDiff:
    """Outcome of comparing a SupplierOrder against an InvenTree PO's lines."""
    to_add: list           # list[SupplierOrderLine]
    to_update: list        # list[LineItemAction]
    to_delete: list        # list[PurchaseOrderLineItem]

    @property
    def is_empty(self) -> bool:
        return not (self.to_add or self.to_update or self.to_delete)

    def format_report(self) -> str:
        """Human-readable summary used by Pfad C drift fail-loud."""
        out = []
        for sl in self.to_add:
            out.append(f"  ADD     {sl.sku} qty={sl.qty} {sl.currency} {sl.unit_price}")
        for upd in self.to_update:
            out.append(
                f"  UPDATE  {getattr(upd.line_item, 'reference', '?')} "
                f"qty→{upd.new_quantity} price→{upd.new_price}"
            )
        for li in self.to_delete:
            ref = getattr(li, "reference", "") or f"line#{li.pk}"
            qty = getattr(li, "quantity", "?")
            received = getattr(li, "received", 0)
            warn = ""
            if received and int(received) > 0:
                warn = f" (would orphan {received} StockItem(s))"
            out.append(f"  REMOVE  {ref} qty={qty}{warn}")
        return "\n".join(out) if out else "  (no changes)"


def _po_line_sku(po_line, sp_pk_to_sku: dict) -> str:
    """Return the SKU for a PurchaseOrderLineItem.

    Strategy: prefer line.reference (we set it = SKU during create); fall
    back to looking up SupplierPart.SKU via the supplier_part PK on the
    line.  Fallback supports POs that were originally created without
    `reference`.
    """
    ref = (getattr(po_line, "reference", "") or "").strip()
    if ref:
        return ref
    sp_pk = getattr(po_line, "part", None)
    if sp_pk is None:
        return ""
    return sp_pk_to_sku.get(int(sp_pk), "")


def compute_po_line_diff(
    file_lines: list,           # list[SupplierOrderLine]
    po_lines: list,             # list[PurchaseOrderLineItem]
    sku_to_supplier_part_pk: dict,
) -> POLineDiff:
    """Diff a SupplierOrder against an existing PO's line items.

    Returns three buckets keyed by SKU:
      to_add     SupplierOrderLines whose SKU is not yet in the PO
      to_update  Existing items whose qty or price disagrees with the file
      to_delete  PO items whose SKU is no longer in the file

    *sku_to_supplier_part_pk* lets the indexer recover SKUs from PO line
    items that were created without ``reference`` (older / hand-made POs).
    """
    sp_pk_to_sku = {v: k for k, v in sku_to_supplier_part_pk.items()}

    # Index file by SKU (last write wins for duplicate-SKU rows — shouldn't
    # happen with real Mouser/LCSC exports but defensive against hand-edits)
    by_sku_file: dict[str, SupplierOrderLine] = {fl.sku: fl for fl in file_lines}
    by_sku_po: dict[str, object] = {}
    for li in po_lines:
        sku = _po_line_sku(li, sp_pk_to_sku)
        if sku:
            by_sku_po[sku] = li

    to_add: list = []
    to_update: list = []
    to_delete: list = []

    for sku, fl in by_sku_file.items():
        existing = by_sku_po.get(sku)
        if existing is None:
            to_add.append(fl)
            continue
        ex_qty = int(getattr(existing, "quantity", 0))
        ex_price = float(getattr(existing, "purchase_price", 0) or 0)
        qty_diff = ex_qty != fl.qty
        price_diff = not math.isclose(ex_price, fl.unit_price, abs_tol=_PRICE_EPSILON)
        if qty_diff or price_diff:
            to_update.append(LineItemAction(
                line_item=existing,
                new_quantity=fl.qty,
                new_price=fl.unit_price,
            ))

    for sku, li in by_sku_po.items():
        if sku not in by_sku_file:
            to_delete.append(li)

    return POLineDiff(to_add=to_add, to_update=to_update, to_delete=to_delete)


# Module-private numeric constants used by the upsert/diff machinery.
# Grouped here so the InvenTree status codes and the float-comparison
# tolerance live in one place.
_STATUS_PENDING = 10
_STATUS_PLACED = 20
_STATUS_COMPLETE = 30
_STOCK_STATUS_OK = 10  # InvenTree StockItem status code "OK"
_PRICE_EPSILON = 1e-6  # tolerance for float price comparison


@dataclass
class UpsertReport:
    """Result of upsert_purchase_order — used by the CLI for summary print."""
    action: str               # CREATED | RECONCILED | IN_SYNC | DRY_RUN_*
    po_reference: str
    lines_added: int = 0
    lines_updated: int = 0
    lines_deleted: int = 0


def _next_po_reference(api: InvenTreeAPI) -> str:
    """Read the next valid PurchaseOrder.reference from the server.

    InvenTree's OPTIONS response for /api/order/po/ includes a computed
    ``actions.POST.reference.default`` that is the next reference matching
    the server's configured pattern (e.g. ``"PO-0006"`` for the default
    ``PO-{ref:04d}`` pattern). This is the same mechanism the React UI
    uses to pre-fill the Create-PO form.

    Raises RuntimeError if the OPTIONS response is missing the field or
    the request fails — the importer can't reliably create POs without
    knowing the next valid reference, so we fail loud rather than guess.
    """
    try:
        resp = api.request(PurchaseOrder.URL, method="OPTIONS")
        body = resp.json()
        return body["actions"]["POST"]["reference"]["default"]
    # Broad except is intentional: HTTPError/ConnectionError/KeyError/TypeError/
    # JSONDecodeError all map to the same caller-visible contract — fail loud.
    except Exception as exc:
        host = getattr(api, "base_url", "<unknown>")
        raise RuntimeError(
            f"Failed to read next PurchaseOrder.reference from "
            f"OPTIONS {host}{PurchaseOrder.URL} "
            f"(expected actions.POST.reference.default): {exc}"
        ) from exc


def _find_po(api: InvenTreeAPI, supplier_pk: int, supplier_reference: str):
    """Locate a PurchaseOrder by supplier + supplier_reference.

    The server-side ``?supplier_reference=`` filter is silently ignored
    (verified empirically against InvenTree 1.3.4 — all POs for the
    supplier come back regardless of the filter value), so we list all
    POs for the supplier and post-filter on supplier_reference. Same
    defensive pattern as ``find_part_by_name`` in ``client.py``.

    The supplier= filter is also post-filtered: some InvenTree versions
    serialize FKs as strings, so we coerce to int before comparing and
    skip the check when the value isn't numeric.

    Returns the first match (or None). Multiple matches are not
    expected — supplier_reference is the operational identifier — and
    we don't warn on them to keep the path simple; in practice a
    duplicate would be a data-quality issue the operator should resolve
    in the UI.
    """
    matches = PurchaseOrder.list(api, supplier=supplier_pk)
    for po in matches:
        # Supplier post-filter: server-side ?supplier= filter is unreliable on
        # some InvenTree versions; verify locally. Same pattern as below for
        # supplier_reference and as find_part_by_name in client.py.
        po_supplier = getattr(po, "supplier", None)
        if po_supplier is not None and not isinstance(po_supplier, bool):
            try:
                po_supplier_pk = int(po_supplier)
            except (TypeError, ValueError):
                po_supplier_pk = None
            if po_supplier_pk is not None and po_supplier_pk != supplier_pk:
                continue
        # supplier_reference post-filter — server-side ?supplier_reference=
        # filter is empirically ignored (InvenTree 1.3.4, verified by direct
        # curl). The supplier_reference field carries the Mouser/LCSC order ID.
        po_supplier_ref = getattr(po, "supplier_reference", None)
        if isinstance(po_supplier_ref, str) and po_supplier_ref == supplier_reference:
            return po
    return None


def upsert_purchase_order(
    api: InvenTreeAPI,
    order: SupplierOrder,
    supplier,                                # Company
    sku_to_supplier_part: dict,              # dict[str, SupplierPart]
    receive_location,                        # StockLocation
    *,
    dry_run: bool = False,
) -> UpsertReport:
    """Create or reconcile a PurchaseOrder for *order*, then receiveAll.

    Three paths keyed off the existing PO's status:
      A — no PO matches reference → create + add all lines + issue + receive.
      B — PO exists in PENDING(10)/PLACED(20) → reconcile to file + receive.
      C — PO exists in COMPLETE(30+) → diff against file:
            empty → log IN_SYNC, return.
            non-empty → raise RuntimeError with a drift report (no writes).

    dry_run=True suppresses every InvenTree mutation; the planned action
    and counts are still returned via UpsertReport for the CLI's dry-run
    summary.
    """
    sku_to_sp_pk = {sku: sp.pk for sku, sp in sku_to_supplier_part.items()}

    # Dedup file lines by SKU (last occurrence wins) — matches the
    # `by_sku_file = {fl.sku: fl for fl in file_lines}` semantics of
    # compute_po_line_diff. Without this, Pfad A would create duplicate
    # PO line items with the same `reference`, which Pfad B/C diff would
    # then silently ignore (leaving them un-reconcilable on re-run).
    deduped_lines = list({line.sku: line for line in order.lines}.values())
    if len(deduped_lines) < len(order.lines):
        logger.warning(
            "Order %s has %d duplicate SKU rows; deduplicated to %d (last wins).",
            order.reference,
            len(order.lines) - len(deduped_lines),
            len(deduped_lines),
        )

    existing = _find_po(api, supplier.pk, supplier_reference=order.reference)

    if existing is None:
        # Pfad A
        if dry_run:
            return UpsertReport(
                action="DRY_RUN_CREATE", po_reference=order.reference,
                lines_added=len(deduped_lines),
            )
        po = PurchaseOrder.create(api, {
            "supplier": supplier.pk,
            "reference": order.reference,
            "description": f"Imported from {order.supplier_name} order {order.reference}",
            **({"target_date": order.order_date} if order.order_date else {}),
        })
        for line in deduped_lines:
            sp = sku_to_supplier_part[line.sku]
            po.addLineItem(
                part=sp.pk,
                quantity=line.qty,
                purchase_price=line.unit_price,
                purchase_price_currency=line.currency,
                reference=line.sku,
            )
        po.issue()
        po.receiveAll(location=receive_location.pk, status=_STOCK_STATUS_OK)
        return UpsertReport(
            action="CREATED", po_reference=order.reference,
            lines_added=len(deduped_lines),
        )

    status = int(getattr(existing, "status", 0))
    existing_lines = list(existing.getLineItems())
    diff = compute_po_line_diff(deduped_lines, existing_lines, sku_to_sp_pk)

    if status >= _STATUS_COMPLETE:
        # Pfad C
        if diff.is_empty:
            logger.info("PO %s already COMPLETE and matches file — no-op.",
                        order.reference)
            return UpsertReport(action="IN_SYNC", po_reference=order.reference)
        raise RuntimeError(
            f"PO {order.reference} ({order.supplier_name}) is COMPLETE but "
            f"differs from the source file:\n"
            f"{diff.format_report()}\n"
            f"Resolve manually in the InvenTree UI (or delete the PO + "
            f"associated StockItems) and re-run."
        )

    # Pfad B
    # Guard against deleting partially-received lines — would orphan StockItems
    # or fail at the API layer. Run BEFORE any add/update mutation so a
    # rejected reconcile leaves the PO untouched (atomic-or-nothing).
    partial = [li for li in diff.to_delete
               if int(getattr(li, "received", 0) or 0) > 0]
    if partial:
        details = ", ".join(
            f"{getattr(li, 'reference', '') or f'line#{li.pk}'} "
            f"(received={int(getattr(li, 'received', 0) or 0)})"
            for li in partial
        )
        raise RuntimeError(
            f"PO {order.reference} ({order.supplier_name}) has line item(s) "
            f"with received stock that the source file no longer lists: "
            f"{details}. Resolve manually in the InvenTree UI (delete the "
            f"StockItems and the line, or restore the line in the source file)."
        )

    if dry_run:
        return UpsertReport(
            action="DRY_RUN_RECONCILE", po_reference=order.reference,
            lines_added=len(diff.to_add),
            lines_updated=len(diff.to_update),
            lines_deleted=len(diff.to_delete),
        )

    for sl in diff.to_add:
        sp = sku_to_supplier_part[sl.sku]
        existing.addLineItem(
            part=sp.pk,
            quantity=sl.qty,
            purchase_price=sl.unit_price,
            purchase_price_currency=sl.currency,
            reference=sl.sku,
        )
    for upd in diff.to_update:
        upd.line_item.save({
            "quantity": upd.new_quantity,
            "purchase_price": upd.new_price,
        })
    for li in diff.to_delete:
        li.delete()

    if status == _STATUS_PENDING:
        existing.issue()
    existing.receiveAll(location=receive_location.pk, status=_STOCK_STATUS_OK)

    return UpsertReport(
        action="RECONCILED", po_reference=order.reference,
        lines_added=len(diff.to_add),
        lines_updated=len(diff.to_update),
        lines_deleted=len(diff.to_delete),
    )
