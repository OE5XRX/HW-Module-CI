# PR-6 MPN-Name for Generic Symbols — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or just implement inline since this is a small PR.

**Goal:** Verhindert dass physisch verschiedene Bauteile mit demselben generischen
KiCad-Symbol (`Conn_*`, `Screw_Terminal_*`) zu einem InvenTree-Part collapse'n,
indem `generate_part_name` für diese Symbol-Klassen den MPN aus part_data nutzt
statt den generischen `kicad_value` durchzureichen.

**Tech Stack:** Python 3.x, kein neues Dependency. Pure-function-Erweiterung.

---

## File Structure

**Modified:**
- `scripts/inventree_sync/categories.py` — `_GENERIC_SYMBOL_PREFIXES` Konstante;
  `generate_part_name` bekommt `part_data: Optional[PartData] = None` Parameter
  und MPN-Branch im else-Pfad.
- `scripts/inventree_sync/part_manager.py` — `generate_part_name`-Aufruf in
  `ensure_parts_exist` bekommt `part_data=part_data`.
- `scripts/e2e_revision_handling.py` — neuer Test `test_generic_connector_mpn_disambiguation`.

**Created:**
- `scripts/tests/test_generate_part_name.py` — 8 pytest cases.

**Untouched:**
- `scripts/inventree_sync/client.py` (Lookup-Kette unverändert)
- `scripts/inventree_sync/fetchers.py`
- `scripts/inventree_sync/models.py` (PartData hat schon `mpn` field)
- `scripts/bom_export.py`

---

## Task 1: Pytest cases zuerst (red)

**Files:** `scripts/tests/test_generate_part_name.py` (NEU)

- [ ] **Step 1.1: Pytest-File mit allen 8 Cases anlegen**

```python
"""Pure-Python unit tests for generate_part_name with the part_data branch."""

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
    """Same for C."""
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


def test_screw_terminal_uses_mpn():
    """Screw_Terminal_ prefix also triggers MPN-based naming."""
    pd = _mpn("MKDS-1,5/2-5.08")
    assert generate_part_name(
        "Screw_Terminal_01x02", "Screw_Terminal_01x02",
        "TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal", pd,
    ) == "MKDS-1,5/2-5.08"


def test_mpn_is_stripped():
    """Leading/trailing whitespace in mpn is stripped."""
    pd = _mpn("  PCN10-20P-2.54DS  ")
    assert generate_part_name(
        "Conn_02x10_Row_Letter_First", "Conn_02x10_Row_Letter_First",
        "PCN10-20P-2.54DS", pd,
    ) == "PCN10-20P-2.54DS"


def test_non_generic_ic_still_uses_kicad_value():
    """STM32 and other ICs: kicad_value IS the MPN-family name. Don't override
    with part_data.mpn even if the fetched MPN differs by variant suffix
    (STM32U575CITx vs STM32U575CIT6 — kicad_value should win)."""
    pd = _mpn("STM32U575CIT6")
    assert generate_part_name(
        "STM32U575CITx", "STM32U575CITx", "LQFP-48_7x7mm_P0.5mm", pd,
    ) == "STM32U575CITx"
```

- [ ] **Step 1.2: Confirm tests fail (red)**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 -m pytest scripts/tests/test_generate_part_name.py -v
```

Expected: all 8 tests fail with TypeError (`generate_part_name` heute hat nur 3 Parameter, der 4. wird hineingegeben).

---

## Task 2: `generate_part_name` erweitern

**Files:** `scripts/inventree_sync/categories.py`

- [ ] **Step 2.1: Konstante + Signatur + Code**

Edit `scripts/inventree_sync/categories.py`. Imports: `PartData` brauchen wir nur
als Type-Hint → `from typing import TYPE_CHECKING` + lazy.

Konstante direkt nach `_UNIT_TOKENS` definieren:

```python
# KiCad-Symbol-Präfixe, deren kicad_value bewusst generisch ist
# (typischer Connector: kicad_value == kicad_part == "Conn_02x10_Row_Letter_First",
# also identisch zum Symbol-Namen statt eines Bauteil-Identifikators). Für
# diese Klasse nutzt generate_part_name den MPN aus PartData als Part-Name,
# wenn verfügbar — sonst Fallback auf den generischen Namen.
#
# Erweitern nach Bedarf: jede neue Präfix-Familie ist eine 1-Zeilen-Änderung
# hier. NICHT in der YAML-Category-Map, weil das eine Symbol-Library-Konvention
# ist, kein Bauteil-Attribut.
_GENERIC_SYMBOL_PREFIXES = (
    "Conn_",
    "Screw_Terminal_",
)
```

`generate_part_name`-Signatur und Body:

```python
def generate_part_name(
    kicad_part: str,
    kicad_value: str,
    footprint: str,
    part_data: Optional["PartData"] = None,
) -> str:
    """
    Generate a human-readable InvenTree part name from KiCad fields.

    For structured passive symbols (R, C, C_Polarized, L, L_Iron, Crystal),
    a value-with-package convention is used: "R 10k 0805", "C 100nF 0805",
    "XTAL 8MHz/20pF".  Value normalization (_normalize_value) is applied
    to absorb Schaltplan-side inconsistencies (10K vs 10k, kΩ vs k, …).

    For everything else — generic KiCad connector symbols
    (Conn_*, Screw_Terminal_*) AND real MPN-style component names
    (STM32U575CITx, INA226) — the kicad_value is passed through as the
    Part name.  When *part_data* is provided AND the kicad_part starts
    with one of the _GENERIC_SYMBOL_PREFIXES AND part_data.mpn is set,
    the MPN replaces the generic kicad_value — this is what saves us
    from physically-distinct connectors collapsing to one Part via
    find_part_by_name during sync.

    Examples:
      R, '10K', 'R_0805_2012Metric'         → 'R 10k 0805'
      C, '100 nF', 'C_0805_2012Metric'      → 'C 100nF 0805'
      Conn_02x10_..., 'Conn_02x10_...', ..., PartData(mpn="PCN10-20P-2.54DS")
                                            → 'PCN10-20P-2.54DS'
      Conn_02x10_..., 'Conn_02x10_...', ..., None
                                            → 'Conn_02x10_Row_Letter_First'  (fallback)
      STM32U575CITx, 'STM32U575CITx', ..., PartData(mpn="STM32U575CIT6")
                                            → 'STM32U575CITx'  (kicad_value wins for non-generic)
    """
    # Collapse spaces around '/' and consecutive spaces (compound values).
    val = re.sub(r"\s*/\s*", "/", kicad_value.strip())
    val = re.sub(r"\s+", " ", val).strip()

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
        # die ein generisches KiCad-Symbol teilen (Conn_02x10_..., 
        # Conn_Coaxial, Screw_Terminal_..., ...).
        if (part_data is not None and part_data.mpn
                and kicad_part.startswith(_GENERIC_SYMBOL_PREFIXES)):
            return part_data.mpn.strip()
        return val
