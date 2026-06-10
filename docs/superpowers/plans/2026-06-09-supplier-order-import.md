# Supplier Order Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `import_supplier_order.py` CLI + `inventree_sync/order_import.py` library module that imports historical Mouser-XLS and LCSC-CSV supplier orders into InvenTree as PurchaseOrders with LineItems and received StockItems. File is source-of-truth on re-runs; idempotent reconciliation for open POs, loud-fail for completed-PO drift.

**Architecture:** Thin CLI on top of a new `order_import` module inside the existing `inventree_sync` package. Reuses the BOM-export library's part-dedup/create primitives (`find_existing_part`, `find_part_by_mpn_and_manufacturer`, `find_part_by_name`, `create_part_in_inventree`, `ensure_supplier_parts`). PurchaseOrder side uses `inventree.purchase_order.PurchaseOrder` directly via three code paths: new-PO creation, open-PO reconciliation, completed-PO drift-check.

**Tech Stack:** Python 3.13, `inventree==0.23.1` Python client, `xlrd==2.0.1` for legacy `.xls`, stdlib `csv` for LCSC files, pytest for tests.

**Spec:** `docs/superpowers/specs/2026-06-09-supplier-order-import-design.md`

**Working directory for all commands:** `/home/pbuchegger/OE5XRX/HW-Module-CI`

---

## Conventions for every task

- All Python tests use `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` at the top so `inventree_sync` and the script imports resolve from `scripts/`. Follow the pattern from `tests/test_fetch_and_merge.py`.
- Activate the venv before pytest: `source .venv/bin/activate && pytest <path>`.
- Commit messages follow the existing convention: `feat(inventree-sync): <subject>` or `feat(import-orders): <subject>`.
- Tests are TDD-first: write the failing test, confirm it fails, then implement, confirm pass, commit.

---

## File Structure

| File | Purpose |
|---|---|
| `scripts/inventree_sync/order_import.py` | Library module: dataclasses, parsers, reconciliation, PO-upsert |
| `scripts/import_supplier_order.py` | CLI entrypoint, argparse, top-level orchestration |
| `scripts/tests/test_order_import_dataclasses.py` | Dataclass sanity |
| `scripts/tests/test_order_import_lcsc_parser.py` | LCSC-CSV parser unit tests |
| `scripts/tests/test_order_import_mouser_parser.py` | Mouser-XLS parser unit tests |
| `scripts/tests/test_order_import_part_resolution.py` | `ensure_part_for_order_line` mock-based |
| `scripts/tests/test_order_import_diff.py` | `compute_po_line_diff` pure-function tests |
| `scripts/tests/test_order_import_upsert.py` | PO upsert (Pfad A/B/C) mock-based |
| `scripts/tests/test_order_import_cli.py` | argparse + main() smoke (no real API) |
| `scripts/requirements.txt` | Add `xlrd==2.0.1` |
| `README.md` | Add usage section for the new CLI |

---

## Task 1: Setup — Add xlrd dependency and create empty module skeletons

**Files:**
- Modify: `scripts/requirements.txt`
- Create: `scripts/inventree_sync/order_import.py`
- Create: `scripts/import_supplier_order.py`

- [ ] **Step 1: Add xlrd to requirements**

Edit `scripts/requirements.txt`. Append `xlrd==2.0.1` to the end (after `PyYAML==6.0.3`). The pinning rationale is documented at the top of the file already.

The file should look like:

```
# Pinned to current stable versions. Bump intentionally via a HW-Module-CI PR
# (`pip index versions <pkg>` to check latest) so consumer release workflows
# stay reproducible — unpinned deps were causing random-day-breakage risk.
inventree==0.23.1
requests==2.34.0
beautifulsoup4==4.14.3
Pillow==12.2.0
numpy==2.4.4
cairosvg==2.9.0
PyYAML==6.0.3
xlrd==2.0.1
```

- [ ] **Step 2: Install the new dep into the existing venv**

Run: `source .venv/bin/activate && pip install xlrd==2.0.1`

Expected: "Successfully installed xlrd-2.0.1" or "Requirement already satisfied".

- [ ] **Step 3: Verify xlrd can open the real Mouser file**

Run:
```
source .venv/bin/activate && python3 -c "
import xlrd
book = xlrd.open_workbook('/home/pbuchegger/OE5XRX/inventree_import/275708282.xls')
sheet = book.sheet_by_index(0)
print('rows:', sheet.nrows, 'cols:', sheet.ncols)
print('header:', sheet.row_values(0))
print('row 1:', sheet.row_values(1))
"
```

Expected: prints `rows: 28` (header + 27 data rows), the header columns including `Sales Order No:`, `Mouser No:`, `Mfr. No:`, `Desc.:`, `Order Qty.`, `Price (EUR)`, and a sample row 1 with values.

- [ ] **Step 4: Create empty module skeletons**

Write `scripts/inventree_sync/order_import.py` with just the docstring:

```python
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
```

Write `scripts/import_supplier_order.py` with just a shebang + module docstring (we fill it in Task 10):

```python
#!/usr/bin/env python3
"""
import_supplier_order.py – CLI to import a Mouser-XLS or LCSC-CSV supplier
order into InvenTree.  See `inventree_sync/order_import.py` for the
library-side logic.
"""
```

Make the script executable: `chmod +x scripts/import_supplier_order.py`.

- [ ] **Step 5: Commit**

```
git add scripts/requirements.txt scripts/inventree_sync/order_import.py scripts/import_supplier_order.py
git commit -m "feat(import-orders): scaffold order_import module + CLI entrypoint

Adds xlrd==2.0.1 for legacy .xls reading. Empty skeletons that the
follow-up tasks fill out per the 2026-06-09-supplier-order-import-design spec."
```

---

## Task 2: Dataclasses — SupplierOrderLine + SupplierOrder

**Files:**
- Modify: `scripts/inventree_sync/order_import.py`
- Create: `scripts/tests/test_order_import_dataclasses.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_order_import_dataclasses.py`:

```python
"""Sanity tests for the order_import dataclasses."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import SupplierOrder, SupplierOrderLine  # noqa: E402


def test_supplier_order_line_defaults_package_to_empty_string():
    line = SupplierOrderLine(
        sku="C1739",
        qty=100,
        unit_price=0.0074,
        currency="USD",
        mpn="0805B333K500NT",
        mfr_name="FH",
        description="33nF X7R 0805",
    )
    assert line.package == ""


def test_supplier_order_holds_a_list_of_lines():
    order = SupplierOrder(
        supplier_name="LCSC",
        reference="WM2504270070",
        order_date=None,
        currency="USD",
        lines=[],
    )
    assert order.lines == []
    assert order.supplier_name == "LCSC"


def test_supplier_order_line_is_hashable_via_sku():
    """SKU is the reconciliation key; lines from a file are deduped on it."""
    a = SupplierOrderLine(sku="X", qty=1, unit_price=0.0, currency="EUR",
                          mpn="", mfr_name="", description="")
    b = SupplierOrderLine(sku="X", qty=2, unit_price=1.0, currency="EUR",
                          mpn="", mfr_name="", description="")
    # We don't require hashability — instead we test that SKU is a string and
    # can be used as a dict key downstream.
    d = {a.sku: a, b.sku: b}
    assert d["X"].qty == 2  # second insert wins (last-write-wins semantics)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_dataclasses.py -v`
