"""
client.py – Low-level InvenTree API helpers for creating and updating parts,
supplier records, manufacturer records, and price breaks.
"""

import logging
import os
import tempfile
import urllib.parse
from typing import Optional

import requests

from inventree.api import InvenTreeAPI
from inventree.company import Company, ManufacturerPart, SupplierPart, SupplierPriceBreak
from inventree.part import Part, PartCategory

from .models import PartData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Image-download headers
# ---------------------------------------------------------------------------
# PerimeterX (Mouser) + LCSC-CDN-defeating header set, verified 2026-06-03
# against real CDNs.  Five of the six mandatory headers are presence-only
# from PerimeterX's perspective today — we set realistic browser values
# anyway so a future PerimeterX tightening doesn't silently break the
# Auto-Release workflow.  See docs/superpowers/specs/2026-06-03-bug-
# mouser-image-headers.md for the full analysis.
#
# Falls Image-Downloads später wieder 4–5 kB HTML statt Bild zurückgeben:
#   1. Chrome-Version unten gegen current-stable refreshen
#      (https://www.useragentstring.com/pages/Chrome/).
#   2. `python3 scripts/probe_supplier_images.py` laufen lassen — zeigt
#      sofort, ob das Problem an UA, Sec-Fetch-*, oder etwas Neuem liegt.
#   3. Spec-Doc mit dem aktualisierten Befund updaten.

_DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _image_headers(image_url: str) -> dict[str, str]:
    """Browser-fingerprint headers for PerimeterX-protected supplier CDNs.

    The same set works for both Mouser (PerimeterX) and LCSC (asset CDN),
    so callers don't need to switch on host.
    """
    parts = urllib.parse.urlsplit(image_url)
    site_root = f"{parts.scheme}://{parts.netloc}/"
    return {
        "User-Agent":         _DESKTOP_UA,
        "Accept":             "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language":    "en-US,en;q=0.9",
        "Referer":            site_root,
        "Sec-Fetch-Dest":     "image",
        "Sec-Fetch-Mode":     "no-cors",
        "Sec-Fetch-Site":     "same-origin",
        "Sec-Ch-Ua":          '"Chromium";v="130", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile":   "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
    }


def _try_mouser_hd_url(url: str) -> str:
    """Upgrade a Mouser thumbnail URL to its HD variant when possible.

    The Mouser API typically returns paths like
        https://www.mouser.com/images/<mfr>/lrg/<sku>_SPL.jpg
        https://www.mouser.com/images/<mfr>/images/<sku>_SPL.jpg
    The same path with ``/hd/`` in place of ``/lrg/`` or ``/images/``
    returns a ~1000-px version (10–20× larger).  Callers are expected
    to fall back to *url* on 404/validation-fail.

    Examples
    --------
    >>> _try_mouser_hd_url("https://www.mouser.com/images/ti/lrg/X_t.jpg")
    'https://www.mouser.com/images/ti/hd/X_t.jpg'
    >>> _try_mouser_hd_url("https://www.lcsc.com/foo.jpg")
    'https://www.lcsc.com/foo.jpg'
    """
    if "mouser." not in url:
        return url
    # Order matters: try /lrg/ → /hd/ first (more specific), then /images/ → /hd/.
    # Use rpartition so we hit the *variant-folder* /images/ near the SKU, not the
    # leading host-level /images/ in URLs like
    # https://www.mouser.at/images/<mfr>/images/<sku>_SPL.jpg.
    for needle in ("/lrg/", "/images/"):
        if needle in url:
            head, _, tail = url.rpartition(needle)
            return f"{head}/hd/{tail}"
    return url


def get_or_create_supplier(api: InvenTreeAPI, name: str) -> Optional[Company]:
    """Return the supplier Company by name, creating it if not found."""
    try:
        companies = Company.list(api, name=name, is_supplier=True)
        if companies:
            return companies[0]
        return Company.create(api, {"name": name, "is_supplier": True, "is_manufacturer": False})
    except Exception as exc:
        logger.error("get_or_create_supplier(%s) failed: %s", name, exc)
        return None


