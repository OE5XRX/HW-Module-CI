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
from typing import Optional

from inventree.api import InvenTreeAPI
from inventree.company import SupplierPart
from inventree.part import BomItem, Part, PartCategory, PartRelated

from inventree_sync import BomEntry, ensure_parts_exist
from inventree_sync.attachments import attach_kibot_outputs
from inventree_sync.categories import load_category_map
from inventree_sync.client import find_part_by_name_and_revision
from inventree_sync.cost_report import generate_cost_report
from inventree_sync.dry_run import DryRunReporter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Human-readable category names used for PCB and assembly parts.
PCB_CATEGORY_NAME      = "Printed-Circuit Boards"
ASSEMBLY_CATEGORY_NAME = "PCBA"
STENCIL_CATEGORY_NAME  = "SMT Stencil"


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
    # In dry-run mode, the print_report+exit happens up in main().
    # With collector, the summary+exit happens in main() too.


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
        "--output_dir",
        required=False,
        help=(
            "KiBot output directory.  When given, fabrication artifacts "
            "(STEP, 3D renders, schematic PDF, BOM HTML/CSV, iBOM, "
            "stencil files, JLCPCB-stencil ZIP) are auto-discovered and "
            "attached to the respective Parts.  Omit to skip attachments."
        ),
    )
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
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Simulate the sync flow without InvenTree side-effects. "
             "Prints a Would-CREATE/REUSE/SKIP/FAIL report; exit 1 on FAIL.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Connection settings are read from environment variables by InvenTreeAPI:
    #   INVENTREE_API_HOST  +  INVENTREE_API_TOKEN
    #   (or INVENTREE_API_USERNAME / INVENTREE_API_PASSWORD)
    api = InvenTreeAPI()
    reporter = DryRunReporter() if args.dry_run else None

    entries = load_bom(args.csv_file)

    # Load category map (custom file or built-in default)
    category_map = load_category_map(args.categories)

    if reporter is not None:
        # Dry-run path: record decisions, skip side-effecting operations
        # (Part.create / SupplierPart.create / BomItem.create / save / supplier
        # fetch). Read-only InvenTree lookups (find_part_by_name_and_revision,
        # BomItem.list, SupplierPart.list in match_supplier_parts) still run —
        # they're how we know whether something WOULD be CREATE vs REUSE.
        ensure_parts_exist(api, entries, category_map, reporter=reporter)
        match_supplier_parts(api, entries, reporter=reporter)

        pcb_existing      = find_part_by_name_and_revision(api, f"{args.name} PCB", args.version)
        if pcb_existing is not None:
            reporter.record("REUSE", "PCB", f"{args.name} PCB rev {args.version}",
                            f"existing pk={pcb_existing.pk}")
        else:
            reporter.record("CREATE", "PCB", f"{args.name} PCB rev {args.version}")

        assembly_existing = find_part_by_name_and_revision(api, f"{args.name} Module", args.version)
        if assembly_existing is not None:
            reporter.record("REUSE", "Assembly", f"{args.name} Module rev {args.version}",
                            f"existing pk={assembly_existing.pk}")
        else:
            reporter.record("CREATE", "Assembly", f"{args.name} Module rev {args.version}")

        stencil_existing  = find_part_by_name_and_revision(api, f"{args.name} SMT Stencil", args.version)
        if stencil_existing is not None:
            reporter.record("REUSE", "Stencil", f"{args.name} SMT Stencil rev {args.version}",
                            f"existing pk={stencil_existing.pk}")
        else:
            reporter.record("CREATE", "Stencil", f"{args.name} SMT Stencil rev {args.version}")

        # BomItem decisions: count from the reporter's own Parts records.
        # entry.inventree_part can't be relied on here because the dry-run
        # branches in ensure_parts_exist `continue` before appending — they
        # only record decisions. Use the reporter's Parts CREATE+REUSE count
        # as the closest proxy for "entries that would yield a BomItem".
        parts_resolved = sum(
            1 for r in reporter.records
            if r.category == "Parts" and r.action in ("CREATE", "REUSE")
        )
        if assembly_existing is None:
            reporter.record(
                "CREATE", "BomItem",
                f"{parts_resolved + 1} items (PCB + resolved entries)",
            )
        else:
            # Existing assembly: would create or skip depending on overlap
            # with existing BomItems. We can't predict the exact split
            # without simulating populate_bom against the real Part PKs
            # (which we don't have for the CREATE-branch parts in dry-run).
            reporter.record(
                "CREATE", "BomItem",
                f"up to {parts_resolved + 1} items (PCB + resolved entries)",
                "exact create/skip split known only at sync time",
            )

        reporter.print_report(title=f"bom_export {args.name} v{args.version}")
        if reporter.has_failures():
            sys.exit(1)
        return  # End of dry-run path

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


if __name__ == "__main__":
    main()