Expected: `ImportError: cannot import name 'SupplierOrder'` (or similar).

- [ ] **Step 3: Implement the dataclasses**

Append to `scripts/inventree_sync/order_import.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_dataclasses.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_dataclasses.py
git commit -m "feat(import-orders): SupplierOrder + SupplierOrderLine dataclasses

SKU is the reconciliation key across re-runs (lookup key in compute_po_line_diff).
LCSC supplies a package column; Mouser doesn't — default '' on SupplierOrderLine."
```

---

## Task 3: LCSC CSV parser

**Files:**
- Modify: `scripts/inventree_sync/order_import.py`
- Create: `scripts/tests/test_order_import_lcsc_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_order_import_lcsc_parser.py`:

```python
"""Unit tests for parse_lcsc_csv."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import SupplierOrderLine, parse_lcsc_csv  # noqa: E402


_FIXTURE_CSV = (
    "LCSC Part Number,Manufacture Part Number,Manufacturer,Customer NO.,"
    "Package,Description,RoHS,Quantity,Unit Price($),Ext.Price($),"
    "Estimated lead time (business days),Updated lead time,Date Code / Lot No.\n"
    "C1739,0805B333K500NT,FH (Guangdong Fenghua Advanced Tech),,0805,"
    "33nF +-10% 50V Ceramic Capacitor X7R 0805,YES,100,0.0074,0.74,,,\n"
    "C17513,0805W8F1001T5E,UNI-ROYAL(Uniroyal Elec),,0805,"
    "1kOhm +-1% 125mW 0805 Thick Film Resistor,YES,100,0.0017,0.17,,,\n"
)


def _write_fixture(tmp_path: Path, basename: str, content: str) -> Path:
    f = tmp_path / basename
    f.write_text(content, encoding="utf-8")
    return f


def test_parse_lcsc_derives_reference_from_filename(tmp_path):
    f = _write_fixture(tmp_path, "LCSC__WM2504270070_20260610043835.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert order.reference == "WM2504270070"


def test_parse_lcsc_sets_supplier_name_and_currency(tmp_path):
    f = _write_fixture(tmp_path, "LCSC__WM2504270070_20260610043835.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert order.supplier_name == "LCSC"
    assert order.currency == "USD"
    assert order.order_date is None


def test_parse_lcsc_two_lines_with_full_fields(tmp_path):
    f = _write_fixture(tmp_path, "LCSC__WM2504270070_20260610043835.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert len(order.lines) == 2

    first = order.lines[0]
    assert first.sku == "C1739"
    assert first.mpn == "0805B333K500NT"
    assert first.mfr_name == "FH (Guangdong Fenghua Advanced Tech)"
    assert first.qty == 100
    assert first.unit_price == 0.0074
    assert first.currency == "USD"
    assert first.package == "0805"
    assert "Ceramic Capacitor" in first.description

    second = order.lines[1]
    assert second.sku == "C17513"
    assert second.qty == 100
    assert second.unit_price == 0.0017


def test_parse_lcsc_falls_back_when_filename_does_not_match(tmp_path):
    f = _write_fixture(tmp_path, "random.csv", _FIXTURE_CSV)
    order = parse_lcsc_csv(f)
    assert order.reference == "lcsc-unknown"


def test_parse_lcsc_skips_rows_with_empty_sku(tmp_path):
    csv_with_blank = _FIXTURE_CSV + ",,,,,,,,,,,,\n"  # trailing empty data row
    f = _write_fixture(tmp_path, "LCSC__X_1.csv", csv_with_blank)
    order = parse_lcsc_csv(f)
    assert len(order.lines) == 2  # blank row dropped


def test_parse_lcsc_strips_whitespace_in_sku_and_mpn(tmp_path):
    padded = _FIXTURE_CSV.replace("C1739,0805B333K500NT", "  C1739  ,  0805B333K500NT  ")
    f = _write_fixture(tmp_path, "LCSC__X_1.csv", padded)
    order = parse_lcsc_csv(f)
    assert order.lines[0].sku == "C1739"
    assert order.lines[0].mpn == "0805B333K500NT"


def test_parse_lcsc_invalid_quantity_raises_value_error(tmp_path):
    bad = _FIXTURE_CSV.replace(",YES,100,0.0074", ",YES,not-a-number,0.0074")
    f = _write_fixture(tmp_path, "LCSC__X_1.csv", bad)
    import pytest
    with pytest.raises(ValueError):
        parse_lcsc_csv(f)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_lcsc_parser.py -v`
Expected: all fail with `ImportError: cannot import name 'parse_lcsc_csv'`.

- [ ] **Step 3: Implement the parser**

Append to `scripts/inventree_sync/order_import.py`:

```python
import csv
import re
from pathlib import Path

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_lcsc_parser.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_lcsc_parser.py
git commit -m "feat(import-orders): LCSC CSV parser

Reference derives from canonical filename LCSC__<ref>_<ts>.csv; falls
back to 'lcsc-unknown' if the pattern doesn't match. Currency hard-
coded USD — LCSC always exports dollar prices."
```

---

## Task 4: Mouser XLS parser

**Files:**
- Modify: `scripts/inventree_sync/order_import.py`
- Create: `scripts/tests/test_order_import_mouser_parser.py`

