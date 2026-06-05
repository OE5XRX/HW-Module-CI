"""
categories.py – KiCad → InvenTree category mapping and part-name generation.
"""

import logging
import re
from pathlib import Path
from typing import Optional

import yaml
from inventree.api import InvenTreeAPI
from inventree.part import PartCategory

from .models import PartData

logger = logging.getLogger(__name__)

# Path to the built-in category map shipped with the package.
_DEFAULT_CATEGORIES_FILE = Path(__file__).parent / "default_categories.yaml"

# KiCad symbol names that receive an automatic package-level sub-category.
_PACKAGE_SUBCATEGORY_CAPS = frozenset({"C", "C_Small"})
_PACKAGE_SUBCATEGORY_RESISTORS = frozenset({"R", "R_Small", "R_Network", "RN"})


# ---------------------------------------------------------------------------
# Category map loading
# ---------------------------------------------------------------------------

def load_category_map(path: Optional[str] = None) -> dict[str, tuple[str, ...]]:
    """Load a KiCad symbol → InvenTree category map from a YAML file.

    Each YAML key is a KiCad symbol name; its value must be a list of strings
    that form the InvenTree category hierarchy (top-level → sub-category).

    If *path* is None the built-in ``default_categories.yaml`` is used.

    Example YAML entry::

        R: [Resistors, Surface Mount]
        Crystal: [Crystals and Oscillators, Crystals]

    Raises ``SystemExit`` with a descriptive message when the file cannot be
    read or contains an invalid entry.
    """
    file_path = Path(path) if path else _DEFAULT_CATEGORIES_FILE
    try:
        with open(file_path) as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.error("Category map file not found: %s", file_path)
        raise SystemExit(f"ERROR: category map file not found: {file_path}")
    except yaml.YAMLError as exc:
        logger.error("Failed to parse category map %s: %s", file_path, exc)
        raise SystemExit(f"ERROR: failed to parse YAML in {file_path}: {exc}")

    result: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            raise SystemExit(
                f"ERROR: invalid entry in {file_path}: key '{key}' must map to "
                f"a list of strings, got {type(value).__name__!r}"
            )
        result[str(key)] = tuple(value)
    return result


def extract_package(footprint: str) -> str:
    """
    Extract a short package code from a KiCad footprint string.
    'C_0805_2012Metric' → '0805', 'SOT-23' → 'SOT-23', 'SOIC-8_3.9x...' → 'SOIC-8'
    """
    m = re.match(r"(?:C|R|L)_(\w+?)_", footprint)
    if m:
        return m.group(1)
    return footprint.split("_")[0]


# Unicode codepoints we normalize away from value strings:
#   Ω (U+03A9) GREEK CAPITAL LETTER OMEGA  → KiCad uses this for ohms
#   Ω (U+2126) OHM SIGN                    → some libraries use this instead
#   µ (U+00B5) MICRO SIGN                  → micro (e.g. 4.7µF)
_OMEGA_CHARS = ("Ω", "Ω")
_MICRO_CHAR = "µ"

# Known unit/SI-prefix tokens used in component values. Order is purely
# stylistic — the \b boundary in the regex below prevents partial matches
# (e.g. "k" cannot half-match "kHz" because the regex requires \b after
# the unit token).
_UNIT_TOKENS = (
    "mHz", "MHz", "kHz", "Hz",
    "mA", "A", "mV", "V", "W",
    "uF", "nF", "pF", "F",
    "uH", "nH", "mH", "H",
    "m", "k", "M",      # bare SI prefixes used for resistors (10k, 1M, 470m)
)