def get_or_create_manufacturer(api: InvenTreeAPI, name: str) -> Optional[Company]:
    """Return (or create) a manufacturer Company by name (case-insensitive)."""
    try:
        companies = Company.list(api, is_manufacturer=True)
        for c in companies:
            if c.name.lower() == name.lower():
                return c
        return Company.create(api, {"name": name, "is_manufacturer": True, "is_supplier": False})
    except Exception as exc:
        logger.error("get_or_create_manufacturer(%s) failed: %s", name, exc)
        return None


def upload_image_from_url(part: Part, url: str) -> None:
    """Download an image from *url* and attach it to *part*.

    For Mouser URLs, tries the HD variant first and falls back to the
    original on failure.  Validates Content-Type + body size before
    upload to catch CDN bot-block pages that come back as HTTP 200 +
    HTML (see ``_image_headers`` docstring).
    """
    if not url:
        return

    # Build the list of URL candidates: HD first (if applicable), then original.
    candidates: list[str] = []
    hd = _try_mouser_hd_url(url)
    if hd != url:
        candidates.append(hd)
    candidates.append(url)

    for candidate in candidates:
        try:
            resp = requests.get(candidate, timeout=20, headers=_image_headers(candidate))
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Image download failed (%s): %s", candidate, exc)
            continue

        # Content-Type is case-insensitive per RFC 9110; lowercase once at read time.
        content_type = resp.headers.get("Content-Type", "").lower()
        body = resp.content
        # 200 B floor: smaller than any real product thumbnail, larger than any
        # plausible PerimeterX-block-HTML or empty-body error response.
        if not content_type.startswith("image/") or len(body) < 200:
            snippet = body[:80].decode("utf-8", errors="replace").strip()
            logger.warning(
                "Image rejected for %s (ct=%r size=%d). First 80 B: %r",
                candidate, content_type, len(body), snippet,
            )
            continue

        if "jpeg" in content_type or "jpg" in content_type:
            suffix = ".jpg"
        elif "png" in content_type:
            suffix = ".png"
        elif "webp" in content_type:
            suffix = ".webp"
        elif "avif" in content_type:
            suffix = ".avif"
        elif "gif" in content_type:
            suffix = ".gif"
        else:
            suffix = ".jpg"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(body)
                tmp_path = tmp.name
            part.uploadImage(tmp_path)
            logger.info("Uploaded image to part %s (from %s)", part.pk, candidate)
            return  # Erfolg — keine weiteren Fallbacks
        except Exception as exc:
            logger.warning("Image upload failed for part %s: %s", part.pk, exc)
            return  # Upload-Fehler: kein Fallback, sonst evtl. doppelter Upload
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    logger.warning("No usable image obtained from any variant of %s", url)


def _add_price_breaks(
    api: InvenTreeAPI,
    supplier_part: SupplierPart,
    price_breaks: dict,
    currency: str,
) -> None:
    """Create price break records on *supplier_part*."""
    for qty, price in sorted(price_breaks.items()):
        try:
            SupplierPriceBreak.create(api, {
                "part": supplier_part.pk,
                "quantity": qty,
                "price": str(price),
                "price_currency": currency,
            })
        except Exception as exc:
            logger.warning("Price break creation failed (qty=%s): %s", qty, exc)