The XLS reader is split in two pieces so the transformation logic is unit-testable without an actual .xls fixture on disk. `_read_mouser_rows(path)` reads xlrd → list of dicts; `_rows_to_mouser_order(rows)` is pure-Python. Unit tests target the pure function with synthetic dicts.

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_order_import_mouser_parser.py`:

```python
"""Unit tests for the Mouser-XLS parser (pure transformation layer)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import (  # noqa: E402
    _parse_mouser_price,
    _parse_mouser_date,
    _rows_to_mouser_order,
)


def test_parse_mouser_price_european_format():
    assert _parse_mouser_price("€ 0,381") == 0.381
    assert _parse_mouser_price("€ 0,02") == 0.02
    assert _parse_mouser_price("€ 1,16") == 1.16
    assert _parse_mouser_price("€ 1.234,56") == 1234.56  # thousands sep


def test_parse_mouser_price_us_format():
    assert _parse_mouser_price("$ 1.23") == 1.23
    assert _parse_mouser_price("0.0074") == 0.0074


def test_parse_mouser_price_empty_returns_zero():
    assert _parse_mouser_price("") == 0.0
    assert _parse_mouser_price(None) == 0.0


def test_parse_mouser_date_to_iso():
    # XLS file uses "07-Jul-25" — 2-digit year, English month abbreviation.
    assert _parse_mouser_date("07-Jul-25") == "2025-07-07"
    assert _parse_mouser_date("31-Dec-99") == "2099-12-31"


def test_parse_mouser_date_invalid_returns_none():
    assert _parse_mouser_date("") is None
    assert _parse_mouser_date("not a date") is None


def _row(**overrides):
    base = {
        "Sales Order No:": "275708282",
        "Order Date:": "07-Jul-25",
        "Mouser No:": "576-0297003.L",
        "Mfr. No:": "0297003.L",
        "Desc.:": "Automotive Fuses 32V 3A MINI",
        "Order Qty.": 10,
        "Price (EUR)": "€ 0,381",
    }
    base.update(overrides)
    return base


def test_rows_to_mouser_order_basic_header():
    rows = [_row(), _row(**{
        "Mouser No:": "667-ERA-6AEB221V",
        "Mfr. No:": "ERA-6AEB221V",
        "Desc.:": "Thin Film Resistors 0805 220ohm",
        "Order Qty.": 20,
        "Price (EUR)": "€ 0,052",
    })]
    order = _rows_to_mouser_order(rows)
    assert order.supplier_name == "Mouser"
    assert order.reference == "275708282"
    assert order.order_date == "2025-07-07"
    assert order.currency == "EUR"
    assert len(order.lines) == 2


def test_rows_to_mouser_order_line_fields():
    rows = [_row()]
    order = _rows_to_mouser_order(rows)
    line = order.lines[0]
    assert line.sku == "576-0297003.L"
    assert line.mpn == "0297003.L"
    assert line.mfr_name == ""  # not in file, filled later by API
    assert line.qty == 10
    assert line.unit_price == 0.381
    assert line.currency == "EUR"
    assert line.package == ""
    assert "Automotive Fuses" in line.description


def test_rows_to_mouser_order_skips_empty_rows():
    rows = [_row(), _row(**{"Mouser No:": "", "Mfr. No:": ""})]  # second is blank
    order = _rows_to_mouser_order(rows)
    assert len(order.lines) == 1


def test_rows_to_mouser_order_qty_as_string_coerces():
    rows = [_row(**{"Order Qty.": "15"})]
    order = _rows_to_mouser_order(rows)
    assert order.lines[0].qty == 15


def test_rows_to_mouser_order_empty_input_uses_unknown_reference():
    order = _rows_to_mouser_order([])
    assert order.reference == "mouser-unknown"
    assert order.order_date is None
    assert order.lines == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_mouser_parser.py -v`
Expected: all fail with ImportError for `_parse_mouser_price`, `_parse_mouser_date`, `_rows_to_mouser_order`.

- [ ] **Step 3: Implement the helpers and the pure transformation**

Append to `scripts/inventree_sync/order_import.py`:

```python
from datetime import datetime
from typing import Optional


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_mouser_parser.py -v`
Expected: 10 passed.

- [ ] **Step 5: Smoke-test the file-reading layer against the real file**

Run:
```
source .venv/bin/activate && python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from inventree_sync.order_import import parse_mouser_xls
order = parse_mouser_xls(Path('/home/pbuchegger/OE5XRX/inventree_import/275708282.xls'))
print(f'supplier={order.supplier_name} ref={order.reference} date={order.order_date} lines={len(order.lines)}')
print(f'first: sku={order.lines[0].sku} qty={order.lines[0].qty} price={order.lines[0].unit_price} mpn={order.lines[0].mpn}')
"
```

Expected output (approximate):
```
supplier=Mouser ref=275708282 date=2025-07-07 lines=27
first: sku=576-0297003.L qty=10 price=0.381 mpn=0297003.L
```

If any field is off (e.g. column name mismatch in xlrd vs pandas), fix the key strings in `_rows_to_mouser_order` and re-run the unit tests.

- [ ] **Step 6: Commit**

```
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_mouser_parser.py
git commit -m "feat(import-orders): Mouser XLS parser via xlrd

Splits xls-IO (_read_mouser_rows) from the pure transformation
(_rows_to_mouser_order) so the transformation is unit-testable without
a binary fixture. Date parser converts '07-Jul-25' → '2025-07-07';
price parser handles European-format euro strings."
```

---

## Task 5: Receive-location helper

**Files:**
- Modify: `scripts/inventree_sync/order_import.py`

This helper resolves the InvenTree StockLocation we'll receive-into. CLI default is the name `"Lager"`; user may override via `--location`. Fall back to the first top-level location if nothing matches; fail-loud if even that's empty.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_order_import_receive_location.py`:

```python
"""Unit tests for get_receive_location."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import get_receive_location  # noqa: E402


def _stock_location(pk: int, name: str, parent: int | None = None):
    loc = MagicMock()
    loc.pk = pk
    loc.name = name
    loc.parent = parent
    return loc


def test_returns_named_location_when_present():
    api = MagicMock()
    target = _stock_location(7, "Lager")
    with patch("inventree_sync.order_import.StockLocation") as SL:
        SL.list.return_value = [target]
        result = get_receive_location(api, "Lager")
    assert result is target


def test_falls_back_to_first_top_level_when_named_missing():
    api = MagicMock()
    fallback = _stock_location(3, "Default", parent=None)
    with patch("inventree_sync.order_import.StockLocation") as SL:
        SL.list.side_effect = [
            [],                      # name= lookup returns empty
            [fallback,               # full list — first top-level
             _stock_location(4, "Sub", parent=3)],
        ]
        result = get_receive_location(api, "DoesNotExist")
    assert result is fallback


def test_raises_when_no_locations_exist_at_all():
    api = MagicMock()
    with patch("inventree_sync.order_import.StockLocation") as SL:
        SL.list.side_effect = [[], []]  # name= empty, full empty
        with pytest.raises(RuntimeError) as exc:
            get_receive_location(api, "Lager")
    assert "StockLocation" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_receive_location.py -v`
Expected: 3 fails with `ImportError: cannot import name 'get_receive_location'`.

- [ ] **Step 3: Implement the helper**

Append to `scripts/inventree_sync/order_import.py`:

```python
import logging

from inventree.api import InvenTreeAPI
from inventree.stock import StockLocation

logger = logging.getLogger(__name__)


def get_receive_location(api: InvenTreeAPI, name: str) -> StockLocation:
    """Resolve the receive-into StockLocation.

    Order of attempts:
      1. Exact name match (case-sensitive — InvenTree's filter is exact).
      2. First top-level StockLocation (parent is None).
      3. Raise RuntimeError if no StockLocation exists at all.

    The fallback exists so a fresh InvenTree install with just a single
    default location can be imported into without forcing the user to
    rename it to "Lager" first.
    """
    try:
        matches = StockLocation.list(api, name=name)
        for loc in matches:
            if loc.name == name:
                return loc
    except Exception as exc:
        logger.warning("StockLocation lookup for %r failed: %s", name, exc)

    try:
        all_locs = StockLocation.list(api)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot list StockLocations: {exc}. "
            "Verify INVENTREE_API_HOST/TOKEN and that the server is up."
        ) from exc

    top_level = [l for l in all_locs if getattr(l, "parent", None) in (None, 0)]
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_receive_location.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_receive_location.py
git commit -m "feat(import-orders): get_receive_location helper

Resolves InvenTree StockLocation by name; falls back to first top-level
when name doesn't match; raises if no locations exist at all."
```

---

## Task 6: Part-resolution helper — ensure_part_for_order_line

**Files:**
- Modify: `scripts/inventree_sync/order_import.py`
- Create: `scripts/tests/test_order_import_part_resolution.py`

This function resolves a `SupplierOrderLine` to `(Part, SupplierPart)`, calling the existing `inventree_sync.client` helpers. It mirrors the dedup chain in `part_manager.ensure_parts_exist` but adapted for the supplier-order context (no KiCad fields).

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_order_import_part_resolution.py`:

