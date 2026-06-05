"""Pure-Python unit tests for generate_part_name's part_data branch.

PR-6: generate_part_name accepts an optional part_data argument. For
generic KiCad symbol prefixes (Conn_*, Screw_Terminal_*) the MPN replaces
the symbol-name as the InvenTree Part name when part_data is provided
and has a non-empty mpn. Structured RCL passives, real IC names, and
the no-part_data path are unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap sys.path so `inventree_sync` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.categories import generate_part_name
from inventree_sync.models import PartData


def _mpn(mpn: str) -> PartData:
    """Helper: minimal PartData with just an MPN set."""
    return PartData(mpn=mpn)


def test_R_unchanged_without_part_data():
    """Resistors stay structured — package suffix, value, no MPN injection."""
    assert generate_part_name("R", "10k", "R_0805_2012Metric") == "R 10k 0805"


def test_R_unchanged_with_part_data():
    """Even when part_data.mpn is set, R stays structured (MPN ignored)."""
    pd = _mpn("0805W8F1002T5E")
    assert generate_part_name("R", "10k", "R_0805_2012Metric", pd) == "R 10k 0805"


def test_C_unchanged_with_part_data():
    """Same for C — kicad_value+package wins over any supplier MPN."""
    pd = _mpn("CL21B104KBCNNNC")
    assert generate_part_name("C", "100nF", "C_0805_2012Metric", pd) == "C 100nF 0805"


def test_generic_connector_falls_back_when_no_part_data():
    """Dry-run case: no part_data → use kicad_value (KiCad-Symbol-Name)."""
    assert generate_part_name(
        "Conn_02x10_Row_Letter_First", "Conn_02x10_Row_Letter_First",
        "PCN10-20P-2.54DS",
    ) == "Conn_02x10_Row_Letter_First"


def test_generic_connector_uses_mpn_when_part_data_provided():
    """Real-sync case: part_data.mpn becomes the Part-Name."""
    pd = _mpn("PCN10-20P-2.54DS")
    assert generate_part_name(
        "Conn_02x10_Row_Letter_First", "Conn_02x10_Row_Letter_First",
        "PCN10-20P-2.54DS", pd,
    ) == "PCN10-20P-2.54DS"


def test_generic_connector_falls_back_when_part_data_has_empty_mpn():
    """If MPN is empty (fetch returned PartData without MPN), fall back."""
    pd = _mpn("")
    assert generate_part_name(
        "Conn_02x10_Row_Letter_First", "Conn_02x10_Row_Letter_First",
        "PCN10-20P-2.54DS", pd,
    ) == "Conn_02x10_Row_Letter_First"


def test_generic_connector_falls_back_when_mpn_is_whitespace_only():
    """Whitespace-only MPN must fall back, not return empty string.

    Without the strip-first-then-check guard, ``"   ".strip()`` would
    return ``""`` and silently propagate to ``find_part_by_name(api, "")``
    and eventually ``Part.create(name="")`` — a downstream failure mode
    far from this call site.
    """
    pd = _mpn("   ")
    assert generate_part_name(
        "Conn_02x10_Row_Letter_First", "Conn_02x10_Row_Letter_First",
        "PCN10-20P-2.54DS", pd,
    ) == "Conn_02x10_Row_Letter_First"


def test_screw_terminal_uses_mpn():
    """Screw_Terminal_ prefix also triggers MPN-based naming."""
    pd = _mpn("MKDS-1,5/2-5.08")
    assert generate_part_name(
        "Screw_Terminal_01x02", "Screw_Terminal_01x02",
        "TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal", pd,
    ) == "MKDS-1,5/2-5.08"


def test_mpn_is_stripped():
    """Leading/trailing whitespace in mpn is stripped before use."""
    pd = _mpn("  PCN10-20P-2.54DS  ")
    assert generate_part_name(
        "Conn_02x10_Row_Letter_First", "Conn_02x10_Row_Letter_First",
        "PCN10-20P-2.54DS", pd,
    ) == "PCN10-20P-2.54DS"


def test_non_generic_ic_still_uses_kicad_value():
    """For non-generic symbols (real MPN-style names), kicad_value wins
    even when part_data.mpn differs by variant suffix.

    Example: kicad_value=STM32U575CITx (KiCad symbol for the family),
    Mouser MPN=STM32U575CIT6 (specific variant). The kicad_value is the
    family-level identifier the schematic author chose; the supplier MPN
    is one of potentially many concrete variants.
    """
    pd = _mpn("STM32U575CIT6")
    assert generate_part_name(
        "STM32U575CITx", "STM32U575CITx", "LQFP-48_7x7mm_P0.5mm", pd,
    ) == "STM32U575CITx"
