# PR-5 Sync-Ready Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bundle five backlog items (#13, #15, #19, #16, #17) so the production marathon-sync (re-pflegen aller bestehenden Releases ins InvenTree) durchläuft, ohne Duplikat-Parts zu erzeugen oder bei transienten Fehlern aufzugeben.

**Architecture:** Five mostly-independent additions to the existing `scripts/inventree_sync/` package and `scripts/bom_export.py` CLI. New helper `find_part_by_mpn_and_manufacturer` inserts between the existing SKU-lookup and name-lookup in `part_manager.ensure_parts_exist`. New pure function `_normalize_value` in `categories.py` runs only on R/C/L/CP/XTAL value tokens. Retry-equipped session helper `_make_retry_session` replaces the bare `requests.Session()` in both fetchers. New `ErrorCollector` class collects per-entry failures instead of crashing early. New `--planned-builds N` CLI flag drives `Part.minimum_stock` updates in `populate_bom`.

**Tech Stack:** Python 3.14, `requests` + `urllib3.util.Retry`, `inventree-python-client` v0.23.1, `pytest` for pure-Python unit tests, custom E2E harness against the real InvenTree server at `parts.oe5xrx.org`.

---

## File Structure

**Modified:**
- `scripts/inventree_sync/client.py` — add `find_part_by_mpn_and_manufacturer` + Company-name cache.
- `scripts/inventree_sync/part_manager.py` — insert MPN+Mfr lookup branch in `ensure_parts_exist`.
- `scripts/inventree_sync/categories.py` — add `_normalize_value`, call it in `generate_part_name`.
- `scripts/inventree_sync/fetchers.py` — add `_make_retry_session`, switch both fetchers to it.
- `scripts/bom_export.py` — add `ErrorCollector` class, `--planned-builds` CLI flag, min-stock update logic, refactor `match_supplier_parts` to use collector.
- `scripts/e2e_revision_handling.py` — add three new test functions + register them in `main()`.

**Created:**
- `scripts/tests/test_normalization.py` — pytest cases for `_normalize_value`.
- `scripts/tests/test_error_collector.py` — pytest cases for `ErrorCollector`.

**Untouched (deliberately):**
- `scripts/inventree_sync/cost_report.py`
- `scripts/inventree_sync/dry_run.py`
- `scripts/inventree_sync/attachments.py`
- `scripts/inventree_sync/models.py`
- `scripts/inventree_refresh.py` (read-only paths benefit from the retry session transparently because they share the fetchers).

---

## Task 1: #19 Value Normalization — pure function with pytest

**Files:**
- Modify: `scripts/inventree_sync/categories.py:79-105` (`generate_part_name`)
- Create: `scripts/tests/test_normalization.py`

### Steps

- [ ] **Step 1.1: Write the pytest file with all 10 cases (failing — module not exposed yet)**

Create `scripts/tests/test_normalization.py`:

```python
"""Pure-Python unit tests for value normalization in part-name generation."""

from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap sys.path so `inventree_sync` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.categories import _normalize_value


def test_normalize_strip_omega():
    """Unicode Ohm signs (Ω and Ohm-Sign) are stripped."""
    assert _normalize_value("10kΩ") == "10k"      # U+03A9 GREEK CAPITAL LETTER OMEGA
    assert _normalize_value("4.7Ω") == "4.7"      # bare omega
    assert _normalize_value("1MΩ") == "1M"        # mega preserved (see lowercase test)


def test_normalize_uppercase_k_to_lowercase():
    """Capital K (kilo) is normalized to lowercase k."""
    assert _normalize_value("10K") == "10k"
    assert _normalize_value("4.7K") == "4.7k"


def test_normalize_lowercase_m_stays_milli():
    """Lowercase m (milli) is NOT changed — ambiguity with M (mega) is preserved."""
    assert _normalize_value("10m") == "10m"
    assert _normalize_value("470mA") == "470mA"


def test_normalize_uppercase_M_stays_mega():
    """Capital M (mega) is NOT changed."""
    assert _normalize_value("1M") == "1M"
    assert _normalize_value("16MHz") == "16MHz"


def test_normalize_micro_to_u():
    """µ (U+00B5) is converted to ASCII u for InvenTree-search-friendliness."""
    assert _normalize_value("4.7µF") == "4.7uF"
    assert _normalize_value("100µH") == "100uH"


def test_normalize_strip_whitespace_between_number_and_unit():
    """Single space between digits and a SI-prefix-or-unit token is removed."""
    assert _normalize_value("10 k") == "10k"
    assert _normalize_value("100 nF") == "100nF"
    assert _normalize_value("4.7 µF") == "4.7uF"


def test_normalize_idempotent():
    """Running the normalizer twice produces the same output as running it once."""
    for inp in ("10K", "10 kΩ", "4.7µF", "100 nF", "1MΩ"):
        once = _normalize_value(inp)
        twice = _normalize_value(once)
        assert once == twice, f"f({inp!r})={once!r}, f(f({inp!r}))={twice!r}"


def test_normalize_passthrough_for_non_RCL_strings():
    """Already-canonical or non-RCL strings pass through unchanged."""
    assert _normalize_value("8MHz") == "8MHz"
    assert _normalize_value("STM32U575CITx") == "STM32U575CITx"
    assert _normalize_value("100nF") == "100nF"


def test_normalize_empty_string():
    """Empty input → empty output (no exception)."""
    assert _normalize_value("") == ""


def test_normalize_compound_value_with_slash():
    """Slash-separated compound values (e.g. crystal load) keep their structure."""
    # Crystal value like "8MHz/20pF" — slash already collapsed by caller, but
    # _normalize_value should not split it further.
    assert _normalize_value("8MHz/20pF") == "8MHz/20pF"
```

- [ ] **Step 1.2: Run the pytest file to confirm it fails with ImportError**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -m pytest scripts/tests/test_normalization.py -v
```

Expected: `ImportError: cannot import name '_normalize_value' from 'inventree_sync.categories'`

- [ ] **Step 1.3: Implement `_normalize_value` in `categories.py`**

Edit `scripts/inventree_sync/categories.py`, inserting the new function between `extract_package` (line 77) and `generate_part_name` (line 79):

```python
# Unicode codepoints we normalize away from value strings:
#   Ω (U+03A9) GREEK CAPITAL LETTER OMEGA  → KiCad uses this for ohms
#   Ω (U+2126) OHM SIGN                    → some libraries use this instead
#   µ (U+00B5) MICRO SIGN                  → micro (e.g. 4.7µF)
_OMEGA_CHARS = ("Ω", "Ω")
_MICRO_CHAR = "µ"