def create_part_in_inventree(
    api: InvenTreeAPI,
    name: str,
    part_data: PartData,
    category: Optional[PartCategory],
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
) -> Optional[Part]:
    """
    Create an InvenTree Part (with manufacturer/supplier parts) from *part_data*.
    Returns the created Part, or None on failure.
    """
    # 1. Create the base part
    part_payload = {
        "name": name,
        "description": part_data.description or name,
        "component": True,
        "purchaseable": True,
        "active": True,
    }
    if category:
        part_payload["category"] = category.pk
    if part_data.datasheet_url:
        part_payload["link"] = part_data.datasheet_url

    try:
        part = Part.create(api, part_payload)
        logger.info("Created part '%s' (pk=%s)", name, part.pk)
    except Exception as exc:
        logger.error("Part creation failed for '%s': %s", name, exc)
        return None

    # 2. Upload image
    if part_data.image_url:
        upload_image_from_url(part, part_data.image_url)

    # 3. Manufacturer part
    if part_data.mpn and part_data.manufacturer:
        manufacturer = get_or_create_manufacturer(api, part_data.manufacturer)
        if manufacturer:
            try:
                ManufacturerPart.create(api, {
                    "part": part.pk,
                    "manufacturer": manufacturer.pk,
                    "MPN": part_data.mpn,
                })
                logger.info("Created ManufacturerPart %s / %s", part_data.manufacturer, part_data.mpn)
            except Exception as exc:
                logger.warning("ManufacturerPart creation failed: %s", exc)

    # 4. LCSC supplier part
    if part_data.lcsc_sku and lcsc_supplier:
        try:
            sp = SupplierPart.create(api, {
                "part": part.pk,
                "supplier": lcsc_supplier.pk,
                "SKU": part_data.lcsc_sku,
                "manufacturer_part": None,
            })
            if part_data.price_breaks:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
        except Exception as exc:
            logger.warning("LCSC SupplierPart creation failed (%s): %s", part_data.lcsc_sku, exc)

    # 5. Mouser supplier part
    if part_data.mouser_sku and mouser_supplier:
        try:
            sp = SupplierPart.create(api, {
                "part": part.pk,
                "supplier": mouser_supplier.pk,
                "SKU": part_data.mouser_sku,
            })
            # Use Mouser price breaks only if LCSC had none
            if part_data.price_breaks and not part_data.lcsc_sku:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
        except Exception as exc:
            logger.warning("Mouser SupplierPart creation failed (%s): %s", part_data.mouser_sku, exc)

    return part


def find_existing_part(
    api: InvenTreeAPI, lcsc_sku: str, mouser_sku: str
) -> Optional[Part]:
    """Return the InvenTree Part if a SupplierPart with a matching SKU exists."""
    for sku in filter(None, [lcsc_sku, mouser_sku]):
        try:
            sp_list = SupplierPart.list(api, SKU=sku)
            if sp_list:
                return Part(api, pk=sp_list[0].part)
        except Exception as exc:
            logger.debug("SupplierPart lookup failed for SKU=%s: %s", sku, exc)
    return None


def find_part_by_name(api: InvenTreeAPI, name: str) -> Optional[Part]:
    """Return the InvenTree Part with an exact name match, or None."""
    if not name:
        return None
    try:
        results = Part.list(api, search=name)
        for part in results:
            if part.name == name:
                return part
    except Exception as exc:
        logger.debug("Part name lookup failed for '%s': %s", name, exc)
    return None


def ensure_supplier_parts(
    api: InvenTreeAPI,
    part: Part,
    part_data: PartData,
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
) -> None:
    """Add any missing SupplierParts to an already-existing InvenTree Part."""
    try:
        existing_skus = {sp.SKU for sp in SupplierPart.list(api, part=part.pk)}
    except Exception:
        existing_skus = set()

    if part_data.lcsc_sku and lcsc_supplier and part_data.lcsc_sku not in existing_skus:
        try:
            sp = SupplierPart.create(api, {
                "part": part.pk,
                "supplier": lcsc_supplier.pk,
                "SKU": part_data.lcsc_sku,
            })
            if part_data.price_breaks:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
        except Exception as exc:
            logger.warning("Could not add LCSC supplier part: %s", exc)

    if part_data.mouser_sku and mouser_supplier and part_data.mouser_sku not in existing_skus:
        try:
            SupplierPart.create(api, {
                "part": part.pk,
                "supplier": mouser_supplier.pk,
                "SKU": part_data.mouser_sku,
            })
        except Exception as exc:
            logger.warning("Could not add Mouser supplier part: %s", exc)