# KiCad-Symbol-Präfixe, deren kicad_value bewusst generisch ist (typisch:
# kicad_value == kicad_part == "Conn_02x10_Row_Letter_First" — also der reine
# Symbol-Name statt eines Bauteil-Identifikators). Bei einem Real-Sync nutzt
# generate_part_name für diese Klasse den MPN aus PartData als Part-Name,
# wenn verfügbar; sonst fällt es auf den generischen Symbol-Namen zurück.
#
# Hintergrund: zwei physisch verschiedene Connectors (z.B. Stiftleiste vs
# Buchsenleiste mit derselben Pin-Belegung) teilen denselben KiCad-Symbol-
# Namen. Ohne diese Disambiguierung würde der name-based Lookup-Fallback in
# part_manager.ensure_parts_exist sie zu einem InvenTree-Part collapse'n —
# Stand des Bugs, der die PR-6 motiviert hat.
#
# Erweitern nach Bedarf: neue Präfix-Familie ist eine 1-Zeilen-Änderung hier.
# Bewusst NICHT in default_categories.yaml, weil es eine Symbol-Library-
# Konvention ist, kein Bauteil-Attribut.
_GENERIC_SYMBOL_PREFIXES = (
    "Conn_",
    "Screw_Terminal_",
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

    # 2. Capital K (kilo) → lowercase k. Requires a digit on the left and a
    #    word-boundary on the right. The \s* lets us handle "10 K" too.
    #    Note: this regex does NOT protect IC part numbers like "BAT54K" —
    #    protection comes from the call-site gating in generate_part_name,
    #    which only invokes _normalize_value for R/C/L/CP/XTAL component
    #    types where the kicad_value is a numeric-with-unit token.
    out = re.sub(r"(\d)\s*K\b", r"\1k", out)

    # 3. Collapse whitespace between digits and a known unit/prefix token.
    #    Longest-first to avoid partial matches.
    for tok in _UNIT_TOKENS:
        # \b on the right of `tok` ensures we don't eat "kg" when matching "k".
        out = re.sub(rf"(\d)\s+({re.escape(tok)})\b", r"\1\2", out)

    out = out.strip()
    return out


def generate_part_name(
    kicad_part: str,
    kicad_value: str,
    footprint: str,
    part_data: Optional[PartData] = None,
) -> str:
    """
    Generate a human-readable InvenTree part name from KiCad fields.

    For structured passive symbols (R, C, C_Polarized, L, L_Iron, Crystal)
    a value-with-package convention is used: ``R 10k 0805``, ``C 100nF 0805``,
    ``XTAL 8MHz/20pF``. Value normalization (``_normalize_value``) absorbs
    Schaltplan-side inconsistencies (10K vs 10k, kΩ vs k, …).

    For everything else — generic KiCad connector symbols
    (``Conn_*``, ``Screw_Terminal_*``) AND real MPN-style component names
    (STM32U575CITx, INA226, USBLC6-2SC6) — the ``kicad_value`` is normally
    passed through as the Part name. When *part_data* is provided AND the
    ``kicad_part`` starts with one of the ``_GENERIC_SYMBOL_PREFIXES`` AND
    ``part_data.mpn`` is set, the MPN replaces the generic ``kicad_value``.
    This prevents physically-distinct connectors that share a KiCad symbol
    name from collapsing to a single InvenTree Part via the name-based
    fallback in ``part_manager.ensure_parts_exist``.

    Real MPN-style symbol names (STM32U575CITx) keep their ``kicad_value``
    even when *part_data.mpn* differs — the schematic-side family-level
    identifier wins over the supplier-side variant suffix.

    Examples:
      R, '10K', 'R_0805_2012Metric'                       → 'R 10k 0805'
      C, '100 nF', 'C_0805_2012Metric'                    → 'C 100nF 0805'
      C_Polarized, '100u / 25V', ...                       → 'CP 100u/25V'
      Crystal, '8MHz / 20pF', ...                          → 'XTAL 8MHz/20pF'
      Conn_02x10_..., ..., PartData(mpn='PCN10-20P-2.54DS')→ 'PCN10-20P-2.54DS'
      Conn_02x10_..., ..., None                            → 'Conn_02x10_...'
      STM32U575CITx, 'STM32U575CITx', ..., PartData(mpn='STM32U575CIT6')
                                                           → 'STM32U575CITx'
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
        # Generic-Symbol-Klassen: MPN aus part_data nutzen wenn verfügbar.
        # Schützt vor Name-Kollisionen bei physisch verschiedenen Bauteilen
        # die ein generisches KiCad-Symbol teilen (Conn_02x10_..., Conn_Coaxial,
        # Screw_Terminal_..., ...).
        # Strip-first-then-check: ein whitespace-only MPN ("   ") darf nicht zum
        # Empty-String-Part-Name werden — der ginge sonst still durch
        # find_part_by_name (das None auf "" zurückgibt) bis in Part.create
        # mit name="".
        if (part_data is not None
                and kicad_part.startswith(_GENERIC_SYMBOL_PREFIXES)):
            mpn_stripped = (part_data.mpn or "").strip()
            if mpn_stripped:
                return mpn_stripped
        return val


def get_or_create_category(api: InvenTreeAPI, path_tuple: tuple) -> Optional[PartCategory]:
    """
    Walk the category hierarchy, creating any levels that don't yet exist.
    Returns the leaf PartCategory.
    """
    parent = None
    category = None
    for name in path_tuple:
        search_kwargs = {"name": name}
        if parent:
            search_kwargs["parent"] = parent.pk
        try:
            cats = PartCategory.list(api, **search_kwargs)
        except Exception as exc:
            logger.error("Category list failed for '%s': %s", name, exc)
            return None

        if cats:
            category = cats[0]
        else:
            data = {"name": name, "description": name}
            if parent:
                data["parent"] = parent.pk
            try:
                category = PartCategory.create(api, data)
                logger.info("Created category '%s'", name)
            except Exception as exc:
                logger.error("Category create failed for '%s': %s", name, exc)
                return None
        parent = category
    return category


def resolve_part_category(
    api: InvenTreeAPI,
    kicad_part: str,
    part_data: PartData,
    footprint: str,
    category_map: Optional[dict[str, tuple[str, ...]]] = None,
) -> Optional[PartCategory]:
    """Return the InvenTree PartCategory for a part, creating it if necessary.

    *category_map* defaults to the built-in map loaded from
    ``default_categories.yaml`` when not provided.
    """
    if category_map is None:
        category_map = load_category_map()

    path = category_map.get(kicad_part)
    if path:
        pkg = extract_package(footprint) if footprint else ""
        # Ceramic caps and resistors get a package-level sub-category.
        if kicad_part in _PACKAGE_SUBCATEGORY_CAPS and pkg:
            path = path + (pkg,)
        elif kicad_part in _PACKAGE_SUBCATEGORY_RESISTORS and pkg:
            path = path + (pkg,)
        return get_or_create_category(api, path)

    # Symbol not in the map – warn so the user can extend the YAML file
    logger.warning(
        "KiCad symbol %r not found in category map; "
        "add it to your categories YAML to assign a specific category.",
        kicad_part,
    )

    # Supplier-provided category path as a fallback
    if part_data and part_data.category_path:
        logger.debug("Using supplier-provided category for %r: %s", kicad_part, part_data.category_path)
        return get_or_create_category(api, tuple(part_data.category_path))

    logger.debug("Falling back to 'Miscellaneous' for %r", kicad_part)
    return get_or_create_category(api, ("Miscellaneous",))
