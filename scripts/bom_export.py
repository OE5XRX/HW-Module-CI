#!/usr/bin/env python3
"""
bom_export.py – Export a KiCad BOM CSV to InvenTree.

Creates a PCB part and an assembly part in InvenTree, then populates the BOM
with all components from the CSV.  Any parts not yet present in InvenTree are
created automatically (via LCSC / Mouser) before the BOM is assembled.

Required environment variables:
    INVENTREE_API_HOST     – InvenTree server URL
    INVENTREE_API_TOKEN    – API token  (or use USERNAME + PASSWORD instead)
    MOUSER_API_KEY         – Mouser API v2 key
"""

import argparse
import csv
import logging
import sys

from inventree.api import InvenTreeAPI
from inventree.company import SupplierPart
from inventree.part import BomItem, Part, PartCategory, PartRelated

from inventree_sync import BomEntry, ensure_parts_exist
from inventree_sync.categories import load_category_map
from inventree_sync.client import find_part_by_name_and_revision

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Human-readable category names used for PCB and assembly parts.
PCB_CATEGORY_NAME      = "Printed-Circuit Boards"
ASSEMBLY_CATEGORY_NAME = "PCBA"
STENCIL_CATEGORY_NAME  = "SMT Stencil"


# ---------------------------------------------------------------------------
# Category lookup
# ---------------------------------------------------------------------------

def get_category_by_name(api: InvenTreeAPI, name: str) -> PartCategory:
    """Return the PartCategory with the given name, or abort if not found."""
    matches = [c for c in PartCategory.list(api) if c.name == name]
    if not matches:
        log.error("InvenTree category %r not found. Create it first.", name)
        sys.exit(1)
    return matches[0]


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def _split_sku_field(field: str) -> list[str]:
    """Split a comma-separated SKU CSV cell into a clean list.

    Handles human-edited CSV quirks: leading/trailing whitespace per token,
    trailing commas producing empty tokens, etc. Returns only non-empty
    stripped SKUs.
    """
    return [s.strip() for s in field.split(",") if s.strip()]


def load_bom(csv_path: str) -> list[BomEntry]:
    """Parse the KiCad BOM CSV and return a list of BomEntry objects."""
    entries: list[BomEntry] = []
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            entries.append(BomEntry(
                reference=row["References"],
                qty=int(row["Quantity Per PCB"]),
                kicad_part=row["Part"].strip(),
                kicad_value=row["Value"].strip(),
                kicad_footprint=row["Footprint"].strip(),
                lcsc=_split_sku_field(row["LCSC"]),
                mouser=_split_sku_field(row["MOUSER"]),
            ))
    log.info("Loaded %d BOM entries from %s", len(entries), csv_path)
    return entries


# ---------------------------------------------------------------------------
# Part matching
# ---------------------------------------------------------------------------