```python
"""Mock-based tests for ensure_part_for_order_line."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.models import PartData  # noqa: E402
from inventree_sync.order_import import (  # noqa: E402
    SupplierOrderLine,
    ensure_part_for_order_line,
)


def _line(sku="C1739", supplier="LCSC"):
    line = SupplierOrderLine(
        sku=sku, qty=100, unit_price=0.01, currency="USD",
        mpn="0805B333K500NT", mfr_name="FH", description="33nF",
        package="0805",
    )
    return line, supplier


def _supplier_part_mock(pk=42, sku="C1739", part_pk=101):
    sp = MagicMock()
    sp.pk = pk
    sp.SKU = sku
    sp.part = part_pk
    return sp


def _part_mock(pk=101):
    p = MagicMock()
    p.pk = pk
    return p


def test_existing_part_via_sku_returns_part_and_supplier_part():
    api = MagicMock()
    lcsc_fetcher = MagicMock()
    mouser_fetcher = MagicMock()
    lcsc_supplier = MagicMock(); lcsc_supplier.pk = 1; lcsc_supplier.name = "LCSC"
    mouser_supplier = MagicMock(); mouser_supplier.pk = 2; mouser_supplier.name = "Mouser"

    line, supplier_kind = _line()

    with patch("inventree_sync.order_import.find_existing_part") as find_exist, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        find_exist.return_value = _part_mock(pk=101)
        SP.list.return_value = [_supplier_part_mock(pk=42, sku="C1739", part_pk=101)]
        part, supplier_part = ensure_part_for_order_line(
            api, line, supplier_kind, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map={},
        )
    assert part.pk == 101
    assert supplier_part.pk == 42
    # Fetcher must NOT be called when SKU lookup already hit
    lcsc_fetcher.fetch_by_sku.assert_not_called()
    mouser_fetcher.fetch.assert_not_called()


def test_routes_to_lcsc_fetcher_for_lcsc_line():
    api = MagicMock()
    lcsc_fetcher = MagicMock()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    mouser_fetcher = MagicMock()
    line, supplier_kind = _line(supplier="LCSC")

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        rcat.return_value = MagicMock(pk=9)
        created = _part_mock(pk=202)
        create.return_value = created
        SP.list.return_value = [_supplier_part_mock(pk=55, sku="C1739", part_pk=202)]

        part, sp = ensure_part_for_order_line(
            api, line, supplier_kind,
            lcsc_fetcher, MagicMock(),
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    lcsc_fetcher.fetch_by_sku.assert_called_once_with("C1739")
    create.assert_called_once()
    # Verify SKU lists routed correctly:
    kwargs = create.call_args.kwargs
    assert kwargs["lcsc_skus"] == ["C1739"]
    assert kwargs["mouser_skus"] == []
    assert part.pk == 202
    assert sp.pk == 55


def test_routes_to_mouser_fetcher_for_mouser_line():
    line = SupplierOrderLine(
        sku="576-0297003.L", qty=10, unit_price=0.381, currency="EUR",
        mpn="0297003.L", mfr_name="", description="Fuse",
    )
    mouser_fetcher = MagicMock()
    mouser_fetcher.fetch.return_value = PartData(
        mpn="0297003.L", manufacturer="Littelfuse",
        description="Fuse 3A", mouser_sku="576-0297003.L",
    )

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        rcat.return_value = MagicMock(pk=9)
        create.return_value = _part_mock(pk=303)
        SP.list.return_value = [_supplier_part_mock(pk=66, sku="576-0297003.L", part_pk=303)]

        part, sp = ensure_part_for_order_line(
            MagicMock(), line, "Mouser",
            MagicMock(), mouser_fetcher,
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    mouser_fetcher.fetch.assert_called_once_with("576-0297003.L")
    kwargs = create.call_args.kwargs
    assert kwargs["lcsc_skus"] == []
    assert kwargs["mouser_skus"] == ["576-0297003.L"]


def test_existing_via_mpn_links_supplier_part():
    line, supplier_kind = _line(supplier="LCSC")
    lcsc_fetcher = MagicMock()
    lcsc_fetcher.fetch_by_sku.return_value = PartData(
        mpn="0805B333K500NT", manufacturer="FH",
        description="33nF", lcsc_sku="C1739",
    )
    existing = _part_mock(pk=404)

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.ensure_supplier_parts") as esp, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = existing
        SP.list.return_value = [_supplier_part_mock(pk=77, sku="C1739", part_pk=404)]

        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, MagicMock(),
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    esp.assert_called_once()
    assert part.pk == 404
    assert sp.pk == 77


def test_fetcher_failure_falls_back_to_file_data():
    """If both LCSC and Mouser APIs return None, build PartData from the line."""
    line, supplier_kind = _line(supplier="LCSC")
    lcsc_fetcher = MagicMock()
    lcsc_fetcher.fetch_by_sku.return_value = None

    with patch("inventree_sync.order_import.find_existing_part") as fe, \
         patch("inventree_sync.order_import.find_part_by_mpn_and_manufacturer") as fmpn, \
         patch("inventree_sync.order_import.find_part_by_name") as fname, \
         patch("inventree_sync.order_import.resolve_part_category") as rcat, \
         patch("inventree_sync.order_import.create_part_in_inventree") as create, \
         patch("inventree_sync.order_import.SupplierPart") as SP:
        fe.return_value = None
        fmpn.return_value = None
        fname.return_value = None
        rcat.return_value = MagicMock(pk=9)
        create.return_value = _part_mock(pk=505)
        SP.list.return_value = [_supplier_part_mock(pk=88, sku="C1739", part_pk=505)]

        part, sp = ensure_part_for_order_line(
            MagicMock(), line, supplier_kind,
            lcsc_fetcher, MagicMock(),
            MagicMock(pk=1), MagicMock(pk=2),
            category_map={},
        )

    # create called with PartData built from line, not None
    args, kwargs = create.call_args[0], create.call_args.kwargs
    part_data = args[2] if len(args) > 2 else kwargs["part_data"]
    assert part_data.mpn == "0805B333K500NT"
    assert part_data.manufacturer == "FH"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_part_resolution.py -v`
Expected: 5 fails with `ImportError: cannot import name 'ensure_part_for_order_line'`.

- [ ] **Step 3: Implement the function**

Append to `scripts/inventree_sync/order_import.py`:

```python
from inventree.company import Company, SupplierPart

from .categories import resolve_part_category
from .client import (
    create_part_in_inventree,
    ensure_supplier_parts,
    find_existing_part,
    find_part_by_mpn_and_manufacturer,
    find_part_by_name,
)
from .fetchers import LCSCFetcher, MouserFetcher
from .models import PartData


def _lookup_supplier_part(api: InvenTreeAPI, sku: str):
    """Return the SupplierPart for *sku* or raise RuntimeError.

    Used after create_part_in_inventree to recover the SupplierPart PK
    (the create call returns only the Part).
    """
    sps = SupplierPart.list(api, SKU=sku)
    for sp in sps:
        if sp.SKU == sku:
            return sp
    raise RuntimeError(
        f"SupplierPart for SKU {sku!r} missing after create — "
        "did create_part_in_inventree's SupplierPart create fail silently?"
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
    supplier_kind: str,                  # "LCSC" or "Mouser"
    lcsc_fetcher: LCSCFetcher,
    mouser_fetcher: MouserFetcher,
    lcsc_supplier: Company,
    mouser_supplier: Company,
    category_map: dict,
) -> tuple:
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
    """
    is_lcsc = supplier_kind == "LCSC"
    lcsc_skus = [line.sku] if is_lcsc else []
    mouser_skus = [line.sku] if not is_lcsc else []

    # 1. SKU lookup
    existing = find_existing_part(api, lcsc_skus, mouser_skus)
    if existing is not None:
        return existing, _lookup_supplier_part(api, line.sku)

    # 2. Supplier fetch
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
        ensure_supplier_parts(
            api, by_name, part_data,
            lcsc_supplier, mouser_supplier,
            lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
        )
        return by_name, _lookup_supplier_part(api, line.sku)

    # 5. Create
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_part_resolution.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_part_resolution.py
git commit -m "feat(import-orders): ensure_part_for_order_line dedup chain

Mirrors part_manager.ensure_parts_exist but with a file-row PartData
fallback when the supplier API fails. Returns (Part, SupplierPart) so
the upsert step has the SupplierPart-PK needed for PO LineItems."
```