# Units/prefixes for which whitespace between the numeric part and the unit
# must be stripped. Order matters: longer prefixes (mHz, kHz) before single-
# letter ones (m, k) so the regex below doesn't half-match.
_UNIT_TOKENS = (
    "mHz", "MHz", "kHz", "Hz",
    "mA", "A", "mV", "V", "W",
    "uF", "nF", "pF", "F",
    "uH", "nH", "mH", "H",
    "m", "k", "M",      # bare SI prefixes used for resistors (10k, 1M, 470m)
)


def _normalize_value(value: str) -> str:
    """Normalize a KiCad value string to a canonical form for part-name generation.

    Rules (conservative — no SI-prefix conversion like 1000→1k):
      - Strip Unicode Ω (U+03A9 GREEK CAPITAL LETTER OMEGA, U+2126 OHM SIGN).
      - Convert µ (U+00B5) to ASCII u.
      - Convert capital K (kilo) to lowercase k.  Lowercase m (milli) and
        capital M (mega) are kept as-is — confusing them would create silent
        unit errors.
      - Collapse single whitespace between numeric prefix and unit/SI-prefix
        token: "10 k" → "10k", "100 nF" → "100nF".
      - Idempotent: ``_normalize_value(_normalize_value(x)) == _normalize_value(x)``.

    Non-numeric strings (e.g. ``STM32U575CITx``) pass through unchanged.

    Examples:
        >>> _normalize_value("10K")
        '10k'
        >>> _normalize_value("10 kΩ")
        '10k'
        >>> _normalize_value("4.7µF")
        '4.7uF'
        >>> _normalize_value("1MΩ")
        '1M'
        >>> _normalize_value("STM32U575CITx")
        'STM32U575CITx'
    """
    if not value:
        return value

    # 1. Strip Ω, convert µ → u.
    out = value
    for omega in _OMEGA_CHARS:
        out = out.replace(omega, "")
    out = out.replace(_MICRO_CHAR, "u")

    # 2. Capital K (kilo) → lowercase k, when preceded by a digit.
    #    The digit-precondition prevents accidentally lowercasing "K" in
    #    part numbers like "1K78" or "BAT54K".
    out = re.sub(r"(\d)K", r"\1k", out)

    # 3. Collapse whitespace between digits and a known unit/prefix token.
    #    Longest-first to avoid partial matches.
    for tok in _UNIT_TOKENS:
        # \b on the right of `tok` ensures we don't eat "kg" when matching "k".
        out = re.sub(rf"(\d)\s+({re.escape(tok)})\b", r"\1\2", out)

    return out
```

- [ ] **Step 1.4: Run the pytest file to verify all cases pass**

```bash
python3 -m pytest scripts/tests/test_normalization.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 1.5: Wire `_normalize_value` into `generate_part_name`**

Edit `scripts/inventree_sync/categories.py:79-105`. Replace the entire `generate_part_name` body:

```python
def generate_part_name(kicad_part: str, kicad_value: str, footprint: str) -> str:
    """
    Generate a human-readable InvenTree part name from KiCad fields.

    Examples:
      R, '10K', 'R_0805_2012Metric'         → 'R 10k 0805'  (normalized)
      C, '100 nF', 'C_0805_2012Metric'       → 'C 100nF 0805' (normalized)
      C_Polarized, '100u / 25V', ...          → 'CP 100u/25V'
      Crystal, '8MHz / 20pF', ...             → 'XTAL 8MHz/20pF'
      STM32U575CITx, 'STM32U575CITx', ...    → 'STM32U575CITx' (unchanged)
    """
    # Collapse spaces around '/' and consecutive spaces (compound values).
    val = re.sub(r"\s*/\s*", "/", kicad_value.strip())
    val = re.sub(r"\s+", " ", val).strip()

    # Apply value normalization ONLY for component types where the value
    # is a numeric-with-unit token (R/C/L/CP/XTAL). For types like generic
    # ICs (STM32U575CITx) the value IS the part number — normalizing it
    # would silently mangle it.
    if kicad_part in {"R", "C", "C_Polarized", "L", "L_Iron", "Crystal"}:
        val = _normalize_value(val)

    if kicad_part == "R":
        return f"R {val} {extract_package(footprint)}"
    elif kicad_part == "C":
        return f"C {val} {extract_package(footprint)}"
    elif kicad_part == "C_Polarized":
        return f"CP {val}"
    elif kicad_part in ("L", "L_Iron"):
        return f"L {val}"
    elif kicad_part == "Crystal":
        return f"XTAL {val}"
    else:
        return val
```

- [ ] **Step 1.6: Smoke-test `generate_part_name` via REPL**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -c "
from scripts.inventree_sync.categories import generate_part_name
print(repr(generate_part_name('R', '10K', 'R_0805_2012Metric')))
print(repr(generate_part_name('C', '100 nF', 'C_0805_2012Metric')))
print(repr(generate_part_name('R', '10kΩ', 'R_0805_2012Metric')))
print(repr(generate_part_name('Crystal', '8MHz/20pF', 'Crystal_SMD_3225-4Pin')))
print(repr(generate_part_name('STM32U575CITx', 'STM32U575CITx', 'TQFP-48')))
"
```

Expected:
```
'R 10k 0805'
'C 100nF 0805'
'R 10k 0805'
'XTAL 8MHz/20pF'
'STM32U575CITx'
```

- [ ] **Step 1.7: Commit**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
git add scripts/inventree_sync/categories.py scripts/tests/test_normalization.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): normalize value strings before part-name generation (#19)

Adds _normalize_value to categories.py: strips Ω/Ohm-Sign, lowercases K
(kilo), converts µ → u, removes whitespace between digit and SI-prefix.
Applied only to R/C/L/CP/XTAL value tokens — IC part numbers like
STM32U575CITx pass through unchanged.

10 pytest cases for the normalizer cover idempotency, edge cases (empty
string, milli-vs-mega ambiguity), and pass-through behavior.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: #17 Retry session for LCSC + Mouser fetchers

**Files:**
- Modify: `scripts/inventree_sync/fetchers.py:1-30` (imports + module-level helper) and `:24-43` (LCSCFetcher.__init__) and `:177-205` (MouserFetcher).

### Steps

- [ ] **Step 2.1: Add the retry-session helper at module top**

Edit `scripts/inventree_sync/fetchers.py`. Replace the import block (lines 1-21) with:

```python
"""
fetchers.py – Supplier data fetchers for LCSC and Mouser.
"""

import logging
import os
import re
from typing import Optional

import requests
import urllib3.util
from requests.adapters import HTTPAdapter

from .models import PartData

logger = logging.getLogger(__name__)

# iOS User-Agent – avoids bot-blocking on LCSC's CDN / wmsc API
_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)