```

Imports anpassen (oben in der Datei):

```python
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .models import PartData
```

- [ ] **Step 2.2: Pytest grün?**

```bash
python3 -m pytest scripts/tests/test_generate_part_name.py -v
```

Expected: 8/8 pass.

- [ ] **Step 2.3: Full pytest suite grün?**

```bash
python3 -m pytest scripts/tests/ -v
```

Expected: 105 + 8 = 113 pass.

---

## Task 3: Aufruf in `part_manager` weiterreichen

**Files:** `scripts/inventree_sync/part_manager.py`

- [ ] **Step 3.1: `part_data` an `generate_part_name` durchreichen**

Finde in `ensure_parts_exist` den Aufruf:

```python
name = generate_part_name(kicad_part, kicad_value, kicad_footprint)
```

Ersetze durch:

```python
name = generate_part_name(
    kicad_part, kicad_value, kicad_footprint, part_data=part_data,
)
```

(Es gibt genau einen solchen Aufruf im CREATE-Pfad. Der Dry-Run-Pfad
in `ensure_parts_exist` ruft `generate_part_name` ohne part_data,
das bleibt unverändert.)

- [ ] **Step 3.2: Smoke-test**

```bash
python3 -c "
from scripts.inventree_sync.part_manager import ensure_parts_exist
print('imports OK')
"
```

Expected: `imports OK`.

---

## Task 4: E2E test

**Files:** `scripts/e2e_revision_handling.py`

- [ ] **Step 4.1: Test hinzufügen vor Entry-Point**

Direkt nach `test_minimum_stock_set_and_preserved` einfügen:

```python
def test_generic_connector_mpn_disambiguation(api: InvenTreeAPI) -> None:
    """generate_part_name uses MPN from part_data for generic connector symbols.

    Pure-function test: PR-6 inserts an MPN-from-part_data path into
    generate_part_name's else-branch for Conn_*/Screw_Terminal_* symbols.
    Two physically-distinct connectors that share the KiCad symbol
    Conn_02x10_Row_Letter_First (different physical part: Stiftleiste straight
    vs Buchsenleiste) must produce different InvenTree Part names so the
    find_part_by_name fallback in ensure_parts_exist doesn't collapse them
    into a single Part.
    """
    from inventree_sync.categories import generate_part_name
    from inventree_sync.models import PartData

    sym = "Conn_02x10_Row_Letter_First"
    fp_a = "PCN10-20P-2.54DS"
    fp_b = "PCN10C-20S-2.54DS"

    # Without part_data (dry-run path): both collapse to symbol name.
    no_pd_a = generate_part_name(sym, sym, fp_a)
    no_pd_b = generate_part_name(sym, sym, fp_b)
    assert no_pd_a == no_pd_b == sym, (
        f"dry-run fallback should keep generic symbol name; got {no_pd_a!r}, {no_pd_b!r}")

    # With part_data (real-sync): MPNs disambiguate.
    pd_a = PartData(mpn="PCN10-20P-2.54DS")
    pd_b = PartData(mpn="PCN10C-20S-2.54DS")
    real_a = generate_part_name(sym, sym, fp_a, part_data=pd_a)
    real_b = generate_part_name(sym, sym, fp_b, part_data=pd_b)
    assert real_a == "PCN10-20P-2.54DS", f"expected MPN-A, got {real_a!r}"
    assert real_b == "PCN10C-20S-2.54DS", f"expected MPN-B, got {real_b!r}"
    assert real_a != real_b, "two distinct MPNs must yield distinct Part names"

    # Non-generic IC: kicad_value still wins even with PartData.mpn.
    pd_ic = PartData(mpn="STM32U575CIT6")
    ic_name = generate_part_name(
        "STM32U575CITx", "STM32U575CITx", "LQFP-48_7x7mm_P0.5mm", part_data=pd_ic,
    )
    assert ic_name == "STM32U575CITx", (
        f"IC name should be kicad_value (STM32U575CITx), not MPN; got {ic_name!r}")

    print(f"  PASS  generic_connector_mpn_disambiguation "
          f"({real_a!r} vs {real_b!r}, IC={ic_name!r})")