---

## Task 7: Pure diff function — compute_po_line_diff

**Files:**
- Modify: `scripts/inventree_sync/order_import.py`
- Create: `scripts/tests/test_order_import_diff.py`

`compute_po_line_diff(file_lines, po_lines, sku_to_sp_pk)` is a pure function that returns three lists: `to_add`, `to_update`, `to_delete`. This is what Pfad B applies and what Pfad C checks for drift.

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_order_import_diff.py`:

```python
"""Pure-function tests for compute_po_line_diff."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import (  # noqa: E402
    SupplierOrderLine,
    compute_po_line_diff,
    LineItemAction,
)


def _line(sku, qty, price):
    return SupplierOrderLine(
        sku=sku, qty=qty, unit_price=price, currency="EUR",
        mpn=sku, mfr_name="X", description="",
    )


def _po_line(sku, qty, price, pk):
    """Mock a PurchaseOrderLineItem. Reference holds the SKU."""
    li = MagicMock()
    li.pk = pk
    li.reference = sku
    li.quantity = qty
    li.purchase_price = price
    return li


def test_no_diff_when_identical():
    file_lines = [_line("A", 10, 1.0), _line("B", 5, 2.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1), _po_line("B", 5, 2.0, pk=2)]
    sku_to_sp = {"A": 100, "B": 101}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert diff.to_add == []
    assert diff.to_update == []
    assert diff.to_delete == []
    assert diff.is_empty is True


def test_add_when_file_has_extra_line():
    file_lines = [_line("A", 10, 1.0), _line("B", 5, 2.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1)]
    sku_to_sp = {"A": 100, "B": 101}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_add) == 1
    assert diff.to_add[0].sku == "B"
    assert diff.to_delete == []
    assert diff.to_update == []
    assert diff.is_empty is False


def test_delete_when_po_has_extra_line():
    file_lines = [_line("A", 10, 1.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1), _po_line("B", 5, 2.0, pk=2)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_delete) == 1
    assert diff.to_delete[0].pk == 2


def test_update_when_qty_differs():
    file_lines = [_line("A", 15, 1.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_update) == 1
    upd: LineItemAction = diff.to_update[0]
    assert upd.line_item.pk == 1
    assert upd.new_quantity == 15
    assert upd.new_price == 1.0


def test_update_when_price_differs():
    file_lines = [_line("A", 10, 1.5)]
    po_lines = [_po_line("A", 10, 1.0, pk=1)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert len(diff.to_update) == 1
    assert diff.to_update[0].new_price == 1.5


def test_price_compared_with_epsilon():
    """Floating-point near-equality must not trigger a spurious update."""
    file_lines = [_line("A", 10, 0.1 + 0.2)]  # 0.30000000000000004
    po_lines = [_po_line("A", 10, 0.3, pk=1)]
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    assert diff.to_update == []


def test_indexes_po_by_part_when_reference_missing():
    """Older POs created without `reference` need fallback indexing."""
    file_lines = [_line("A", 10, 1.0)]
    po = MagicMock()
    po.pk = 99
    po.reference = ""    # no SKU here
    po.part = 100        # SupplierPart pk
    po.quantity = 10
    po.purchase_price = 1.0
    sku_to_sp = {"A": 100}
    diff = compute_po_line_diff(file_lines, [po], sku_to_sp)
    assert diff.to_update == [] and diff.to_add == [] and diff.to_delete == []


def test_human_readable_report():
    file_lines = [_line("A", 15, 1.5), _line("B", 5, 2.0)]
    po_lines = [_po_line("A", 10, 1.0, pk=1), _po_line("C", 3, 0.5, pk=3)]
    sku_to_sp = {"A": 100, "B": 101}
    diff = compute_po_line_diff(file_lines, po_lines, sku_to_sp)
    report = diff.format_report()
    assert "ADD" in report and "B" in report
    assert "REMOVE" in report and "C" in report
    assert "UPDATE" in report and "A" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_diff.py -v`
Expected: 8 fails with ImportError for `compute_po_line_diff` / `LineItemAction`.

- [ ] **Step 3: Implement the diff function**

Append to `scripts/inventree_sync/order_import.py`:

```python
import math


_PRICE_EPSILON = 1e-6  # tolerance for float price comparison


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_diff.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_diff.py
git commit -m "feat(import-orders): compute_po_line_diff pure function

Three-bucket diff (add/update/delete) keyed by SKU. Used by both the
PENDING/PLACED reconcile path (applies the diff) and the COMPLETE drift
path (checks is_empty before fail-loud). Price comparison uses math.isclose
with abs_tol=1e-6 to absorb float-roundtrip noise."
```

---

## Task 8: PurchaseOrder upsert — all three paths

**Files:**
- Modify: `scripts/inventree_sync/order_import.py`
- Create: `scripts/tests/test_order_import_upsert.py`

This wires everything together: looks up or creates the PO, applies the diff, and triggers `receiveAll`. Status code semantics (per `inventree.purchase_order`):

- 10 = Pending (initial state after Create)
- 20 = Placed (after `.issue()`)
- 30 = Complete (after `.receiveAll()` of all line items)

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_order_import_upsert.py`:

```python
"""Mock-based tests for upsert_purchase_order (Pfad A/B/C)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import (  # noqa: E402
    SupplierOrder,
    SupplierOrderLine,
    upsert_purchase_order,
)


def _line(sku, qty, price):
    return SupplierOrderLine(
        sku=sku, qty=qty, unit_price=price, currency="EUR",
        mpn=sku, mfr_name="x", description="",
    )


def _supplier_part(pk, sku):
    sp = MagicMock(); sp.pk = pk; sp.SKU = sku
    return sp


def _po(status=10, lines=None):
    po = MagicMock()
    po.pk = 999
    po.status = status
    po.getLineItems.return_value = lines or []
    return po


def _make_order():
    return SupplierOrder(
        supplier_name="Mouser", reference="275708282",
        order_date="2025-07-07", currency="EUR",
        lines=[_line("A", 10, 1.0), _line("B", 5, 2.0)],
    )


def test_path_a_creates_po_and_lines_and_receives():
    """No existing PO → create, add 2 lines, issue, receive."""
    order = _make_order()
    supplier = MagicMock(); supplier.pk = 1; supplier.name = "Mouser"
    receive_loc = MagicMock(); receive_loc.pk = 7
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    new_po = _po(status=10)
    new_po.addLineItem.return_value = MagicMock()

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = []
        PO.create.return_value = new_po
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=supplier,
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=receive_loc,
        )

    PO.create.assert_called_once()
    create_kwargs = PO.create.call_args[0][1]
    assert create_kwargs["supplier"] == 1
    assert create_kwargs["reference"] == "275708282"

    assert new_po.addLineItem.call_count == 2
    new_po.issue.assert_called_once()
    new_po.receiveAll.assert_called_once_with(location=7, status=10)
    assert report.action == "CREATED"
    assert report.lines_added == 2


