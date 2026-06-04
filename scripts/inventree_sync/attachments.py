"""attachments.py — Auto-discover KiBot outputs and attach to InvenTree Parts.

Public entry point: ``attach_kibot_outputs(api, pcb, assembly, stencil,
output_dir)``.

Mapping table at module level pairs a glob-pattern with a target-Part
(``pcb``/``assembly``/``stencil``) and a comment string.  Files matching
known image-file patterns are skipped because they are already in use
as ``Part.image`` (set by ``bom_export.py``-CLI's ``--*_image`` args).

Idempotent: before uploading, the function lists each target Part's
existing attachments and skips files whose basename is already present.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from inventree.api import InvenTreeAPI
from inventree.part import Part

logger = logging.getLogger(__name__)


# (glob_pattern, target_kwarg, comment)
# target_kwarg ∈ {"pcb", "assembly", "stencil"} — the relevant Part kwarg.
# Glob is relative to the *output_dir* passed to ``attach_kibot_outputs``.
_KIBOT_OUTPUT_MAPPING: list[tuple[str, str, str]] = [
    ("*.step",                 "pcb",      "3D STEP model"),
    ("*-3D_top.png",           "pcb",      "3D render (top, no components)"),
    ("*-3D_bottom.png",        "pcb",      "3D render (bottom)"),
    ("*-stencil_top.svg",      "stencil",  "Stencil paste layer (SVG)"),
    ("Fabrication/*.zip",      "stencil",  "JLCPCB stencil spec"),
    ("*-schematic.pdf",        "assembly", "Schematic"),
    ("*-bom.html",             "assembly", "BOM (static HTML)"),
    ("*-bom.csv",              "assembly", "BOM (CSV)"),
    ("*-ibom.html",            "assembly", "Interactive BOM"),
]


def attach_kibot_outputs(
    api: InvenTreeAPI,
    pcb: Part,
    assembly: Part,
    stencil: Part,
    output_dir: str | Path,
) -> None:
    """Auto-discover KiBot outputs in *output_dir* and attach to Parts.

    Idempotent: any file whose basename is already attached to its
    target Part is skipped.  Files that would duplicate ``Part.image``
    (``-3D_top-with``, ``-3D_top-without``, ``-stencil_top.png``) are
    not in the mapping table, so they are implicitly excluded.

    Returns None.  Errors per-file are logged and skipped so a single
    bad file can't break the whole sync.
    """
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        logger.warning(
            "attach_kibot_outputs: output_dir %s does not exist; skipping",
            output_dir)
        return

    targets = {"pcb": pcb, "assembly": assembly, "stencil": stencil}
    uploaded = 0
    skipped = 0
    unmatched_patterns = 0

    # Cache existing-attachment-filenames per target so we don't refetch
    # for each matching file.
    existing_cache: dict[str, set[str]] = {}

    def _existing_for(target_kwarg: str) -> set[str]:
        if target_kwarg in existing_cache:
            return existing_cache[target_kwarg]
        target = targets[target_kwarg]
        try:
            # `filename` is a derived property on current InvenTree versions.
            # Fall back to basename of `attachment` so a future server-side
            # rename doesn't silently turn re-runs into re-uploads.
            names = {
                getattr(a, "filename", None)
                or os.path.basename(getattr(a, "attachment", "") or "")
                for a in target.getAttachments()
            }
            names.discard("")
        except Exception as exc:
            logger.warning(
                "Could not list attachments for %s (pk=%s): %s",
                target_kwarg, target.pk, exc)
            names = set()
        existing_cache[target_kwarg] = names
        return names

    for pattern, target_kwarg, comment in _KIBOT_OUTPUT_MAPPING:
        matches = sorted(output_dir.glob(pattern))
        if not matches:
            unmatched_patterns += 1
            logger.debug(
                "Pattern %r matched no files in %s", pattern, output_dir)
            continue
        target = targets[target_kwarg]
        existing = _existing_for(target_kwarg)
        for match in matches:
            basename = match.name
            if basename in existing:
                logger.info(
                    "Attachment %r already on %s pk=%s, skipping",
                    basename, target_kwarg, target.pk)
                skipped += 1
                continue
            try:
                target.uploadAttachment(str(match), comment=comment)
                existing.add(basename)
                logger.info(
                    "Uploaded attachment %r to %s pk=%s (%s)",
                    basename, target_kwarg, target.pk, comment)
                uploaded += 1
            except Exception as exc:
                logger.warning(
                    "Failed to upload %s to %s pk=%s: %s",
                    match, target_kwarg, target.pk, exc)

    logger.info(
        "Attachments summary: %d uploaded, %d skipped (already present), "
        "%d patterns with no match", uploaded, skipped, unmatched_patterns)
