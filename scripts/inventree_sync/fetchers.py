"""
fetchers.py – Supplier data fetchers for LCSC and Mouser.
"""

import html
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


# Pre-compiled regex for the HTML-tag-strip in _clean_description below.
# Module-level so it's compiled once per process.
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_description(text: Optional[str]) -> Optional[str]:
    """Strip HTML tags and decode HTML entities from a supplier description.

    InvenTree's Part.description field rejects strings containing HTML
    markup with HTTP 400 ("Remove HTML tags from this value"). Both LCSC
    and Mouser occasionally return descriptions with <b>/<sup>-style tags
    and/or HTML entities like &reg;, &trade;, &amp;.

    Order matters: ``html.unescape`` runs FIRST so encoded tag-syntax
    (``&lt;b&gt;``) gets converted to real tags, which are then stripped.
    The reverse order would leave decoded tag-syntax intact in the output.

    Returns the input unchanged when it is falsy (None, "") so callers
    don't need to special-case missing supplier fields.

    Examples:
        >>> _clean_description("SIMPLE SWITCHER&reg; buck regulator")
        'SIMPLE SWITCHER® buck regulator'
        >>> _clean_description("<b>10kΩ</b> &plusmn;1%")
        '10kΩ ±1%'
        >>> _clean_description("")
        ''
    """
    if not text:
        return text
    return _HTML_TAG_RE.sub("", html.unescape(text)).strip()