def test_path_b_adds_missing_line():
    """PO exists PLACED with 1 line; file has 2 → add the missing one."""
    order = _make_order()
    supplier = MagicMock(); supplier.pk = 1; supplier.name = "Mouser"
    receive_loc = MagicMock(); receive_loc.pk = 7
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")

    existing_li = MagicMock()
    existing_li.pk = 500
    existing_li.reference = "A"
    existing_li.quantity = 10
    existing_li.purchase_price = 1.0
    existing_li.part = 101

    existing_po = _po(status=20, lines=[existing_li])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=supplier,
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=receive_loc,
        )

    existing_po.addLineItem.assert_called_once()
    add_kwargs = existing_po.addLineItem.call_args.kwargs
    assert add_kwargs["part"] == 102  # supplier_part pk for B
    assert add_kwargs["quantity"] == 5
    assert add_kwargs["reference"] == "B"
    existing_po.receiveAll.assert_called_once()
    assert report.action == "RECONCILED"
    assert report.lines_added == 1


def test_path_b_updates_qty_change():
    """PO exists PLACED, file has different qty → save() called."""
    order = SupplierOrder(
        supplier_name="Mouser", reference="X", order_date=None,
        currency="EUR", lines=[_line("A", 15, 1.0)],
    )
    sp_a = _supplier_part(pk=101, sku="A")
    existing_li = MagicMock()
    existing_li.pk = 500; existing_li.reference = "A"
    existing_li.quantity = 10; existing_li.purchase_price = 1.0
    existing_li.part = 101

    existing_po = _po(status=20, lines=[existing_li])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a},
            receive_location=MagicMock(pk=7),
        )

    existing_li.save.assert_called_once()
    save_data = existing_li.save.call_args[0][0]
    assert save_data["quantity"] == 15


def test_path_b_deletes_extra_line():
    """PO has line C that file no longer has → delete it."""
    order = SupplierOrder(
        supplier_name="Mouser", reference="X", order_date=None,
        currency="EUR", lines=[_line("A", 10, 1.0)],
    )
    sp_a = _supplier_part(pk=101, sku="A")
    li_a = MagicMock()
    li_a.pk = 1; li_a.reference = "A"; li_a.quantity = 10
    li_a.purchase_price = 1.0; li_a.part = 101
    li_c = MagicMock()
    li_c.pk = 2; li_c.reference = "C"; li_c.quantity = 3
    li_c.purchase_price = 0.5; li_c.part = 103

    existing_po = _po(status=20, lines=[li_a, li_c])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a},
            receive_location=MagicMock(pk=7),
        )

    li_c.delete.assert_called_once()
    li_a.delete.assert_not_called()


def test_path_b_no_op_when_in_sync():
    """PENDING/PLACED PO with identical lines → no mutation, just receive."""
    order = _make_order()
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    li_a = MagicMock(); li_a.pk = 1; li_a.reference = "A"
    li_a.quantity = 10; li_a.purchase_price = 1.0; li_a.part = 101
    li_b = MagicMock(); li_b.pk = 2; li_b.reference = "B"
    li_b.quantity = 5; li_b.purchase_price = 2.0; li_b.part = 102

    existing_po = _po(status=10, lines=[li_a, li_b])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
        )

    existing_po.addLineItem.assert_not_called()
    li_a.save.assert_not_called(); li_b.save.assert_not_called()
    li_a.delete.assert_not_called(); li_b.delete.assert_not_called()
    existing_po.receiveAll.assert_called_once()


def test_path_c_in_sync_logs_and_exits_clean():
    """PO COMPLETE matching file → no-op, no exception."""
    order = _make_order()
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    li_a = MagicMock(); li_a.pk = 1; li_a.reference = "A"
    li_a.quantity = 10; li_a.purchase_price = 1.0; li_a.part = 101
    li_b = MagicMock(); li_b.pk = 2; li_b.reference = "B"
    li_b.quantity = 5; li_b.purchase_price = 2.0; li_b.part = 102

    existing_po = _po(status=30, lines=[li_a, li_b])

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
        )

    existing_po.addLineItem.assert_not_called()
    existing_po.receiveAll.assert_not_called()
    assert report.action == "IN_SYNC"


def test_path_c_drift_raises_runtime_error():
    """PO COMPLETE diverging from file → RuntimeError, no writes."""
    order = _make_order()  # file has A qty=10, B qty=5
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")
    li_a = MagicMock(); li_a.pk = 1; li_a.reference = "A"
    li_a.quantity = 7  # qty drift
    li_a.purchase_price = 1.0; li_a.part = 101

    existing_po = _po(status=30, lines=[li_a])  # missing B too

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = [existing_po]
        with pytest.raises(RuntimeError) as exc:
            upsert_purchase_order(
                api=MagicMock(),
                order=order,
                supplier=MagicMock(pk=1, name="Mouser"),
                sku_to_supplier_part={"A": sp_a, "B": sp_b},
                receive_location=MagicMock(pk=7),
            )

    msg = str(exc.value)
    assert "275708282" in msg
    assert "ADD" in msg or "UPDATE" in msg
    existing_po.addLineItem.assert_not_called()
    li_a.save.assert_not_called()


def test_dry_run_paths_no_writes():
    """dry_run=True must skip every InvenTree mutation."""
    order = _make_order()
    sp_a = _supplier_part(pk=101, sku="A")
    sp_b = _supplier_part(pk=102, sku="B")

    with patch("inventree_sync.order_import.PurchaseOrder") as PO:
        PO.list.return_value = []
        report = upsert_purchase_order(
            api=MagicMock(),
            order=order,
            supplier=MagicMock(pk=1),
            sku_to_supplier_part={"A": sp_a, "B": sp_b},
            receive_location=MagicMock(pk=7),
            dry_run=True,
        )

    PO.create.assert_not_called()
    assert report.action == "DRY_RUN_CREATE"
    assert report.lines_added == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v`
Expected: 8 fails with ImportError on `upsert_purchase_order`.

- [ ] **Step 3: Implement the upsert function**

Append to `scripts/inventree_sync/order_import.py`:

```python
from inventree.purchase_order import PurchaseOrder

_STATUS_PENDING = 10
_STATUS_PLACED = 20
_STATUS_COMPLETE = 30


@dataclass
class UpsertReport:
    """Result of upsert_purchase_order — used by the CLI for summary print."""
    action: str               # CREATED | RECONCILED | IN_SYNC | DRY_RUN_*
    po_reference: str
    lines_added: int = 0
    lines_updated: int = 0
    lines_deleted: int = 0