```

- [ ] **Step 4.2: Test in main() registrieren**

In `main()` das Test-Tuple um den neuen Test ergänzen:

```python
        for tc in (test_find_part_by_name_exact,
                   ...
                   test_minimum_stock_set_and_preserved,
                   test_generic_connector_mpn_disambiguation):
```

- [ ] **Step 4.3: E2E-Suite gegen real InvenTree laufen**

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
source ~/.inventree_test.env
python3 scripts/e2e_revision_handling.py
```

Expected: 17/17 tests pass.

---

## Task 5: Re-Verify Dry-Runs

- [ ] **Step 5.1: DeviceTester Dry-Run re-laufen**

```bash
source ~/.inventree_test.env
ART=/tmp/pr5-dryrun/DeviceTester-v1.0/production
python3 scripts/bom_export.py \
  --csv_file "$ART/DeviceTester-bom.csv" \
  --name "DeviceTester" --version "1.0" \
  --pcb_image "$ART/DeviceTester-3D_top-without.png" \
  --assembly_image "$ART/DeviceTester-3D_top-with.png" \
  --stencil_image "$ART/DeviceTester-stencil_top.png" \
  --output_dir "$ART" \
  --dry-run 2>&1 | grep -E "DRY-RUN|Would|Summary|EXIT"
```

Expected: identische Ausgabe zu vorher — Dry-Run zeigt weiter generische
Namen, weil Fetcher nicht läuft. Wichtig zu zeigen: kein Regression.

- [ ] **Step 5.2: FMTransceiver Dry-Run re-laufen**

Analog. Erwartet identische Ausgabe.

---

## Task 6: Commit, Push, PR, Review, Merge

- [ ] **Step 6.1: Commit**

```bash
git add scripts/inventree_sync/categories.py \
        scripts/inventree_sync/part_manager.py \
        scripts/tests/test_generate_part_name.py \
        scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): MPN-based Part name for generic connector symbols

generate_part_name now accepts an optional part_data argument; when the
kicad_part starts with a _GENERIC_SYMBOL_PREFIXES entry (Conn_, 
Screw_Terminal_) AND part_data.mpn is set, the MPN becomes the Part name
instead of the (generic, collision-prone) KiCad symbol value.

Fixes a sync bug discovered during pre-marathon dry-runs: J203 and J302
in DeviceTester v1.0 are physically distinct connectors (Stiftleiste vs
Buchsenleiste, different Mouser SKUs PCN10-20P-2.54DS vs PCN10C-20S-2.54DS)
that share the KiCad symbol Conn_02x10_Row_Letter_First. Without this
fix, the second SKU would be attached to the first Part via find_part_by_name
fallback — one Part with two physically incompatible supplier records.

R/C/L/Crystal/IC naming unchanged: their kicad_value either has the
structured form (R 10k 0805) or already IS the MPN-family name (STM32U575CITx).
Only the generic-symbol else-branch is augmented.

Dry-run behavior unchanged: ensure_parts_exist in the dry-run path calls
generate_part_name without part_data, so dry-run reports still show the
generic name. Real-sync gets the MPN.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6.2: Push + PR**

```bash
git push -u origin feat/pr6-mpn-name-for-generic-symbols
gh pr create --title "feat(inventree-sync): PR-6 MPN-based Part-Name for generic connector symbols" --body "$(cat <<'EOF'
## Summary
Fix für einen Sync-Bug der in den PR-5-Dry-Run-Tests gefunden wurde: physisch
verschiedene Bauteile mit demselben generischen KiCad-Symbol (Conn_*,
Screw_Terminal_*) würden in InvenTree zu einem einzigen Part collapse'n.

Lösung: generate_part_name bekommt optional part_data, nutzt für
_GENERIC_SYMBOL_PREFIXES den MPN aus PartData statt den generischen
kicad_value.

## Test plan
- [x] 8 pytest cases (test_generate_part_name.py)
- [x] 1 neuer E2E test (test_generic_connector_mpn_disambiguation)
- [x] Re-verified dry-runs gegen alle 5 Module — identisches Verhalten

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6.3: Copilot review loop + merge**