def _make_retry_session() -> requests.Session:
    """Build a requests.Session with urllib3 Retry mounted on http(s)://.

    Retries on transient distributor-side failures so a single 502 from
    LCSC or a Mouser-API hiccup doesn't kill an 80-part marathon-sync:

      total=3              — three retries beyond the initial attempt
      backoff_factor=1     — urllib3 sleeps backoff_factor * 2^(n-1) before
                              retry n, with n=1 returning 0. That yields
                              0s before retry 1, 2s before retry 2, 4s
                              before retry 3 — the 0/2/4 schedule the spec
                              calls for. (backoff_factor=2 would give 0/4/8.)
      status_forcelist     — 429 (rate-limit) + 5xx server errors
      allowed_methods      — GET (LCSC detail) + POST (LCSC search, Mouser)
      raise_on_status=False — the urllib3 retry layer does not raise on
                              the final response; the requests-level
                              ``resp.raise_for_status()`` call in each
                              fetcher still handles 4xx/5xx that aren't
                              in status_forcelist (e.g. 404). Keeping
                              this False here avoids double-raising
                              between the two layers.

    Image downloads in client.py.upload_image_from_url do NOT use this
    session — PerimeterX blocks are not transient and a retry only
    floods the logs.
    """
    session = requests.Session()
    retry = urllib3.util.Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class LCSCFetcher:
    """Fetches part data from the LCSC wmsc API."""

    _UA = _IOS_UA

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_by_sku(self, lcsc_sku: str) -> Optional[PartData]:
        """Fetch a single part by its LCSC product code."""
        url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={lcsc_sku}"
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("LCSC fetch_by_sku(%s) failed: %s", lcsc_sku, exc)
            return None

        result = body.get("result")
        if not result:
            logger.warning("LCSC fetch_by_sku(%s): empty result", lcsc_sku)
            return None
        return self._parse(result, lcsc_sku=lcsc_sku)

    def fetch_by_mpn(self, mpn: str) -> Optional[PartData]:
        """Search LCSC by MPN; prefer exact match.

        The search endpoint returns minimal product data (no paramVOList), so
        after identifying the right product code we always call fetch_by_sku to
        get the full detail (parameters, images, price breaks, …).
        """
        url = "https://wmsc.lcsc.com/ftps/wm/search/v2/global"
        try:
            resp = self.session.post(url, json={"keyword": mpn}, timeout=15)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("LCSC fetch_by_mpn(%s) failed: %s", mpn, exc)
            return None

        result = body.get("result", {})
        if not result:
            return None

        # Direct match hint from the API
        tip = result.get("tipProductDetailUrlVO")
        if tip:
            code = tip.get("productCode")
            if code:
                return self.fetch_by_sku(code)

        # Walk the search result list
        product_list = (
            result.get("productSearchResultVO", {}).get("productList") or []
        )
        # Prefer exact MPN match, fall back to first result
        best_code = None
        for product in product_list:
            if product.get("productModel", "").upper() == mpn.upper():
                best_code = product.get("productCode")
                break
        if best_code is None and product_list:
            best_code = product_list[0].get("productCode")

        if best_code:
            return self.fetch_by_sku(best_code)

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_datasheet(url: str) -> str:
        """Rewrite datasheet CDN URLs to the wmsc mirror."""
        if not url:
            return url
        return url.replace(
            "//datasheet.lcsc.com/",
            "//wmsc.lcsc.com/wmsc/upload/file/pdf/v2/",
        )

    def _parse(self, product: dict, lcsc_sku: str = "") -> PartData:
        """Convert a raw LCSC product dict into a PartData."""
        # Image: prefer big image, fall back to first in list
        image_url = product.get("productImageUrlBig", "")
        if not image_url:
            images = product.get("productImages") or []
            if images:
                image_url = images[0]

        # Datasheet
        datasheet = self._fix_datasheet(product.get("pdfUrl", ""))

        # Parameters
        params = {}
        for p in product.get("paramVOList") or []:
            name = p.get("paramNameEn", "").strip()
            value = p.get("paramValueEn", "").strip()
            if name and value:
                params[name] = value

        # Price breaks  {ladder_qty: unit_price_eur}
        price_breaks = {}
        for pb in product.get("productPriceList") or []:
            try:
                qty = int(pb["ladder"])
                price = float(pb["currencyPrice"])
                price_breaks[qty] = price
            except (KeyError, ValueError, TypeError):
                pass

        sku = lcsc_sku or product.get("productCode", "")

        return PartData(
            mpn=product.get("productModel", ""),
            manufacturer=product.get("brandNameEn", ""),
            description=_clean_description(product.get("productDescEn", "")),
            image_url=image_url,
            datasheet_url=datasheet,
            lcsc_sku=sku,
            package=product.get("encapStandard", ""),
            parameters=params,
            price_breaks=price_breaks,
            currency="EUR",
        )


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
        # Same shape as LCSCFetcher's session via _make_retry_session.
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

        parts = body.get("SearchResults", {}).get("Parts") or []
        if not parts:
            logger.warning("Mouser fetch(%s): no results", mouser_sku)
            return None

        p = parts[0]

        # Strip HTML tags + decode entities (shared with LCSC via _clean_description).
        description = _clean_description(p.get("Description", ""))

        # Category
        category_path = []
        cat = p.get("Category", "").strip()
        if cat:
            category_path = [cat]

        # Price breaks
        price_breaks = {}
        currency = "EUR"
        for pb in p.get("PriceBreaks") or []:
            try:
                qty = int(pb["Quantity"])
                price = self._parse_price(pb.get("Price", "0"))
                price_breaks[qty] = price
                if pb.get("Currency"):
                    currency = pb["Currency"]
            except (KeyError, ValueError, TypeError):
                pass

        return PartData(
            mpn=p.get("ManufacturerPartNumber", ""),
            manufacturer=p.get("Manufacturer", ""),
            description=description,
            image_url=p.get("ImagePath", ""),
            datasheet_url=p.get("DataSheetUrl", ""),
            mouser_sku=mouser_sku,
            category_path=category_path,
            price_breaks=price_breaks,
            currency=currency,
            parameters=self._parse_attributes(p),
        )

    @staticmethod
    def _parse_attributes(product: dict) -> dict[str, str]:
        """Extract parameters from Mouser ProductAttributes list.

        Mouser API v2 returns attributes as a list of {"AttributeName": str,
        "AttributeValue": str} pairs.  We strip both sides and skip empty
        rows.  If a name appears multiple times, the last value wins
        (Mouser does emit duplicates occasionally for unit-aware fields).
        Non-string values are coerced via ``str()`` because Mouser occasionally
        returns numeric values for numeric-only specs (e.g. ``-40`` for an
        operating-temperature minimum).  None values are skipped.
        """
        params: dict[str, str] = {}
        for attr in product.get("ProductAttributes") or []:
            name_raw = attr.get("AttributeName")
            value_raw = attr.get("AttributeValue")
            if name_raw is None or value_raw is None:
                continue
            name = str(name_raw).strip()
            value = str(value_raw).strip()
            if not name or not value:
                continue
            params[name] = value
        return params

    @staticmethod
    def _parse_price(price_str: str) -> float:
        """
        Parse a Mouser price string into a float.
        Handles formats like "€ 7,07", "0.1234", "$ 1.23".
        """
        cleaned = re.sub(r"[^\d,.]", "", price_str.strip())
        if not cleaned:
            return 0.0
        last_comma = cleaned.rfind(",")
        last_dot = cleaned.rfind(".")
        if last_comma > last_dot:
            # European format: 7,07 or 1.234,56
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US format: remove commas used as thousands separator
            cleaned = cleaned.replace(",", "")
        return float(cleaned)