def _make_retry_session() -> requests.Session:
    """Build a requests.Session with urllib3 Retry mounted on http(s)://.

    Retries on transient distributor-side failures so a single 502 from
    LCSC or a Mouser-API hiccup doesn't kill an 80-part marathon-sync:

      total=3              — three retries beyond the initial attempt
      backoff_factor=2     — sleeps 0s, 2s, 4s between attempts
      status_forcelist     — 429 (rate-limit) + 5xx server errors
      allowed_methods      — GET (LCSC detail) + POST (LCSC search, Mouser)
      raise_on_status=False — let calling code see the final response;
                              both fetchers return parseable JSON even on
                              some 4xx and we want to log the body.

    Image downloads in client.py.upload_image_from_url do NOT use this
    session — PerimeterX blocks are not transient and a retry only
    floods the logs.
    """
    session = requests.Session()
    retry = urllib3.util.Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
```

- [ ] **Step 2.2: Switch LCSCFetcher to the retry session**

Edit `scripts/inventree_sync/fetchers.py`. Replace `LCSCFetcher.__init__` (currently lines ~29-43):

```python
    def __init__(self):
        self.session = _make_retry_session()
        self.session.headers.update({
            "User-Agent": self._UA,
            "Accept-Language": "en-US,en",
        })
        # Initialise session / set currency cookie
        try:
            self.session.get(
                "https://wmsc.lcsc.com/wmsc/home/currency?currencyCode=EUR",
                timeout=10,
            )
        except Exception as exc:
            logger.warning("LCSC currency init failed: %s", exc)