def match_supplier_parts(api: InvenTreeAPI, entries: list[BomEntry]) -> None:
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
    """
    # sorted for deterministic API call order — helpful for log diffing.
    all_skus = sorted({
        sku for entry in entries
        for sku in entry.lcsc + entry.mouser
        if sku
    })
    supplier_parts: list[SupplierPart] = []
    if all_skus:
        try:
            supplier_parts = list(SupplierPart.list(api, SKU__in=all_skus))
        except Exception as exc:
            log.warning(
                "SKU__in batch query raised (%s); will fall back to per-SKU",
                exc)

        if not supplier_parts:
            # Either filter unsupported (HTTP 400 swallowed by the client →
            # empty list, indistinguishable from "no matches") or genuinely
            # no SupplierParts on the server for any of these SKUs.  Probe
            # per-SKU to disambiguate and recover.
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
    if missing:
        for entry in missing:
            log.error("No InvenTree part found for %s (LCSC=%s, Mouser=%s)",
                      entry.reference, entry.lcsc, entry.mouser)
        sys.exit(1)


# ---------------------------------------------------------------------------
# PCB + assembly + stencil creation
# ---------------------------------------------------------------------------

def create_pcb_part(api: InvenTreeAPI, category: PartCategory, name: str, version: str, image: str | None) -> Part:
    full_name = f"{name} PCB"
    existing = find_part_by_name_and_revision(api, full_name, version)
    if existing is not None:
        log.info("Reusing existing PCB part '%s' rev %s (pk=%s)",
                 full_name, version, existing.pk)
        return existing

    part = Part.create(api, {
        "category": category.pk,
        "name": full_name,
        "revision": version,
        "component": True,
    })
    if image is not None:
        assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created PCB part '%s' rev %s (pk=%s)", full_name, version, part.pk)
    return part


def create_assembly_part(api: InvenTreeAPI, category: PartCategory, name: str, version: str, image: str | None) -> Part:
    full_name = f"{name} Module"
    existing = find_part_by_name_and_revision(api, full_name, version)
    if existing is not None:
        log.info("Reusing existing assembly part '%s' rev %s (pk=%s)",
                 full_name, version, existing.pk)
        return existing

    part = Part.create(api, {
        "category": category.pk,
        "name": full_name,
        "revision": version,
        "component": True,
        "assembly": True,
        "trackable": True,
    })
    if image is not None:
        assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created assembly part '%s' rev %s (pk=%s)", full_name, version, part.pk)
    return part


def create_stencil_part(
    api: InvenTreeAPI,
    category: PartCategory,
    name: str,
    version: str,
    image: str | None = None,
) -> Part:
    full_name = f"{name} SMT Stencil"
    existing = find_part_by_name_and_revision(api, full_name, version)
    if existing is not None:
        log.info("Reusing existing stencil part '%s' rev %s (pk=%s)",
                 full_name, version, existing.pk)
        return existing

    part = Part.create(api, {
        "category": category.pk,
        "name": full_name,
        "revision": version,
        "component": True,
    })
    if image is not None:
        assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created stencil part '%s' rev %s (pk=%s)", full_name, version, part.pk)
    return part


# ---------------------------------------------------------------------------
# BOM population
# ---------------------------------------------------------------------------

def populate_bom(
    api: InvenTreeAPI,
    assembly: Part,
    pcb: Part,
    entries: list[BomEntry],
) -> None:
    """Create BomItems on *assembly*: one for the PCB, one per BomEntry.

    Idempotent: when the same Assembly already has BomItems linking to
    the same sub-parts with the same reference designators, the existing
    items are kept and the new creation is skipped.  Lets the workflow
    be re-run safely without producing duplicate BomItems.
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a KiCad BOM CSV to an InvenTree assembly BOM."
    )
    parser.add_argument("--csv_file",        required=True,  help="Path to the KiCad BOM CSV")
    parser.add_argument("--name",            required=True,  help="Module name (e.g. HW-Module-FMTransceiver)")
    parser.add_argument("--version",         required=True,  help="Revision string (e.g. 0.99)")
    parser.add_argument("--pcb_image",       required=True,  help="PCB render image")
    parser.add_argument("--assembly_image",  required=True,  help="Assembly render image")
    parser.add_argument("--stencil_image",   required=False, help="Stencil paste-layer render (optional)")
    parser.add_argument(
        "--categories",
        required=False,
        metavar="YAML_FILE",
        help=(
            "Path to a YAML file mapping KiCad symbol names to InvenTree "
            "category hierarchies.  Defaults to the built-in "
            "default_categories.yaml shipped with the package."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Connection settings are read from environment variables by InvenTreeAPI:
    #   INVENTREE_API_HOST  +  INVENTREE_API_TOKEN
    #   (or INVENTREE_API_USERNAME / INVENTREE_API_PASSWORD)
    api = InvenTreeAPI()

    entries = load_bom(args.csv_file)

    # Load category map (custom file or built-in default)
    category_map = load_category_map(args.categories)

    # Create any parts that don't exist in InvenTree yet
    ensure_parts_exist(api, entries, category_map)

    # Match every BOM entry to its InvenTree part via supplier SKU
    match_supplier_parts(api, entries)

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


if __name__ == "__main__":
    main()