def _find_po(api: InvenTreeAPI, supplier_pk: int, reference: str):
    matches = PurchaseOrder.list(api, supplier=supplier_pk, reference=reference)
    for po in matches:
        # Post-filter — server may ignore the reference= filter (same defensive
        # pattern as find_part_by_name in client.py).
        if str(getattr(po, "reference", "")) == reference:
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

    existing = _find_po(api, supplier.pk, order.reference)

    if existing is None:
        # Pfad A
        if dry_run:
            return UpsertReport(
                action="DRY_RUN_CREATE", po_reference=order.reference,
                lines_added=len(order.lines),
            )
        po = PurchaseOrder.create(api, {
            "supplier": supplier.pk,
            "reference": order.reference,
            "description": f"Imported from {order.supplier_name} order {order.reference}",
            **({"target_date": order.order_date} if order.order_date else {}),
        })
        for line in order.lines:
            sp = sku_to_supplier_part[line.sku]
            po.addLineItem(
                part=sp.pk,
                quantity=line.qty,
                purchase_price=line.unit_price,
                purchase_price_currency=line.currency,
                reference=line.sku,
            )
        po.issue()
        po.receiveAll(location=receive_location.pk, status=10)
        return UpsertReport(
            action="CREATED", po_reference=order.reference,
            lines_added=len(order.lines),
        )

    status = int(getattr(existing, "status", 0))
    existing_lines = list(existing.getLineItems())
    diff = compute_po_line_diff(order.lines, existing_lines, sku_to_sp_pk)

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
    existing.receiveAll(location=receive_location.pk, status=10)

    return UpsertReport(
        action="RECONCILED", po_reference=order.reference,
        lines_added=len(diff.to_add),
        lines_updated=len(diff.to_update),
        lines_deleted=len(diff.to_delete),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_upsert.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `source .venv/bin/activate && pytest scripts/tests/ -v`
Expected: all pre-existing tests still pass + the new tests from Tasks 2-8.

- [ ] **Step 6: Commit**

```
git add scripts/inventree_sync/order_import.py scripts/tests/test_order_import_upsert.py
git commit -m "feat(import-orders): upsert_purchase_order — Pfad A/B/C

Pfad A creates a new PO + all lines + receiveAll.
Pfad B reconciles a PENDING/PLACED PO via compute_po_line_diff then receives.
Pfad C either no-ops (in-sync) or fail-louds with a drift report; never
writes on a COMPLETE PO to protect existing StockItems from orphaning.

Dry-run short-circuits before any mutation but still produces a sized
UpsertReport for the CLI summary."
```

---

## Task 9: CLI entrypoint — orchestrate everything

**Files:**
- Modify: `scripts/import_supplier_order.py`
- Create: `scripts/tests/test_order_import_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_order_import_cli.py`:

```python
"""Smoke tests for the CLI argparse + main() wiring (no real InvenTree)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import import_supplier_order as cli  # noqa: E402


def test_parse_args_requires_at_least_one_input():
    with pytest.raises(SystemExit):
        cli.parse_args([])  # neither flag


def test_parse_args_accepts_lcsc_only():
    args = cli.parse_args(["--lcsc-csv", "/tmp/x.csv"])
    assert args.lcsc_csv == "/tmp/x.csv"
    assert args.mouser_xls is None


def test_parse_args_dry_run_default_off():
    args = cli.parse_args(["--mouser-xls", "/tmp/x.xls"])
    assert args.dry_run is False


def test_parse_args_location_default():
    args = cli.parse_args(["--mouser-xls", "/tmp/x.xls"])
    assert args.location == "Lager"


def test_main_imports_lcsc_when_flag_given(tmp_path, monkeypatch):
    """main() should call upsert_purchase_order once for the LCSC file."""
    csv_file = tmp_path / "LCSC__WM_1.csv"
    csv_file.write_text(
        "LCSC Part Number,Manufacture Part Number,Manufacturer,Customer NO.,"
        "Package,Description,RoHS,Quantity,Unit Price($),Ext.Price($),"
        "Estimated lead time (business days),Updated lead time,"
        "Date Code / Lot No.\n"
        "C1,MPN1,M1,,0805,desc,YES,5,0.1,0.5,,,\n"
    )

    monkeypatch.setenv("INVENTREE_API_HOST", "http://localhost")
    monkeypatch.setenv("INVENTREE_API_TOKEN", "deadbeef")
    monkeypatch.setenv("MOUSER_API_KEY", "x")

    with patch("import_supplier_order.InvenTreeAPI") as API, \
         patch("import_supplier_order.LCSCFetcher"), \
         patch("import_supplier_order.MouserFetcher"), \
         patch("import_supplier_order.get_or_create_supplier") as gos, \
         patch("import_supplier_order.get_receive_location") as grl, \
         patch("import_supplier_order.ensure_part_for_order_line") as epfol, \
         patch("import_supplier_order.upsert_purchase_order") as upsert, \
         patch("import_supplier_order.load_category_map", return_value={}):
        API.return_value = MagicMock()
        gos.return_value = MagicMock(pk=1, name="LCSC")
        grl.return_value = MagicMock(pk=7)
        epfol.return_value = (MagicMock(pk=100), MagicMock(pk=200, SKU="C1"))
        upsert.return_value = MagicMock(action="CREATED", po_reference="WM",
                                        lines_added=1, lines_updated=0,
                                        lines_deleted=0)
        rc = cli.main(["--lcsc-csv", str(csv_file)])
    assert rc == 0
    upsert.assert_called_once()


def test_main_returns_nonzero_on_drift(tmp_path, monkeypatch):
    csv_file = tmp_path / "LCSC__WM_1.csv"
    csv_file.write_text(
        "LCSC Part Number,Manufacture Part Number,Manufacturer,Customer NO.,"
        "Package,Description,RoHS,Quantity,Unit Price($),Ext.Price($),"
        "Estimated lead time (business days),Updated lead time,"
        "Date Code / Lot No.\n"
        "C1,MPN1,M1,,0805,desc,YES,5,0.1,0.5,,,\n"
    )
    monkeypatch.setenv("INVENTREE_API_HOST", "http://localhost")
    monkeypatch.setenv("INVENTREE_API_TOKEN", "deadbeef")

    with patch("import_supplier_order.InvenTreeAPI"), \
         patch("import_supplier_order.LCSCFetcher"), \
         patch("import_supplier_order.MouserFetcher"), \
         patch("import_supplier_order.get_or_create_supplier") as gos, \
         patch("import_supplier_order.get_receive_location") as grl, \
         patch("import_supplier_order.ensure_part_for_order_line") as epfol, \
         patch("import_supplier_order.upsert_purchase_order") as upsert, \
         patch("import_supplier_order.load_category_map", return_value={}):
        gos.return_value = MagicMock(pk=1)
        grl.return_value = MagicMock(pk=7)
        epfol.return_value = (MagicMock(pk=100), MagicMock(pk=200, SKU="C1"))
        upsert.side_effect = RuntimeError("drift!")
        rc = cli.main(["--lcsc-csv", str(csv_file)])
    assert rc == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_cli.py -v`
Expected: all fail with `AttributeError: module 'import_supplier_order' has no attribute 'parse_args'`.

- [ ] **Step 3: Implement the CLI**

Replace `scripts/import_supplier_order.py` (which currently has only the docstring) with:

```python
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
    """
    class _F(logging.Filter):
        def filter(self, record):
            return "not found in category map" not in record.getMessage()
    logging.getLogger("inventree_sync.categories").addFilter(_F())


def _import_one_order(
    api: InvenTreeAPI,
    order: SupplierOrder,
    lcsc_fetcher: LCSCFetcher,
    mouser_fetcher: MouserFetcher,
    lcsc_supplier,
    mouser_supplier,
    category_map: dict,
    receive_location,
    dry_run: bool,
) -> int:
    """Process one parsed SupplierOrder. Returns exit-code (0 ok, 1 drift)."""
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
            )
        except Exception as exc:
            log.error("Failed to resolve %s line %s: %s",
                      supplier_kind, line.sku, exc)
            return 1
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

    api = InvenTreeAPI()
    lcsc_fetcher = LCSCFetcher()
    mouser_fetcher = MouserFetcher() if args.mouser_xls else None
    lcsc_supplier = get_or_create_supplier(api, name="LCSC")
    mouser_supplier = get_or_create_supplier(api, name="Mouser")
    category_map = load_category_map(args.categories)
    receive_location = get_receive_location(api, args.location)

    rc = 0
    if args.lcsc_csv:
        order = parse_lcsc_csv(Path(args.lcsc_csv))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
        )
    if args.mouser_xls:
        order = parse_mouser_xls(Path(args.mouser_xls))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_order_import_cli.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run --help to verify argparse output**

Run: `source .venv/bin/activate && python3 scripts/import_supplier_order.py --help`
Expected: usage text listing `--mouser-xls`, `--lcsc-csv`, `--location`, `--categories`, `--dry-run`.

- [ ] **Step 6: Commit**

```
git add scripts/import_supplier_order.py scripts/tests/test_order_import_cli.py
git commit -m "feat(import-orders): CLI entrypoint wiring all pieces together

argparse with --mouser-xls / --lcsc-csv (at least one required), --location
(default 'Lager'), --dry-run. main() resolves parts via
ensure_part_for_order_line then dispatches to upsert_purchase_order;
RuntimeError from Pfad C drift surfaces as exit-code 1.

Category warning filter demotes 'KiCad symbol \"\" not in map' from
WARNING to DEBUG — expected for supplier imports."
```

---

## Task 10: README update + manual smoke test

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a section to README.md**

Open `README.md`. Locate the section after `## Layout of this repo` and BEFORE `## Local development`. Insert a new section:

```markdown
## Importing historical supplier orders

`scripts/import_supplier_order.py` migrates Mouser-XLS or LCSC-CSV order
files into InvenTree as PurchaseOrders with received StockItems. Reuses
`inventree_sync`'s part-dedup/create primitives.

```bash
export INVENTREE_API_HOST=https://inventree.example.org
export INVENTREE_API_TOKEN=...
export MOUSER_API_KEY=...

# Dry-run: print what would happen, no writes
python3 scripts/import_supplier_order.py \
    --mouser-xls ~/orders/275708282.xls \
    --lcsc-csv ~/orders/LCSC__WM2504270070_20260610043835.csv \
    --dry-run

# Real run
python3 scripts/import_supplier_order.py \
    --mouser-xls ~/orders/275708282.xls \
    --lcsc-csv ~/orders/LCSC__WM2504270070_20260610043835.csv
```

The file (XLS/CSV) is **source of truth** on re-runs:

| State of PO in InvenTree | Behaviour |
|---|---|
| Doesn't exist | Create + add line items + issue + receive |
| `PENDING` / `PLACED` | Reconcile line items to the file, then receive |
| `COMPLETE`, in sync with file | No-op, exit 0 |
| `COMPLETE`, diverges from file | Loud-fail with a drift report, exit 1 — resolve manually in the InvenTree UI |

Default receive-into location is named `Lager`; override with `--location <name>`. The script falls back to the first top-level StockLocation if the requested one isn't found.
```

- [ ] **Step 2: Commit**

```
git add README.md
git commit -m "docs: usage section for the new supplier-order importer"
```

- [ ] **Step 3: Manual smoke test (dry-run against a real InvenTree)**

This step is **not** automated — it requires a live InvenTree. Run when ready:

```bash
source .venv/bin/activate
export INVENTREE_API_HOST=<...>  INVENTREE_API_TOKEN=<...>  MOUSER_API_KEY=<...>
python3 scripts/import_supplier_order.py \
    --mouser-xls /home/pbuchegger/OE5XRX/inventree_import/275708282.xls \
    --lcsc-csv  /home/pbuchegger/OE5XRX/inventree_import/LCSC__WM2504270070_20260610043835.csv \
    --dry-run
```

Expected: prints `DRY_RUN_CREATE PO 275708282 — added=27 …` and `DRY_RUN_CREATE PO WM2504270070 — added=28 …` (or `…_RECONCILE` if either PO already exists). No InvenTree mutations.

Then drop `--dry-run` for the real run. Verify in the InvenTree UI:
- Both POs exist with status `COMPLETE` (or appropriate code).
- All 27 + 28 line items present.
- StockItems for each line at the chosen location.

Re-run the command without changes — expect `IN_SYNC` action twice and exit 0.

To exercise drift handling: temporarily edit the LCSC CSV to change one row's `Quantity` and re-run — expect a `RuntimeError` printout with `UPDATE C<…> qty → <new>` and exit 1.

- [ ] **Step 4: No commit for the smoke test itself**

The smoke test is operator-side validation only. If something breaks, file an issue or open a follow-up commit.

---

## Self-Review (done by the plan author)

Spec coverage (matched section-by-section to spec):

- ✅ Motivation/Goals — Task 1 (deps), Tasks 2-9 implement the importer end-to-end.
- ✅ Eine PurchaseOrder pro Lieferant → Task 8 / 9 dispatch per supplier-file.
- ✅ Idempotente Part-Dedup über SKU → MPN+Mfr → Name → Task 6.
- ✅ Volle Part-Anlage mit Datasheet/Image/Parameters → reused via `create_part_in_inventree` (Task 6).
- ✅ POs direkt RECEIVED → Task 8 calls `po.receiveAll(...)`.
- ✅ Re-Run-Sicherheit (File = SoT) → Task 7 (diff) + Task 8 (apply per status).
- ✅ Dry-Run-Modus → Task 8 (dry_run argument) + Task 9 (`--dry-run`).
- ✅ Tests → Tasks 2-9 are TDD-style, each with explicit failing-test step.
- ✅ Logging-Filter für KiCad-empty Warning → Task 9 (`_suppress_category_warning`).
- ✅ Currency-Tag pro LineItem → Task 8 passes `purchase_price_currency=line.currency`.
- ✅ Receive-Location strategy (named → fallback → fail) → Task 5.
- ✅ Non-Goals respected (no currency conversion, no GitHub Action, no state file, no rollback of completed PO).

Placeholder scan: no TBD/TODO, no "implement later", no "handle edge cases" — every code block is concrete.

Type consistency: `SupplierOrderLine`, `SupplierOrder`, `POLineDiff`, `LineItemAction`, `UpsertReport` defined in Task 2/7/8 and referenced consistently afterwards. `ensure_part_for_order_line` returns `tuple` (Part, SupplierPart) — referenced as `(_, sp) = ...` in Task 9. `upsert_purchase_order(sku_to_supplier_part=...)` takes `dict[str, SupplierPart]` — produced from the (Part, SupplierPart) tuples in Task 9's `_import_one_order`.

One ambiguity I cleaned up inline: Task 6's `_lookup_supplier_part` returns the SupplierPart that the resolution step needs for the upsert — without it the upsert can't get `sp.pk` for `addLineItem(part=...)`.
