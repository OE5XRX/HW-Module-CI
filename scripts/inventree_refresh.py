#!/usr/bin/env python3
"""
inventree_refresh.py – Maintenance script: refresh existing Parts.

Iterates every InvenTree Part that has at least one SupplierPart at
LCSC or Mouser, re-fetches supplier data (image, datasheet URL, parameters,
price-breaks), and updates the Part. Conservative on description: only
sets it if the Part currently has an empty description (manual UI edits
are preserved).

Required env vars:
    INVENTREE_API_HOST
    INVENTREE_API_TOKEN  (or USERNAME + PASSWORD)
    MOUSER_API_KEY

Usage:
    python3 scripts/inventree_refresh.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from inventree.api import InvenTreeAPI
from inventree.company import Company, SupplierPart, SupplierPriceBreak
from inventree.part import Part

from inventree_sync.client import (
    _add_price_breaks,
    upload_image_from_url,
    upload_parameters,
)
from inventree_sync.fetchers import LCSCFetcher, MouserFetcher
from inventree_sync.part_manager import _fetch_and_merge

log = logging.getLogger(__name__)


def collect_parts_to_refresh(api: InvenTreeAPI) -> dict[int, dict[str, list[str]]]:
    """Return {part_pk: {'lcsc': [skus...], 'mouser': [skus...]}} for every Part
    that has at least one SupplierPart at LCSC or Mouser."""
    relevant: dict[int, str] = {}  # company_pk -> "lcsc" | "mouser"
    for c in Company.list(api, is_supplier=True):
        name_lower = (c.name or "").lower()
        if "lcsc" in name_lower:
            relevant[c.pk] = "lcsc"
        elif "mouser" in name_lower:
            relevant[c.pk] = "mouser"

    parts_to_skus: dict[int, dict[str, list[str]]] = defaultdict(
        lambda: {"lcsc": [], "mouser": []}
    )
    for company_pk, key in relevant.items():
        for sp in SupplierPart.list(api, supplier=company_pk):
            parts_to_skus[int(sp.part)][key].append(sp.SKU)
    return dict(parts_to_skus)


def refresh_part(
    api: InvenTreeAPI,
    part_pk: int,
    lcsc_skus: list[str],
    mouser_skus: list[str],
    lcsc_fetcher: LCSCFetcher,
    mouser_fetcher: MouserFetcher,
) -> bool:
    """Refresh one Part. Returns True on success, False on no-supplier-data."""
    part = Part(api, pk=part_pk)
    primary_lcsc = lcsc_skus[0] if lcsc_skus else ""
    primary_mouser = mouser_skus[0] if mouser_skus else ""

    part_data = _fetch_and_merge(
        lcsc_fetcher, mouser_fetcher, primary_lcsc, primary_mouser
    )
    if part_data is None:
        log.warning(
            "No supplier data for pk=%s (LCSC=%s, Mouser=%s)",
            part_pk, lcsc_skus, mouser_skus,
        )
        return False

    if part_data.image_url:
        upload_image_from_url(part, part_data.image_url)

    new_link = part_data.datasheet_url or ""
    current_link = getattr(part, "link", "") or part._data.get("link") or ""
    if new_link and new_link != current_link:
        try:
            part.save({"link": new_link})
        except Exception as exc:
            log.warning("Failed to update Part.link pk=%s: %s", part_pk, exc)

    current_desc = (getattr(part, "description", "") or "").strip()
    if not current_desc and part_data.description:
        try:
            part.save({"description": part_data.description})
        except Exception as exc:
            log.warning("Failed to update Part.description pk=%s: %s", part_pk, exc)

    if part_data.parameters:
        upload_parameters(api, part, part_data.parameters)

    if part_data.price_breaks:
        # Mirror the LCSC-primary invariant from client.create_part_in_inventree:
        # _fetch_and_merge produces an LCSC-primary price_breaks dict (Mouser
        # only fills gaps when LCSC didn't provide). Skip Mouser SupplierParts
        # entirely when this Part has any LCSC SKUs — the LCSC-primary breaks
        # would overwrite real Mouser prices. Only apply to Mouser when no
        # LCSC SKUs are present at all.
        apply_to_mouser = not lcsc_skus
        for sp in SupplierPart.list(api, part=part_pk):
            # Resolve supplier name. SupplierPart.list often omits
            # supplier_detail — fall back to fetching the Company by pk.
            # Without this fallback, sp_is_mouser would default to False and
            # Mouser SupplierParts would silently get LCSC-primary prices
            # applied (the exact regression this block exists to prevent).
            sp_supplier_name = (sp._data.get("supplier_detail") or {}).get("name", "")
            if not sp_supplier_name:
                try:
                    sp_supplier_name = Company(api, pk=int(sp.supplier)).name or ""
                except Exception:
                    sp_supplier_name = ""
            sp_supplier_lower = sp_supplier_name.lower()
            sp_is_lcsc = "lcsc" in sp_supplier_lower
            sp_is_mouser = "mouser" in sp_supplier_lower
            # Skip non-LCSC/Mouser suppliers entirely (e.g. DigiKey, manual
            # suppliers). This script only manages LCSC/Mouser data; touching
            # other suppliers' price breaks would silently wipe their real
            # prices with the LCSC-primary merged set.
            if not (sp_is_lcsc or sp_is_mouser):
                continue
            if sp_is_mouser and not apply_to_mouser:
                continue  # preserve real Mouser prices

            # Clear-and-readd: only re-add if the delete actually completed,
            # otherwise we'd stack new breaks on top of stale ones and
            # accumulate duplicates across refresh runs.
            cleared = True
            try:
                for pb in SupplierPriceBreak.list(api, part=sp.pk):
                    pb.delete()
            except Exception as exc:
                log.warning("Failed to clear price breaks SupplierPart pk=%s: %s",
                            sp.pk, exc)
                cleared = False
            if cleared:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)

    log.info("Refreshed pk=%s", part_pk)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if not os.environ.get("INVENTREE_API_HOST"):
        log.error("INVENTREE_API_HOST not set")
        return 2

    api = InvenTreeAPI()
    parts_to_skus = collect_parts_to_refresh(api)
    log.info("Found %d parts to refresh", len(parts_to_skus))

    lcsc_fetcher = LCSCFetcher()
    mouser_fetcher = MouserFetcher()

    refreshed = skipped = errors = 0
    for part_pk, sku_dict in parts_to_skus.items():
        try:
            ok = refresh_part(
                api, part_pk,
                sku_dict["lcsc"], sku_dict["mouser"],
                lcsc_fetcher, mouser_fetcher,
            )
            if ok:
                refreshed += 1
            else:
                skipped += 1
        except Exception as exc:
            log.error("Failed to refresh pk=%s: %s", part_pk, exc)
            errors += 1

    log.info(
        "Refresh complete: %d refreshed, %d skipped, %d errors",
        refreshed, skipped, errors,
    )
    # Total-failure signal (every part errored) → exit 1 so a future
    # Slack/Discord notifier can hook onto it. Partial failures (some
    # parts errored, some refreshed) still return 0 — those are noise,
    # not blockers, and continue-on-error keeps the cron job green.
    if errors > 0 and refreshed == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