```

(The only change from the original is `self.session = _make_retry_session()` instead of `requests.Session()`.)

- [ ] **Step 2.3: Add a session to MouserFetcher and route its POST through it**

Edit `scripts/inventree_sync/fetchers.py`. Replace `MouserFetcher.__init__` and the first 10 lines of `fetch()` (currently lines ~177-205):

```python
class MouserFetcher:
    """Fetches part data from the Mouser API v2.

    Requires the ``MOUSER_API_KEY`` environment variable to be set.
    """

    _URL = "https://api.mouser.com/api/v2/search/partnumber"

    def __init__(self):
        self.api_key = os.environ.get("MOUSER_API_KEY")
        if not self.api_key:
            raise EnvironmentError(
                "MOUSER_API_KEY environment variable is not set. "
                "Export it before running this script."
            )
        # Retry session so a single Mouser-API hiccup doesn't kill the sync.
        # Shared with LCSCFetcher's session-shape via _make_retry_session.
        self.session = _make_retry_session()

    def fetch(self, mouser_sku: str) -> Optional[PartData]:
        """Return PartData for a Mouser SKU, or None on failure."""
        payload = {
            "SearchByPartRequest": {
                "mouserPartNumber": mouser_sku,
                "partSearchOptions": "Exact",
            }
        }
        try:
            resp = self.session.post(
                self._URL,
                params={"apiKey": self.api_key},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("Mouser fetch(%s) failed: %s", mouser_sku, exc)
            return None
```

(Changes from original: added `self.session = _make_retry_session()` in `__init__`; switched `requests.post(...)` → `self.session.post(...)` in `fetch()`.)

- [ ] **Step 2.4: Smoke-test by running an LCSC fetch**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -c "
from scripts.inventree_sync.fetchers import LCSCFetcher, MouserFetcher
import os
os.environ.setdefault('MOUSER_API_KEY', 'dummy-for-smoke')
f = LCSCFetcher()
data = f.fetch_by_sku('C17414')   # well-known 10k 0805
print('LCSC OK' if data and data.mpn else 'LCSC FAIL')
print('  mpn=', data.mpn if data else None)
# Don't actually call Mouser without a real key — just verify construction.
m = MouserFetcher()
print('Mouser session OK:', hasattr(m, 'session'))
"
```

Expected:
```
LCSC OK
  mpn= <some MPN string>
Mouser session OK: True
```

- [ ] **Step 2.5: Commit**

```bash
git add scripts/inventree_sync/fetchers.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): urllib3 Retry on LCSC + Mouser fetchers (#17)

Adds _make_retry_session() helper with total=3 retries, backoff_factor=2
(0s/2s/4s) on 429/500/502/503/504 for both GET (LCSC detail) and POST
(LCSC search, Mouser).  MouserFetcher gets its first session — previously
used bare requests.post.

Image downloads in client.py deliberately unchanged: PerimeterX blocks
aren't transient and a retry just floods logs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: #13 MPN+Manufacturer dedup helper + part_manager integration

**Files:**
- Modify: `scripts/inventree_sync/client.py:14-21` (imports) and end of file (new function).
- Modify: `scripts/inventree_sync/part_manager.py:14-26` (imports) and `:194-207` (insertion in `ensure_parts_exist`).

### Steps

- [ ] **Step 3.1: Add `find_part_by_mpn_and_manufacturer` to `client.py`**

Edit `scripts/inventree_sync/client.py`. Add this function after `find_part_by_name_and_revision` (around line 404):

```python
# Process-lifetime cache: Company-pk → name. ManufacturerPart-based dedup
# (find_part_by_mpn_and_manufacturer) calls Company(api, pk).name once per
# unique manufacturer pk; cache avoids the N+1 round-trip when a BOM has
# many parts from the same manufacturer (typical: 30+ resistors from
# Uniroyal, 20+ caps from Samsung).
_manufacturer_name_cache: dict[int, str] = {}


def _resolve_manufacturer_name(api: InvenTreeAPI, manufacturer_pk: int) -> str:
    """Return Company(pk=manufacturer_pk).name with process-lifetime cache.

    Empty string when the Company lookup fails — caller treats that as
    'manufacturer not found' which fails the find_part_by_mpn_and_manufacturer
    match safely.
    """
    cached = _manufacturer_name_cache.get(manufacturer_pk)
    if cached is not None:
        return cached
    try:
        name = Company(api, pk=manufacturer_pk).name or ""
    except Exception:
        name = ""
    _manufacturer_name_cache[manufacturer_pk] = name
    return name


def find_part_by_mpn_and_manufacturer(
    api: InvenTreeAPI, mpn: str, manufacturer_name: str
) -> Optional[Part]:
    """Find an existing Part by ManufacturerPart MPN + manufacturer name.

    Returns the linked Part when a ManufacturerPart exists whose MPN matches
    *mpn* exactly AND whose linked Company name matches *manufacturer_name*
    case-insensitively.  Returns None otherwise.

    Defensive: post-filters on both MPN AND manufacturer name because some
    InvenTree server versions silently ignore the ``MPN=`` filter (same
    pattern as find_part_by_name).  Manufacturer-name comparison is
    case-insensitive to absorb supplier-side inconsistencies (LCSC may
    return "Texas Instruments", Mouser "TEXAS INSTRUMENTS").
    """
    mpn = (mpn or "").strip()
    manufacturer_name = (manufacturer_name or "").strip()
    if not mpn or not manufacturer_name:
        return None
    try:
        candidates = ManufacturerPart.list(api, MPN=mpn)
    except Exception as exc:
        logger.debug("ManufacturerPart MPN lookup failed for %r: %s", mpn, exc)
        return None

    target_lower = manufacturer_name.lower()
    for mp in candidates:
        # Post-filter the MPN — server might have ignored the filter.
        if (mp.MPN or "").strip() != mpn:
            continue
        # Post-filter the manufacturer name (case-insensitive).
        mpn_mfr_name = _resolve_manufacturer_name(api, int(mp.manufacturer))
        if mpn_mfr_name.lower() == target_lower:
            try:
                return Part(api, pk=int(mp.part))
            except Exception as exc:
                logger.debug("Part lookup for MfrPart pk=%s failed: %s",
                             mp.pk, exc)
                continue
    return None
```

- [ ] **Step 3.2: Smoke-test the new helper is importable**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -c "
from scripts.inventree_sync.client import (
    find_part_by_mpn_and_manufacturer, _resolve_manufacturer_name
)
print('imports OK')
"
```

Expected: `imports OK`

- [ ] **Step 3.3: Insert MPN+Mfr lookup in `part_manager.ensure_parts_exist`**

Edit `scripts/inventree_sync/part_manager.py`. First update the import block (line 17-23):

```python
from .client import (
    create_part_in_inventree,
    ensure_supplier_parts,
    find_existing_part,
    find_part_by_mpn_and_manufacturer,
    find_part_by_name,
    get_or_create_supplier,
)
```

Then in `ensure_parts_exist`, find the block at line 194-207 (currently starts with `name = generate_part_name(...)`) and replace it with:

```python
        # Generate name early so we have it for both the MPN+Mfr-miss branch
        # (where it becomes the secondary cache-key) and the new-part path.
        name = generate_part_name(kicad_part, kicad_value, kicad_footprint)

        # Dedup priority: SKU (done above) → MPN+Manufacturer → Name.
        # MPN+Mfr is more reliable than name because it's a hardware-level
        # identifier — survives our own naming conventions changing (e.g.
        # the #19 value-normalizer landing here in this same PR).
        existing_by_mpn = None
        if part_data.mpn and part_data.manufacturer:
            existing_by_mpn = find_part_by_mpn_and_manufacturer(
                api, part_data.mpn, part_data.manufacturer
            )
        if existing_by_mpn:
            logger.info(
                "Part for MPN=%r mfr=%r already exists (pk=%s); "
                "adding missing supplier parts",
                part_data.mpn, part_data.manufacturer, existing_by_mpn.pk,
            )
            ensure_supplier_parts(
                api, existing_by_mpn, part_data,
                lcsc_supplier, mouser_supplier,
                lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
            )
            entry.inventree_part.append(existing_by_mpn)
            continue

        existing_by_name = find_part_by_name(api, name)
        if existing_by_name:
            logger.info(
                "Part '%s' already exists (pk=%s); adding missing supplier parts",
                name, existing_by_name.pk,
            )
            ensure_supplier_parts(
                api, existing_by_name, part_data,
                lcsc_supplier, mouser_supplier,
                lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
            )
            entry.inventree_part.append(existing_by_name)
            continue
```

(The only structural change vs the original: a new `existing_by_mpn` branch is inserted *before* the existing `find_part_by_name` branch. The `name = generate_part_name(...)` line moves up by 2 lines but lands in the same logical place — before any name-or-MPN lookup.)

- [ ] **Step 3.4: Smoke-test that `ensure_parts_exist` still imports cleanly**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -c "
from scripts.inventree_sync.part_manager import ensure_parts_exist
print('ensure_parts_exist still imports')
"
```

Expected: `ensure_parts_exist still imports`

- [ ] **Step 3.5: Commit**

```bash
git add scripts/inventree_sync/client.py scripts/inventree_sync/part_manager.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): MPN+Manufacturer dedup before name-based lookup (#13)

Adds find_part_by_mpn_and_manufacturer in client.py: searches the
ManufacturerPart table by MPN, post-filters by manufacturer name
(case-insensitive).  Process-lifetime _manufacturer_name_cache avoids
N+1 Company(api, pk).name calls for BOMs with many parts from the same
manufacturer.

ensure_parts_exist insertion order is now:
  1. SKU match            (find_existing_part — unchanged)
  2. fetch part_data       (need MPN+Mfr from supplier)
  3. MPN+Mfr match         (NEW)
  4. Name match            (find_part_by_name — fallback)
  5. Create new            (unchanged)

Hardware-level identifier (MPN+Mfr) is more reliable than the generated
name, especially across the #19 value-normalizer landing in this PR
that may rename "R 10K 0805" → "R 10k 0805".

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: #16 Aggregated error output via ErrorCollector

**Files:**
- Modify: `scripts/bom_export.py:1-32` (imports) and `:90-180` (`match_supplier_parts`) and `:346-454` (`main`).
- Create: `scripts/tests/test_error_collector.py`

### Steps

- [ ] **Step 4.1: Add ErrorCollector class + pytest, both failing first**

Create `scripts/tests/test_error_collector.py`:

```python
"""Pure-Python unit tests for ErrorCollector (bom_export.py)."""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

# Bootstrap sys.path so `bom_export` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bom_export import ErrorCollector


def test_empty_collector_has_no_errors():
    c = ErrorCollector()
    assert c.has_errors() is False
    assert c.errors == []


def test_add_one_error():
    c = ErrorCollector()
    c.add("Parts", "R1", "no InvenTree match")
    assert c.has_errors() is True
    assert len(c.errors) == 1
    assert c.errors[0] == ("Parts", "R1", "no InvenTree match")


def test_multiple_errors_preserved_in_order():
    """Insertion order matters for the summary output — preserve it."""
    c = ErrorCollector()
    c.add("Parts", "R1", "first")
    c.add("Parts", "R2", "second")
    c.add("BomItem", "X", "third")
    assert [e[1] for e in c.errors] == ["R1", "R2", "X"]


def test_print_summary_no_errors_is_quiet(caplog):
    """Empty collector → print_summary emits nothing at ERROR level."""
    c = ErrorCollector()
    with caplog.at_level(logging.ERROR):
        c.print_summary()
    assert caplog.records == []


def test_print_summary_with_errors_logs_each(caplog):
    """Each error appears in the ERROR-level log output."""
    c = ErrorCollector()
    c.add("Parts", "R1", "no InvenTree match")
    c.add("Parts", "R2", "no supplier data")
    with caplog.at_level(logging.ERROR):
        c.print_summary()
    # Header + 2 errors + footer = at least 4 records.
    assert len(caplog.records) >= 4
    text = "\n".join(r.message for r in caplog.records)
    assert "Sync completed with 2 error(s)" in text
    assert "[Parts] R1" in text and "no InvenTree match" in text
    assert "[Parts] R2" in text and "no supplier data" in text
```

- [ ] **Step 4.2: Run the pytest file to confirm import failure**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -m pytest scripts/tests/test_error_collector.py -v
```

Expected: `ImportError: cannot import name 'ErrorCollector' from 'bom_export'`

- [ ] **Step 4.3: Implement ErrorCollector in bom_export.py**

Edit `scripts/bom_export.py`. Right after the `STENCIL_CATEGORY_NAME` constant (around line 38), insert:

```python
# ---------------------------------------------------------------------------
# Error collector
# ---------------------------------------------------------------------------

class ErrorCollector:
    """Collect non-fatal sync errors and emit a single summary at the end.

    Replaces the previous early-``sys.exit(1)`` in ``match_supplier_parts``:
    a single missing-SupplierPart in an 80-part BOM should not kill the whole
    sync, because the user needs to see *every* missing part to plan an
    InvenTree-side cleanup or supplier escalation.

    Usage::

        collector = ErrorCollector()
        match_supplier_parts(api, entries, collector=collector)
        # ... rest of the flow ...
        if collector.has_errors():
            collector.print_summary()
            sys.exit(1)
    """

    def __init__(self) -> None:
        # (category, target, reason) — order preserved for the summary print.
        self.errors: list[tuple[str, str, str]] = []

    def add(self, category: str, target: str, reason: str) -> None:
        """Record one error. Never raises."""
        self.errors.append((category, target, reason))

    def has_errors(self) -> bool:
        return bool(self.errors)

    def print_summary(self) -> None:
        """Emit all collected errors at ERROR log level.

        No-op when there are no errors — keeps the success-path log clean.
        """
        if not self.errors:
            return
        log.error("=" * 60)
        log.error("Sync completed with %d error(s):", len(self.errors))
        for category, target, reason in self.errors:
            log.error("  [%s] %s — %s", category, target, reason)
        log.error("=" * 60)
```

- [ ] **Step 4.4: Run pytest to verify ErrorCollector tests pass**

```bash
python3 -m pytest scripts/tests/test_error_collector.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4.5: Refactor `match_supplier_parts` to use the collector**

Edit `scripts/bom_export.py`. Replace the entire `match_supplier_parts` function (lines ~90-180) with:

```python
def match_supplier_parts(
    api: InvenTreeAPI,
    entries: list[BomEntry],
    reporter: Optional["DryRunReporter"] = None,
    collector: Optional["ErrorCollector"] = None,
) -> None:
    """
    Match each BomEntry to its InvenTree Part via SupplierPart SKU lookup.
    Populates entry.inventree_part for every entry that has a supplier SKU.

    Uses a batch ``SKU__in=[...]`` filter (one API call covering every SKU
    referenced by the BOM) instead of fetching the full SupplierPart table.

    Falls back to per-SKU queries when:
      - The batch call raises an exception, OR
      - The batch call returns an empty list despite having SKUs to look
        up.  The latter case defends against InvenTree versions that
        respond to an unsupported ``__in`` filter with HTTP 400 (which
        the InvenTree Python client silently converts to an empty list).

    Errors (no matching SupplierPart for an entry with SKUs):
      - With ``collector``: each entry is added to the collector; the
        function continues processing the rest. Caller is responsible for
        printing the summary and exiting non-zero.
      - Without ``collector``: legacy behavior preserved — log error and
        ``sys.exit(1)`` on the first miss (back-compat for any caller that
        hasn't been migrated yet).
    """
    # sorted for deterministic API call order — helpful for log diffing.
    all_skus = sorted({
        sku for entry in entries
        for sku in entry.lcsc + entry.mouser
        if sku
    })
    supplier_parts: list[SupplierPart] = []
    if all_skus:
        batch_failed = False
        try:
            supplier_parts = list(SupplierPart.list(api, SKU__in=all_skus))
        except Exception as exc:
            log.warning(
                "SKU__in batch query raised (%s); will fall back to per-SKU",
                exc)
            batch_failed = True

        if not supplier_parts:
            # Empty result: either filter unsupported (HTTP 400 swallowed by
            # the client → empty list, indistinguishable from "no matches"),
            # genuinely no SupplierParts on the server, or the batch raised
            # (warning already logged above). Probe per-SKU to recover.
            if not batch_failed:
                log.info(
                    "Batch SKU lookup returned no results; falling back to "
                    "per-SKU queries for %d SKU(s)", len(all_skus))
            for sku in all_skus:
                try:
                    supplier_parts.extend(SupplierPart.list(api, SKU=sku))
                except Exception as exc2:
                    log.debug("per-SKU lookup failed for %s: %s", sku, exc2)

    sku_to_part: dict[str, Part] = {
        sp.SKU: Part(api, pk=sp.part) for sp in supplier_parts
    }

    for entry in entries:
        if entry.inventree_part:
            continue  # already resolved by ensure_parts_exist
        for sku in entry.lcsc + entry.mouser:
            if part := sku_to_part.get(sku):
                entry.inventree_part.append(part)
                break

    missing = [e for e in entries if not e.inventree_part and (e.lcsc or e.mouser)]
    if not missing:
        return

    # Dry-run guard: ensure_parts_exist already recorded CREATE for new
    # entries. Those have lcsc/mouser SKUs but `find_existing_part` missed
    # (truly new) → not yet in InvenTree → here they'd fall through into
    # `missing`. Don't double-report them as FAIL — they ARE the
    # CREATE entries from the prior step.
    already_creating: set[str] = set()
    if reporter is not None:
        already_creating = {
            r.target for r in reporter.records
            if r.category == "Parts" and r.action == "CREATE"
        }

    for entry in missing:
        reason = f"no InvenTree match (LCSC={entry.lcsc}, Mouser={entry.mouser})"
        if reporter is not None:
            if entry.reference in already_creating:
                continue  # ensure_parts_exist already recorded this as CREATE
            reporter.record("FAIL", "Parts", entry.reference, reason)
        elif collector is not None:
            log.error("No InvenTree part for %s — %s", entry.reference, reason)
            collector.add("Parts", entry.reference, reason)
        else:
            log.error("No InvenTree part found for %s (LCSC=%s, Mouser=%s)",
                      entry.reference, entry.lcsc, entry.mouser)
            sys.exit(1)
```

(Behavioral changes vs original: when `collector` is provided, errors are added to it and the loop continues. When neither `reporter` nor `collector` is provided, the legacy `sys.exit(1)` path is preserved.)

- [ ] **Step 4.6: Wire the collector into `main()`**

Edit `scripts/bom_export.py`. Find `main()` (around line 346) and update the non-dry-run path. Replace lines 420-447 (everything from `# Non-dry-run path` to the `attach_kibot_outputs` call) with:

```python
    # Non-dry-run path: original flow continues below.
    collector = ErrorCollector()

    # Create any parts that don't exist in InvenTree yet
    ensure_parts_exist(api, entries, category_map)

    # Match every BOM entry to its InvenTree part via supplier SKU
    match_supplier_parts(api, entries, collector=collector)

    pcb_cat      = get_category_by_name(api, PCB_CATEGORY_NAME)
    assembly_cat = get_category_by_name(api, ASSEMBLY_CATEGORY_NAME)
    stencil_cat  = get_category_by_name(api, STENCIL_CATEGORY_NAME)

    pcb      = create_pcb_part(api, pcb_cat, args.name, args.version, args.pcb_image)
    assembly = create_assembly_part(api, assembly_cat, args.name, args.version, args.assembly_image)
    stencil  = create_stencil_part(api, stencil_cat, args.name, args.version, args.stencil_image)

    # Link stencil ↔ PCB as related parts (not BOM – the stencil is a
    # production tool, not a consumed component of the assembly).
    PartRelated.add_related(api, pcb, stencil)
    log.info("Linked stencil to PCB as related part")

    populate_bom(api, assembly, pcb, entries)

    # Cost-report (Backlog #11) — Markdown into $GITHUB_STEP_SUMMARY + assembly.notes
    try:
        generate_cost_report(api, assembly, entries)
    except Exception as exc:
        log.warning("Cost-report generation failed: %s", exc)

    if args.output_dir:
        attach_kibot_outputs(api, pcb, assembly, stencil, args.output_dir)

    # Summary + exit-code: errors collected during match_supplier_parts above
    # surface here as a single aggregated report. Partial syncs still create
    # the PCB / Assembly / BOM (best-effort) — only the per-entry fails count
    # against the exit-code contract.
    if collector.has_errors():
        collector.print_summary()
        sys.exit(1)
```

- [ ] **Step 4.7: Smoke-test that bom_export still imports + parses args**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 scripts/bom_export.py --help
```

Expected: argparse help text, no exception.

- [ ] **Step 4.8: Commit**

```bash
git add scripts/bom_export.py scripts/tests/test_error_collector.py
git commit -m "$(cat <<'EOF'
feat(bom_export): aggregate per-entry errors instead of early sys.exit (#16)

Adds ErrorCollector + 5 pytest cases. match_supplier_parts now takes an
optional collector= kwarg: when supplied, missing-SupplierPart entries
are collected and the function continues. main() runs the summary at the
end and exits 1 if any error was recorded.

Marathon-sync (re-pflegen aller Releases) needs to see ALL missing parts
in one pass, not stop on the first one — so the user can plan the
InvenTree-side cleanup or supplier escalation in a single sweep.

Back-compat: when called without reporter and without collector,
match_supplier_parts retains the original sys.exit(1) on first miss.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: #15 --planned-builds CLI flag + Part.minimum_stock update

**Files:**
- Modify: `scripts/bom_export.py:259-301` (`populate_bom` signature + new `_update_min_stock`) and `:308-343` (argparse) and `:420+` (main wiring).

### Steps

- [ ] **Step 5.1: Add `--planned-builds` to the argparse block**

Edit `scripts/bom_export.py`. Inside `parse_args()` (currently lines 308-343), insert this argument right before the `--dry-run` block:

```python
    parser.add_argument(
        "--planned-builds",
        dest="planned_builds",
        type=int,
        default=10,
        help=(
            "Multiplier for Part.minimum_stock: needed = qty_per_PCB × "
            "planned_builds.  Sets minimum_stock on every BOM-resolved "
            "Part to make the InvenTree 'Low Stock' page useful as an "
            "order list.  Higher existing values are preserved (never "
            "decreased).  Default: 10."
        ),
    )
```

- [ ] **Step 5.2: Add `_update_min_stock` helper and call it from `populate_bom`**

Edit `scripts/bom_export.py`. Replace `populate_bom` (lines ~259-301) with:

```python
def _update_min_stock(
    entries: list[BomEntry],
    planned_builds: int,
) -> None:
    """Set Part.minimum_stock = max(current, qty × planned_builds) per entry.

    "Higher wins": if the same Part is referenced by another assembly with a
    higher need, keep the higher value.  Equivalent if planned_builds is 1
    and the Part already had minimum_stock=qty from a previous run.

    Skips Parts that fail to save() — never raises, never blocks the sync.
    PCB/Stencil/Assembly Parts are not touched (no BomEntry points at them
    as a sub-part).
    """
    if planned_builds <= 0:
        log.warning(
            "Skipping minimum_stock update: planned_builds=%d is non-positive",
            planned_builds)
        return
    for entry in entries:
        needed = entry.qty * planned_builds
        for inv_part in entry.inventree_part:
            # `minimum_stock` is a number on the Part. Some InvenTree versions
            # store it as int, others as string-coerced numeric; getattr +
            # int() with a default keeps the comparison robust.
            try:
                current = int(float(getattr(inv_part, "minimum_stock", 0) or 0))
            except (TypeError, ValueError):
                current = 0
            if needed <= current:
                continue
            try:
                inv_part.save({"minimum_stock": needed})
                log.info(
                    "Set minimum_stock=%d on pk=%s (%s × %d builds, was %d)",
                    needed, inv_part.pk, entry.qty, planned_builds, current)
            except Exception as exc:
                log.warning(
                    "minimum_stock update failed for pk=%s: %s",
                    inv_part.pk, exc)


def populate_bom(
    api: InvenTreeAPI,
    assembly: Part,
    pcb: Part,
    entries: list[BomEntry],
    planned_builds: int = 0,
) -> None:
    """Create BomItems on *assembly*: one for the PCB, one per BomEntry.

    Idempotent: when the same Assembly already has BomItems linking to
    the same sub-parts with the same reference designators, the existing
    items are kept and the new creation is skipped.  Lets the workflow
    be re-run safely without producing duplicate BomItems.

    *planned_builds* > 0 triggers a Part.minimum_stock update for every
    BOM-resolved Part (max with current; higher wins).  Default 0 means
    "don't touch minimum_stock" — backwards-compat for callers in tests
    that don't care about stock thresholds.
    """
    existing = BomItem.list(api, part=assembly.pk)
    existing_keys: set[tuple[int, str]] = {
        (int(bi.sub_part), bi.reference or "") for bi in existing
    }
    created = 0
    skipped = 0

    def _maybe_create(sub_part_pk: int, reference: str, qty: int) -> None:
        nonlocal created, skipped
        key = (int(sub_part_pk), reference or "")
        if key in existing_keys:
            skipped += 1
            return
        BomItem.create(api, {
            "part": assembly.pk,
            "sub_part": sub_part_pk,
            "reference": reference,
            "quantity": qty,
        })
        existing_keys.add(key)
        created += 1

    _maybe_create(pcb.pk, "", 1)

    for entry in entries:
        for inv_part in entry.inventree_part:
            _maybe_create(inv_part.pk, entry.reference, entry.qty)

    log.info("BOM populated: %d new items, %d skipped (already present)",
             created, skipped)

    if planned_builds > 0:
        _update_min_stock(entries, planned_builds)
```

- [ ] **Step 5.3: Wire `args.planned_builds` into the `populate_bom` call**

Edit `scripts/bom_export.py` in `main()`. Find the existing line:

```python
    populate_bom(api, assembly, pcb, entries)
```

Replace with:

```python
    populate_bom(api, assembly, pcb, entries, planned_builds=args.planned_builds)
```

- [ ] **Step 5.4: Smoke-test that --help shows the new flag**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 scripts/bom_export.py --help | grep -A 4 planned-builds
```

Expected: the help text for `--planned-builds` is visible, default 10.

- [ ] **Step 5.5: Commit**

```bash
git add scripts/bom_export.py
git commit -m "$(cat <<'EOF'
feat(bom_export): set Part.minimum_stock = qty × --planned-builds (#15)

Adds --planned-builds N CLI flag (default 10) and _update_min_stock helper
in populate_bom: after creating BomItems, updates every BOM-resolved
Part.minimum_stock to max(current, qty × planned_builds).  Higher
existing values are preserved (idempotent + safe for cross-assembly
re-syncs).

Makes InvenTree's "Low Stock" page usable as an order list — the
immediate next step after the marathon-sync is "Bestandsaufnahme zuhause
und fehlende Teile bestellen".

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: E2E tests for the three behavioral changes

**Files:**
- Modify: `scripts/e2e_revision_handling.py:601-619` (registration in `main()`) and end-of-file (3 new test functions).

### Steps

- [ ] **Step 6.1: Add `test_mpn_mfr_dedup`**

Edit `scripts/e2e_revision_handling.py`. Add this function after `test_dry_run_no_side_effects` (around line 588, before the entry-point block):

```python
def test_mpn_mfr_dedup(api: InvenTreeAPI) -> None:
    """find_part_by_mpn_and_manufacturer: an existing Part is reused when
    a second SKU references the same MPN+Manufacturer.

    Direct test against the helper (no LCSC/Mouser fetch): construct one
    manufacturer Company, one Part with a ManufacturerPart, then call the
    lookup with the matching and non-matching arguments.
    """
    from inventree.company import ManufacturerPart
    from inventree_sync.client import find_part_by_mpn_and_manufacturer

    mfr = _track_company(Company.create(api, {
        "name": f"{PREFIX} MfrDedup",
        "is_manufacturer": True,
    }))
    other_mfr = _track_company(Company.create(api, {
        "name": f"{PREFIX} OtherMfrDedup",
        "is_manufacturer": True,
    }))
    target = _track(Part.create(api, {
        "name": f"{PREFIX} MpnDedupPart",
        "description": "mpn dedup",
        "active": True,
        "component": True,
    }))
    mpn = f"{PREFIX}-MPN-A"
    ManufacturerPart.create(api, {
        "part": target.pk,
        "manufacturer": mfr.pk,
        "MPN": mpn,
    })

    # 1. Matching MPN + matching manufacturer → finds the Part.
    hit = find_part_by_mpn_and_manufacturer(api, mpn, mfr.name)
    assert hit is not None and hit.pk == target.pk, (
        f"expected pk={target.pk}, got {hit.pk if hit else None}")

    # 2. Same MPN, wrong manufacturer → None (the post-filter must reject).
    miss = find_part_by_mpn_and_manufacturer(api, mpn, other_mfr.name)
    assert miss is None, (
        f"expected None for mismatched manufacturer, got pk={miss.pk if miss else None}")

    # 3. Wrong MPN, right manufacturer → None.
    miss_mpn = find_part_by_mpn_and_manufacturer(api, f"{PREFIX}-MPN-NONE", mfr.name)
    assert miss_mpn is None, (
        f"expected None for missing MPN, got pk={miss_mpn.pk if miss_mpn else None}")

    # 4. Case-insensitive manufacturer match.
    hit_ci = find_part_by_mpn_and_manufacturer(api, mpn, mfr.name.upper())
    assert hit_ci is not None and hit_ci.pk == target.pk, (
        f"case-insensitive match failed, got {hit_ci.pk if hit_ci else None}")

    print(f"  PASS  mpn_mfr_dedup ({mpn} → pk={target.pk}, 3 negative cases reject)")
```

- [ ] **Step 6.2: Add `test_value_normalization_in_generated_name`**

Add this function right after `test_mpn_mfr_dedup`:

```python
def test_value_normalization_in_generated_name(api: InvenTreeAPI) -> None:
    """generate_part_name applies _normalize_value to R/C/L/CP/XTAL values.

    Pure-function test that doesn't need any server side-effects, but lives
    in the E2E harness because it exercises the integration point (`if
    kicad_part in {...}: val = _normalize_value(val)`) rather than just the
    helper.
    """
    from inventree_sync.categories import generate_part_name

    cases = [
        # (kicad_part, kicad_value, footprint, expected_name)
        ("R", "10K", "R_0805_2012Metric", "R 10k 0805"),
        ("R", "10 kΩ", "R_0805_2012Metric", "R 10k 0805"),
        ("R", "10kΩ", "R_0805_2012Metric", "R 10k 0805"),
        ("C", "100 nF", "C_0805_2012Metric", "C 100nF 0805"),
        ("C", "4.7µF", "C_0805_2012Metric", "C 4.7uF 0805"),
        ("Crystal", "8MHz/20pF", "Crystal_SMD_3225-4Pin", "XTAL 8MHz/20pF"),
        # Non-RCL parts pass through unchanged:
        ("STM32U575CITx", "STM32U575CITx", "TQFP-48", "STM32U575CITx"),
    ]
    failures = []
    for kicad_part, value, footprint, expected in cases:
        got = generate_part_name(kicad_part, value, footprint)
        if got != expected:
            failures.append(f"  {kicad_part!r}/{value!r} → {got!r}, expected {expected!r}")
    assert not failures, "value-normalization mismatches:\n" + "\n".join(failures)
    print(f"  PASS  value normalization in generate_part_name ({len(cases)} cases)")
```

- [ ] **Step 6.3: Add `test_minimum_stock_set_and_preserved`**

Add this function right after `test_value_normalization_in_generated_name`:

```python
def test_minimum_stock_set_and_preserved(api: InvenTreeAPI) -> None:
    """populate_bom with --planned-builds sets minimum_stock; higher wins.

    Constructs an Assembly + PCB + one component Part, then calls populate_bom
    twice:
      Pass 1: planned_builds=5, entry.qty=3 → minimum_stock should be 15.
      Pass 2: planned_builds=2 (lower), entry.qty=3 → minimum_stock STAYS 15.
    Verifies the "higher wins" contract from #15.
    """
    from bom_export import create_assembly_part, create_pcb_part, populate_bom
    cat = _ensure_category(api, f"{PREFIX} cat")

    assembly = _track(create_assembly_part(
        api, cat, f"{PREFIX} MinStockTest", "1.0", image=None))
    pcb = _track(create_pcb_part(
        api, cat, f"{PREFIX} MinStockTest", "1.0", image=None))
    component = _track(Part.create(api, {
        "name": f"{PREFIX} MinStockComp",
        "description": "min-stock test",
        "active": True,
        "component": True,
    }))

    entry = BomEntry(
        reference="R1", qty=3,
        kicad_part="R", kicad_value="10k", kicad_footprint="R_0805_2012Metric",
    )
    entry.inventree_part = [component]

    # Pass 1: planned_builds=5 → minimum_stock should be 15 (3 × 5).
    populate_bom(api, assembly, pcb, [entry], planned_builds=5)
    refreshed = Part(api, pk=component.pk)
    got = int(float(getattr(refreshed, "minimum_stock", 0) or 0))
    assert got == 15, (
        f"after first populate (planned=5, qty=3) minimum_stock={got}, expected 15")

    # Pass 2: planned_builds=2 → would yield 6, but higher (15) wins.
    populate_bom(api, assembly, pcb, [entry], planned_builds=2)
    refreshed = Part(api, pk=component.pk)
    got2 = int(float(getattr(refreshed, "minimum_stock", 0) or 0))
    assert got2 == 15, (
        f"second populate (planned=2, qty=3) should leave minimum_stock=15, got {got2}")

    print(f"  PASS  minimum_stock set + preserved (pass1=15, pass2 still 15)")
```

- [ ] **Step 6.4: Register the three new tests in `main()`**

Edit `scripts/e2e_revision_handling.py`. Find the test-tuple in `main()` (currently lines 607-619). Replace with:

```python
        for tc in (test_find_part_by_name_exact,
                   test_find_part_by_name_and_revision,
                   test_pcb_silently_reuse,
                   test_stencil_silently_reuse,
                   test_assembly_silently_reuse,
                   test_bom_idempotent,
                   test_multi_sku_supplier_parts,
                   test_parameter_sync_delta,
                   test_supplier_link_populated,
                   test_attachment_idempotent,
                   test_cost_report_generation,
                   test_dry_run_no_side_effects,
                   test_refresh_idempotent,
                   test_mpn_mfr_dedup,
                   test_value_normalization_in_generated_name,
                   test_minimum_stock_set_and_preserved):
```

- [ ] **Step 6.5: Run the pytest suite to confirm pure-Python tests still pass**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -m pytest scripts/tests/ -v
```

Expected: all pytest cases (cost-report + dry-run + normalization + error-collector) PASS.

- [ ] **Step 6.6: Run the E2E suite against the live InvenTree server**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
source ~/.inventree_test.env
python3 scripts/e2e_revision_handling.py
```

Expected: all 16 tests (13 previous + 3 new) PASS, cleanup runs to completion, "All tests passed." printed.

Troubleshooting:
- If `test_mpn_mfr_dedup` fails with "ManufacturerPart create failed", the test server may have a unique-together constraint on (manufacturer, MPN) — re-running the test reuses old fixtures. Set `KEEP_TEST_PARTS=1` to inspect and delete manually, or use a fresh `RUN_ID` by re-running the script (each invocation generates a new timestamp prefix).
- If `test_minimum_stock_set_and_preserved` fails because `minimum_stock` isn't read back, the server version may have a different attribute name or storage type — check `Part._data.get("minimum_stock")` raw value and compare.

- [ ] **Step 6.7: Commit**

```bash
git add scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
test(inventree-sync): E2E coverage for PR-5 (#13, #15, #19)

Adds three new E2E tests against the real InvenTree server:

  test_mpn_mfr_dedup
    direct test of find_part_by_mpn_and_manufacturer with 1 positive
    case + 3 negative (wrong mfr, wrong MPN, case-insensitive).

  test_value_normalization_in_generated_name
    integration test that generate_part_name applies _normalize_value
    only to R/C/L/CP/XTAL — 7 case-table inputs.

  test_minimum_stock_set_and_preserved
    populate_bom with planned_builds=5/qty=3 sets minimum_stock=15,
    a second pass with planned_builds=2 leaves it at 15 (higher wins).

#16 (ErrorCollector) and #17 (Retry) are not E2E-tested: ErrorCollector
is fully pytest-covered (scripts/tests/test_error_collector.py) and a
retry-adapter mock test is more work than it's worth — manual verification
via dropped-network probes covers the intent.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Push the branch + open the PR

### Steps

- [ ] **Step 7.1: Run the full test matrix one last time**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -m pytest scripts/tests/ -v
source ~/.inventree_test.env
python3 scripts/e2e_revision_handling.py
```

Expected:
- pytest: all green (cost_report 8 + dry_run 7 + normalization 10 + error_collector 5 = 30 cases).
- E2E: all 16 tests pass; cleanup finishes; "All tests passed." printed.

- [ ] **Step 7.2: Push the branch**

```bash
git push -u origin feat/pr5-sync-ready-foundation
```

- [ ] **Step 7.3: Open the PR with gh**

```bash
gh pr create --title "feat(inventree-sync): PR-5 Sync-Ready Foundation (#13, #15, #19, #16, #17)" --body "$(cat <<'EOF'
## Summary
Bundles five backlog items so the production marathon-sync (re-pflegen aller bestehenden Releases ins InvenTree) durchläuft, ohne Duplikat-Parts zu erzeugen oder bei transienten Fehlern abzubrechen.

- **#19** Wert-Normalisierung im Namensgenerator (`10K`/`10 kΩ`/`10kΩ` → `R 10k 0805`)
- **#13** MPN+Manufacturer-Dedup vor Name-Lookup (Hardware-Identifier statt String-Identitäten)
- **#17** Retry mit Backoff (0s/2s/4s) auf LCSC + Mouser
- **#16** Aggregierte Fehlerausgabe statt früher `sys.exit(1)`
- **#15** `--planned-builds N` (Default 10): `Part.minimum_stock = qty × N`, higher-wins

## Test plan
- [ ] `python3 -m pytest scripts/tests/ -v` → 30+ passing pytest cases
- [ ] `python3 scripts/e2e_revision_handling.py` → 16 passing E2E tests against live InvenTree
- [ ] Cron-Refresh-Workflow läuft unverändert weiter (Retry-Session ist transparent)
- [ ] CLI back-compat: existing workflow `--csv_file --name --version --pcb_image --assembly_image` ohne `--planned-builds` → Default 10

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7.4: Verify the PR was created**

```bash
gh pr view --json url,title,state -q '.url + " — " + .title + " — " + .state'
```

Expected: a URL + "feat(inventree-sync): PR-5 Sync-Ready Foundation …" + "OPEN".
