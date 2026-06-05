#!/usr/bin/env python3
"""
e2e_revision_handling.py — E2E smoke test for PR-2 (Re-Activation Bug-Fixes).

Exercises against a real InvenTree server:
  - find_part_by_name_and_revision helper (Task 1)
  - find_part_by_name exact-filter (Task 1)
  - PCB/Stencil/Assembly silently-reuse (Task 2)
  - populate_bom idempotency (Task 3)
  - Multi-SKU SupplierPart anlage (Task 5)

Required env vars (same as e2e_image_upload.py):
    INVENTREE_API_HOST
    INVENTREE_API_TOKEN
    (or INVENTREE_API_USERNAME + INVENTREE_API_PASSWORD)

Each test creates throwaway Parts/Companies/SupplierParts with unique
timestamped names, asserts the expected behavior, and cleans up at the
end (deactivate + delete). Set KEEP_TEST_PARTS=1 to leave them for
inspection.

Usage:
    source ~/.inventree_test.env
    python3 scripts/e2e_revision_handling.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from inventree.api import InvenTreeAPI
from inventree.company import Company, SupplierPart
from inventree.base import Parameter, ParameterTemplate
from inventree.part import BomItem, Part, PartCategory

from inventree_sync.client import (
    find_part_by_name,
    find_part_by_name_and_revision,
)


RUN_ID = int(time.time())
PREFIX = f"E2E-PR2-{RUN_ID}"

# Track created PKs for cleanup
_created_parts: list[Part] = []
_created_companies: list[Company] = []


def _safe_delete(obj) -> None:
    """Best-effort deactivate-then-delete, swallowing exceptions."""
    try:
        if hasattr(obj, "save") and "active" in (obj._data or {}):
            obj.save({"active": False})
    except Exception:
        pass
    try:
        obj.delete()
    except Exception as exc:
        safe = re.sub(r"Token\s+[A-Za-z0-9._-]+", "Token ***REDACTED***", str(exc))
        print(f"  cleanup-warn: delete pk={getattr(obj,'pk','?')}: {safe}",
              file=sys.stderr)


def _track(part: Part) -> Part:
    _created_parts.append(part)
    return part


def _track_company(c: Company) -> Company:
    _created_companies.append(c)
    return c


_created_categories: list[PartCategory] = []


def _ensure_category(api: InvenTreeAPI, name: str) -> PartCategory:
    """Find-or-create a throwaway PartCategory, track for cleanup."""
    # Post-filter: some InvenTree versions silently ignore the `name=` filter
    # (verified for Part.list on v1.3.2; defensive here for PartCategory.list).
    existing = [c for c in PartCategory.list(api, name=name) if c.name == name]
    if existing:
        return existing[0]
    cat = PartCategory.create(api, {"name": name, "description": "e2e test"})
    _created_categories.append(cat)
    return cat


def _params_by_name(api: InvenTreeAPI, part: Part) -> dict[str, str]:
    """Return {template_name: data} for all Parameters on *part*.

    Uses the generic ``parameter/`` endpoint (API >= 429) — see
    upload_parameters() in client.py for the rationale.
    """
    params = Parameter.list(
        api, model_type=part.getModelType(), model_id=part.pk
    )
    out: dict[str, str] = {}
    for p in params:
        try:
            tpl = ParameterTemplate(api, pk=int(p.template))
            out[tpl.name] = p.data
        except Exception:
            out[f"<pk={p.template}>"] = p.data
    return out


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_find_part_by_name_exact(api: InvenTreeAPI) -> None:
    """find_part_by_name must return only EXACT matches, not substring."""
    name_a = f"{PREFIX} ExactA"
    name_b = f"{PREFIX} ExactA Extra"   # contains name_a as substring
    _track(Part.create(api, {"name": name_a, "description": "A", "active": True}))
    _track(Part.create(api, {"name": name_b, "description": "B", "active": True}))

    hit = find_part_by_name(api, name_a)
    assert hit is not None, f"expected to find {name_a!r}"
    assert hit.name == name_a, f"got {hit.name!r}, expected {name_a!r}"
    print(f"  PASS  find_part_by_name exact ({name_a!r})")


def test_find_part_by_name_and_revision(api: InvenTreeAPI) -> None:
    """Helper finds a Part only when both name AND revision match."""
    name = f"{PREFIX} RevPart"
    _track(Part.create(api, {"name": name, "revision": "1.0", "description": "v1.0", "active": True}))
    _track(Part.create(api, {"name": name, "revision": "1.1", "description": "v1.1", "active": True}))

    a = find_part_by_name_and_revision(api, name, "1.0")
    b = find_part_by_name_and_revision(api, name, "1.1")
    c = find_part_by_name_and_revision(api, name, "9.9")

    assert a is not None and a.revision == "1.0", f"v1.0 lookup got {a}"
    assert b is not None and b.revision == "1.1", f"v1.1 lookup got {b}"
    assert c is None, f"v9.9 should not exist, got {c}"
    print(f"  PASS  find_part_by_name_and_revision (name + revision)")


def test_pcb_silently_reuse(api: InvenTreeAPI) -> None:
    """create_pcb_part(): second call with same name+revision returns existing pk."""
    from bom_export import create_pcb_part
    name = f"{PREFIX} PCBReuse"
    revision = "1.0"
    cat = _ensure_category(api, f"{PREFIX} cat")

    first = create_pcb_part(api, cat, name, revision, image=None)
    _track(first)
    second = create_pcb_part(api, cat, name, revision, image=None)

    assert second.pk == first.pk, (
        f"expected reuse of pk={first.pk}, got pk={second.pk}")
    print(f"  PASS  PCB silently-reuse (both runs returned pk={first.pk})")


def test_stencil_silently_reuse(api: InvenTreeAPI) -> None:
    """create_stencil_part(): second call with same name+revision returns existing pk."""
    from bom_export import create_stencil_part
    name = f"{PREFIX} StencilReuse"
    revision = "1.0"
    cat = _ensure_category(api, f"{PREFIX} cat")

    first = create_stencil_part(api, cat, name, revision, image=None)
    _track(first)
    second = create_stencil_part(api, cat, name, revision, image=None)

    assert second.pk == first.pk, (
        f"expected reuse of pk={first.pk}, got pk={second.pk}")
    print(f"  PASS  Stencil silently-reuse (both runs returned pk={first.pk})")


def test_assembly_silently_reuse(api: InvenTreeAPI) -> None:
    """create_assembly_part(): second call with same name+revision returns existing pk."""
    from bom_export import create_assembly_part
    name = f"{PREFIX} AssemblyReuse"
    revision = "1.0"
    cat = _ensure_category(api, f"{PREFIX} cat")

    first = create_assembly_part(api, cat, name, revision, image=None)
    _track(first)
    second = create_assembly_part(api, cat, name, revision, image=None)

    assert second.pk == first.pk, (
        f"expected reuse of pk={first.pk}, got pk={second.pk}")
    print(f"  PASS  Assembly silently-reuse (both runs returned pk={first.pk})")


def test_bom_idempotent(api: InvenTreeAPI) -> None:
    """populate_bom(): second call with same parts produces no duplicate BomItems."""
    from bom_export import create_assembly_part, create_pcb_part, populate_bom
    from inventree_sync.models import BomEntry
    cat = _ensure_category(api, f"{PREFIX} cat")

    assembly = _track(create_assembly_part(api, cat, f"{PREFIX} BomTest", "1.0", image=None))
    pcb = _track(create_pcb_part(api, cat, f"{PREFIX} BomTest", "1.0", image=None))
    component = _track(Part.create(api, {
        "name": f"{PREFIX} Comp1", "description": "comp", "active": True, "component": True}))

    entry = BomEntry(
        reference="R1",
        qty=2,
        kicad_part="R", kicad_value="10k", kicad_footprint="R_0805_2012Metric",
    )
    entry.inventree_part = [component]

    # First call: populate
    populate_bom(api, assembly, pcb, [entry])
    items_first = BomItem.list(api, part=assembly.pk)
    n_first = len(items_first)
    assert n_first >= 2, f"expected >=2 BomItems after first populate, got {n_first}"

    # Second call: must not duplicate
    populate_bom(api, assembly, pcb, [entry])
    items_second = BomItem.list(api, part=assembly.pk)
    n_second = len(items_second)
    assert n_second == n_first, (
        f"populate_bom not idempotent: first={n_first}, second={n_second} BomItems")
    print(f"  PASS  populate_bom idempotent ({n_first} BomItems both runs)")


def test_multi_sku_supplier_parts(api: InvenTreeAPI) -> None:
    """create_part_in_inventree(): two LCSC SKUs → two SupplierParts."""
    from inventree_sync.client import create_part_in_inventree
    from inventree_sync.models import PartData

    supplier = _track_company(Company.create(api, {
        "name": f"{PREFIX} TestSupplier", "is_supplier": True,
    }))

    # SKUs prefixed with RUN_ID so re-running this test on the same server
    # doesn't collide (SKU is server-unique; SupplierPart cleanup relies on
    # cascade-delete which we don't explicitly verify here).
    sku_a = f"{PREFIX}-LCSC-A"
    sku_b = f"{PREFIX}-LCSC-B"

    pdata = PartData(
        mpn=f"{PREFIX}-MPN",
        manufacturer=f"{PREFIX} TestMfr",
        description="multi-sku test",
        lcsc_sku=sku_a,   # used as the "primary" by current code,
                          # but Task 5 must also create the rest
    )
    # The new signature must accept a SKU list. We will pass two SKUs and
    # assert both end up as SupplierParts.
    part = create_part_in_inventree(
        api,
        name=f"{PREFIX} MultiSkuPart",
        part_data=pdata,
        category=None,
        lcsc_supplier=supplier,
        mouser_supplier=None,
        lcsc_skus=[sku_a, sku_b],
        mouser_skus=[],
    )
    assert part is not None, "create_part_in_inventree returned None"
    _track(part)

    sps = SupplierPart.list(api, part=part.pk)
    skus = sorted(sp.SKU for sp in sps)
    assert skus == [sku_a, sku_b], (
        f"expected both SKUs as SupplierParts, got {skus}")
    print(f"  PASS  Multi-SKU SupplierParts ({skus})")


def test_parameter_sync_delta(api: InvenTreeAPI) -> None:
    """upload_parameters() delta-sync: overwrite present keys, leave others alone."""
    from inventree_sync.client import upload_parameters

    part = _track(Part.create(api, {
        "name": f"{PREFIX} ParamPart",
        "description": "param sync test",
        "active": True,
        "component": True,
    }))

    # First sync: A=1, B=2
    upload_parameters(api, part, {"Resistance": "10kΩ", "Tolerance": "1%"})
    snapshot1 = _params_by_name(api, part)
    assert snapshot1 == {"Resistance": "10kΩ", "Tolerance": "1%"}, snapshot1

    # Second sync: A overwritten, B not mentioned (must stay), C added.
    upload_parameters(api, part, {"Resistance": "11kΩ", "Voltage": "50V"})
    snapshot2 = _params_by_name(api, part)
    assert snapshot2 == {
        "Resistance": "11kΩ",   # overwritten
        "Tolerance": "1%",      # unchanged (delta semantics)
        "Voltage": "50V",        # new
    }, snapshot2

    print(f"  PASS  parameter sync delta ({snapshot2!r})")


def test_supplier_link_populated(api: InvenTreeAPI) -> None:
    """create_part_in_inventree(): SupplierPart.link is populated for LCSC + Mouser."""
    from inventree_sync.client import create_part_in_inventree
    from inventree_sync.models import PartData

    lcsc = _track_company(Company.create(api, {
        "name": f"{PREFIX} LCSC", "is_supplier": True,
    }))
    mouser = _track_company(Company.create(api, {
        "name": f"{PREFIX} Mouser", "is_supplier": True,
    }))

    lcsc_sku = f"{PREFIX}-LCSC-LNK"
    mouser_sku = f"{PREFIX}-MOU-LNK"

    pdata = PartData(
        mpn=f"{PREFIX}-MPN-LNK",
        manufacturer=f"{PREFIX} Mfr-LNK",
        description="link test",
        lcsc_sku=lcsc_sku,
        mouser_sku=mouser_sku,
    )
    part = create_part_in_inventree(
        api,
        name=f"{PREFIX} LinkPart",
        part_data=pdata,
        category=None,
        lcsc_supplier=lcsc,
        mouser_supplier=mouser,
        lcsc_skus=[lcsc_sku],
        mouser_skus=[mouser_sku],
    )
    assert part is not None
    _track(part)

    sps = SupplierPart.list(api, part=part.pk)
    by_sku = {sp.SKU: sp for sp in sps}
    assert lcsc_sku in by_sku, f"LCSC SupplierPart missing; got {list(by_sku)}"
    assert mouser_sku in by_sku, f"Mouser SupplierPart missing; got {list(by_sku)}"

    lcsc_expected = f"https://www.lcsc.com/product-detail/{lcsc_sku}.html"
    mouser_expected = f"https://www.mouser.com/ProductDetail/{mouser_sku}"
    assert by_sku[lcsc_sku].link == lcsc_expected, (
        f"LCSC link {by_sku[lcsc_sku].link!r} != {lcsc_expected!r}")
    assert by_sku[mouser_sku].link == mouser_expected, (
        f"Mouser link {by_sku[mouser_sku].link!r} != {mouser_expected!r}")
    print(f"  PASS  supplier link populated (LCSC + Mouser)")


def test_attachment_idempotent(api: InvenTreeAPI) -> None:
    """attach_kibot_outputs(): idempotent — second call adds nothing."""
    import tempfile
    from bom_export import create_assembly_part, create_pcb_part, create_stencil_part
    from inventree_sync.attachments import attach_kibot_outputs

    cat = _ensure_category(api, f"{PREFIX} cat")
    pcb = _track(create_pcb_part(api, cat, f"{PREFIX} AttachTest", "1.0", image=None))
    assembly = _track(create_assembly_part(api, cat, f"{PREFIX} AttachTest", "1.0", image=None))
    stencil = _track(create_stencil_part(api, cat, f"{PREFIX} AttachTest", "1.0", image=None))

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        proj = f"{PREFIX}-Attach"
        # Files matching the mapping patterns:
        (out / f"{proj}.step").write_text("dummy STEP")              # → PCB
        (out / f"{proj}-3D_top.png").write_bytes(b"PNGbytes" * 30)    # → PCB
        (out / f"{proj}-3D_bottom.png").write_bytes(b"PNGbytes" * 30) # → PCB
        (out / f"{proj}-stencil_top.svg").write_text("dummy SVG")     # → Stencil
        (out / "Fabrication").mkdir()
        (out / "Fabrication" / f"{proj}-stencil.zip").write_bytes(b"ZIPbytes" * 30)  # → Stencil
        (out / f"{proj}-schematic.pdf").write_text("dummy PDF")       # → Assembly
        (out / f"{proj}-bom.html").write_text("dummy BOM HTML")        # → Assembly
        (out / f"{proj}-bom.csv").write_text("dummy BOM CSV")          # → Assembly
        (out / f"{proj}-ibom.html").write_text("dummy IBOM")           # → Assembly
        # Skipped files (already Part.image):
        (out / f"{proj}-3D_top-with.png").write_bytes(b"skip")
        (out / f"{proj}-3D_top-without.png").write_bytes(b"skip")
        (out / f"{proj}-stencil_top.png").write_bytes(b"skip")

        # First call: should land everything except the 3 skipped images.
        attach_kibot_outputs(api, pcb, assembly, stencil, out)
        n_pcb_1 = len(pcb.getAttachments())
        n_assembly_1 = len(assembly.getAttachments())
        n_stencil_1 = len(stencil.getAttachments())
        total_1 = n_pcb_1 + n_assembly_1 + n_stencil_1
        # Expected attached:
        #   PCB: .step + 3D_top + 3D_bottom = 3
        #   Assembly: schematic + bom.html + bom.csv + ibom.html = 4
        #   Stencil: stencil_top.svg + Fabrication/stencil.zip = 2
        # Total = 9. Skipped: 3 image files. No double-attach.
        assert total_1 == 9, (
            f"expected 9 attachments after first call, got {total_1} "
            f"(pcb={n_pcb_1}, assembly={n_assembly_1}, stencil={n_stencil_1})")

        # Second call: idempotent — no new uploads.
        attach_kibot_outputs(api, pcb, assembly, stencil, out)
        n_pcb_2 = len(pcb.getAttachments())
        n_assembly_2 = len(assembly.getAttachments())
        n_stencil_2 = len(stencil.getAttachments())
        total_2 = n_pcb_2 + n_assembly_2 + n_stencil_2
        assert total_2 == total_1, (
            f"not idempotent: first total={total_1}, second total={total_2}")
        print(f"  PASS  attachment idempotent (total={total_1}, second run no-op)")


def test_cost_report_generation(api: InvenTreeAPI) -> None:
    """generate_cost_report() produces Markdown + patches assembly.notes."""
    from inventree.company import Company, SupplierPart, SupplierPriceBreak
    from bom_export import create_assembly_part, create_pcb_part
    from inventree_sync.cost_report import generate_cost_report
    from inventree_sync.models import BomEntry
    cat = _ensure_category(api, f"{PREFIX} cat")

    # Test infrastructure: one Company, one Part with price breaks, one BomEntry.
    supplier = _track_company(Company.create(api, {
        "name": f"{PREFIX} TestSupplierForCost", "is_supplier": True,
    }))
    component = _track(Part.create(api, {
        "name": f"{PREFIX} CostComp", "description": "comp", "active": True,
        "component": True, "purchaseable": True,
    }))
    sp = SupplierPart.create(api, {
        "part": component.pk, "supplier": supplier.pk,
        "SKU": f"{PREFIX}-SKU-1",
    })
    SupplierPriceBreak.create(api, {
        "part": sp.pk, "quantity": 1, "price": "0.5", "price_currency": "EUR",
    })
    SupplierPriceBreak.create(api, {
        "part": sp.pk, "quantity": 100, "price": "0.2", "price_currency": "EUR",
    })

    assembly = _track(create_assembly_part(api, cat, f"{PREFIX} CostTest", "1.0", image=None))
    pcb = _track(create_pcb_part(api, cat, f"{PREFIX} CostTest", "1.0", image=None))

    entry = BomEntry(
        reference="U1", qty=2,
        kicad_part="X", kicad_value="CostTest", kicad_footprint="dummy",
    )
    entry.inventree_part = [component]

    md = generate_cost_report(api, assembly, [entry], tiers=(1, 10, 100))

    # Sanity: markdown contains expected pieces.
    assert "## BOM Cost Report" in md, f"missing header in:\n{md}"
    assert "| 1 |" in md and "| 10 |" in md and "| 100 |" in md, (
        f"missing tier rows in:\n{md}")
    # Tier 1: 2 units * 1 board = 2 needed. Best valid break is qty>=1 at 0.5.
    # Total = 2 * 0.5 = 1.00, per-board = 1.00.
    assert "€1.00" in md, f"expected tier-1 total €1.00 in:\n{md}"
    # Tier 100: 2 units * 100 boards = 200 needed. Best valid break is qty>=100 at 0.2.
    # Total = 200 * 0.2 = 40.00, per-board = 0.40.
    assert "€40.00" in md, f"expected tier-100 total €40.00 in:\n{md}"
    # Supplier name must surface in the Sources column. If supplier_detail
    # ever stops being included in the SupplierPart.list response, this
    # assertion catches it (without it, every supplier renders as "?").
    assert "TestSupplierForCost" in md, (
        f"supplier name missing from Sources column in:\n{md}")

    # Re-fetch the assembly to verify notes were patched.
    refreshed = Part(api, pk=assembly.pk)
    notes = getattr(refreshed, "notes", None) or refreshed._data.get("notes") or ""
    assert "BOM Cost Report" in notes, f"assembly.notes not patched, got: {notes!r}"
    print(f"  PASS  cost-report generation (markdown len={len(md)}, notes patched)")


def test_refresh_idempotent(api: InvenTreeAPI) -> None:
    """inventree_refresh: idempotent + a no-op on a Part it already refreshed."""
    import subprocess
    # Create a throwaway Part with a real LCSC SKU so the refresh has work
    # to do. C17414 = Uniroyal 10kΩ 0805 (used elsewhere in our PR-1 probes).
    cat = _ensure_category(api, f"{PREFIX} cat")
    # Always create a cleanup-safe LCSC supplier. Name must contain "lcsc"
    # (case-insensitive) for collect_parts_to_refresh to discover it.
    # Suffix "Refresh" to avoid collision with the LCSC company already
    # created by test_supplier_link_populated (server enforces
    # unique (name, email) on Company).
    lcsc_supplier = _track_company(Company.create(api, {
        "name": f"{PREFIX} LCSC Refresh", "is_supplier": True,
    }))

    target = _track(Part.create(api, {
        "name": f"{PREFIX} RefreshTest",
        "description": "",  # empty so refresh will populate it
        "active": True, "component": True, "purchaseable": True,
    }))
    SupplierPart.create(api, {
        "part": target.pk, "supplier": lcsc_supplier.pk, "SKU": "C17414",
    })

    # MouserFetcher.__init__ requires MOUSER_API_KEY even when no Mouser
    # SKUs exist. Test uses only LCSC, so a dummy key is fine — Mouser API
    # is never called for the test Part.
    sub_env = {**os.environ}
    sub_env.setdefault("MOUSER_API_KEY", "dummy-for-e2e-test")

    # First run: should populate fields
    proc1 = subprocess.run(
        [sys.executable, "scripts/inventree_refresh.py"],
        env=sub_env, capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert "Refresh complete:" in proc1.stderr or "Refresh complete:" in proc1.stdout, (
        f"refresh did not finish cleanly:\nSTDOUT:\n{proc1.stdout}\nSTDERR:\n{proc1.stderr}")

    # Second run: should also complete without crashing (idempotent).
    proc2 = subprocess.run(
        [sys.executable, "scripts/inventree_refresh.py"],
        env=sub_env, capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc2.returncode == 0, (
        f"second refresh exit={proc2.returncode}:\n"
        f"STDOUT:\n{proc2.stdout}\nSTDERR:\n{proc2.stderr}")
    print("  PASS  refresh idempotent (2 runs, exit 0, no crash)")


def test_dry_run_no_side_effects(api: InvenTreeAPI) -> None:
    """bom_export.py --dry-run produces stdout output, creates no Parts."""
    import subprocess
    import tempfile

    # Mix SKIP (no SKU) and CREATE (synthetic LCSC SKU that won't resolve).
    # The CREATE row is critical: it locks the CREATE→FAIL no-double-report
    # contract (a brand-new SKU triggers CREATE in ensure_parts_exist; without
    # the guard in match_supplier_parts it would also FAIL → CI false negative
    # on every new part).
    synthetic_sku = f"DRYRUN-{PREFIX}-NEW"
    csv_content = (
        '"References","Quantity Per PCB","Part","Value","Footprint","LCSC","MOUSER"\n'
        '"R1","1","R","10k","R_0805_2012Metric","",""\n'  # no SKU → SKIP
        f'"R2","1","R","10k","R_0805_2012Metric","{synthetic_sku}",""\n'  # new SKU → CREATE
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        tmp.write(csv_content)
        csv_path = tmp.name

    try:
        # Baseline part count before dry-run
        before = len(Part.list(api))

        proc = subprocess.run(
            [
                sys.executable, "scripts/bom_export.py",
                "--csv_file", csv_path,
                "--name", f"{PREFIX}DryRunTest",
                "--version", "1.0",
                "--pcb_image", "doc/Icon.png",
                "--assembly_image", "doc/Icon.png",
                "--dry-run",
            ],
            env={**os.environ},
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )

        out = proc.stdout
        assert "DRY-RUN:" in out, f"missing DRY-RUN marker in stdout:\n{out}\nSTDERR:\n{proc.stderr}"
        assert "Would SKIP:" in out, f"expected Would SKIP line for R1:\n{out}"
        assert "Would CREATE:" in out, f"expected Would CREATE line for R2:\n{out}"
        assert "Summary:" in out, f"missing Summary line:\n{out}"
        # CRITICAL: R2 went through ensure_parts_exist as CREATE; it must NOT
        # also appear as FAIL even though its SKU has no SupplierPart yet.
        # Per-line check (action labels are padded — "Would FAIL:" + ljust
        # would produce 3 spaces before the target, but the padding is an
        # implementation detail we don't want to lock in the assertion).
        fail_lines_for_r2 = [
            ln for ln in out.splitlines()
            if "Would FAIL" in ln and "R2" in ln
        ]
        assert not fail_lines_for_r2, (
            f"dry-run double-reported R2 as CREATE+FAIL (bug from review): "
            f"{fail_lines_for_r2}\nfull output:\n{out}")
        # Net exit-code should be 0 because the only "miss" is a CREATE record.
        assert proc.returncode == 0, (
            f"dry-run exited {proc.returncode} on a CREATE-only BOM "
            f"(expected 0):\nSTDOUT:\n{out}\nSTDERR:\n{proc.stderr}")

        after = len(Part.list(api))
        assert before == after, (
            f"dry-run created {after - before} parts on the server (expected 0)")
        print(f"  PASS  dry-run no side-effects (stdout {len(out)}B, parts unchanged)")
    finally:
        # Clean up the tempfile to avoid /tmp leaks on repeated runs.
        try:
            os.unlink(csv_path)
        except OSError:
            pass


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


def test_minimum_stock_set_and_preserved(api: InvenTreeAPI) -> None:
    """populate_bom with planned_builds sets minimum_stock; higher wins.

    Constructs an Assembly + PCB + one component Part, then calls populate_bom
    twice:
      Pass 1: planned_builds=5, entry.qty=3 → minimum_stock should be 15.
      Pass 2: planned_builds=2 (lower), entry.qty=3 → minimum_stock STAYS 15.
    Verifies the "higher wins" contract from #15.
    """
    from bom_export import create_assembly_part, create_pcb_part, populate_bom
    from inventree_sync import BomEntry
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


def test_generic_connector_mpn_disambiguation(api: InvenTreeAPI) -> None:
    """generate_part_name uses MPN from part_data for generic connector symbols.

    PR-6 inserts an MPN-from-part_data path into generate_part_name's
    else-branch for Conn_*/Screw_Terminal_* symbols. Two physically-distinct
    connectors that share the KiCad symbol Conn_02x10_Row_Letter_First
    (Stiftleiste straight vs Buchsenleiste) must produce different InvenTree
    Part names so the find_part_by_name fallback in ensure_parts_exist
    doesn't collapse them into a single Part.

    Pure-function test — no server side-effects — but lives in the E2E harness
    because it documents the integration contract the harness exists to
    protect. The *api* arg is unused; kept for harness-uniformity.
    """
    del api  # unused — pure-function integration check
    from inventree_sync.categories import generate_part_name
    from inventree_sync.models import PartData

    sym = "Conn_02x10_Row_Letter_First"
    fp_a = "PCN10-20P-2.54DS"
    fp_b = "PCN10C-20S-2.54DS"

    # Without part_data (dry-run path): both collapse to symbol name.
    no_pd_a = generate_part_name(sym, sym, fp_a)
    no_pd_b = generate_part_name(sym, sym, fp_b)
    assert no_pd_a == no_pd_b == sym, (
        f"dry-run fallback should keep generic symbol name; "
        f"got {no_pd_a!r}, {no_pd_b!r}")

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
        "STM32U575CITx", "STM32U575CITx", "LQFP-48_7x7mm_P0.5mm",
        part_data=pd_ic,
    )
    assert ic_name == "STM32U575CITx", (
        f"IC name should be kicad_value (STM32U575CITx), not MPN; got {ic_name!r}")

    print(f"  PASS  generic_connector_mpn_disambiguation "
          f"({real_a!r} vs {real_b!r}, IC={ic_name!r})")


def test_ensure_manufacturer_part_backfills_missing(api: InvenTreeAPI) -> None:
    """ensure_supplier_parts backfills a missing ManufacturerPart (PR-9).

    Reproduces the PowerBoard-v1.1 first-sync failure mode: a Part exists
    without ManufacturerPart linkage (e.g. because the first sync ran
    without Company-API permissions and the MfrPart-Create silently 403'd).
    Calling ensure_supplier_parts on it must create the MfrPart from
    part_data. Idempotent: a second call must not produce a duplicate.
    """
    from inventree.company import ManufacturerPart
    from inventree_sync.client import ensure_supplier_parts
    from inventree_sync.models import PartData

    target = _track(Part.create(api, {
        "name": f"{PREFIX} MfrBackfill",
        "description": "mfr backfill",
        "active": True,
        "component": True,
    }))
    pd = PartData(
        mpn=f"{PREFIX}-MPN-BACKFILL",
        manufacturer=f"{PREFIX} BackfillMfr",
    )

    def _mfrparts_for(pk: int) -> list:
        """Defensive: server may ignore the part= filter (see PR-9 helper
        docstring). Post-filter the response on mp.part == pk so the test
        doesn't go flaky on a server that returns the global list."""
        raw = ManufacturerPart.list(api, part=pk)
        return [mp for mp in raw if int(getattr(mp, "part", -1)) == int(pk)]

    # Pre-condition: no MfrPart yet.
    pre = _mfrparts_for(target.pk)
    assert len(pre) == 0, f"expected 0 MfrPart, got {len(pre)}"

    # Call 1: should create the MfrPart.
    ensure_supplier_parts(
        api, target, pd,
        lcsc_supplier=None, mouser_supplier=None,
    )
    mps = _mfrparts_for(target.pk)
    assert len(mps) == 1, f"expected 1 MfrPart after first call, got {len(mps)}"
    assert (mps[0].MPN or "").strip() == pd.mpn, (
        f"MfrPart.MPN expected {pd.mpn!r}, got {mps[0].MPN!r}")

    # Verify the linkage points at the right manufacturer Company
    # (case-insensitive — get_or_create_manufacturer's contract).
    mfr_company = Company(api, pk=mps[0].manufacturer)
    assert (mfr_company.name or "").lower() == pd.manufacturer.lower(), (
        f"linked manufacturer Company.name expected {pd.manufacturer!r} "
        f"(case-insensitive), got {mfr_company.name!r}")

    # Track the manufacturer Company for cleanup.
    _created_companies.append(mfr_company)

    # Call 2: idempotent — must not produce a second MfrPart.
    ensure_supplier_parts(
        api, target, pd,
        lcsc_supplier=None, mouser_supplier=None,
    )
    mps2 = _mfrparts_for(target.pk)
    assert len(mps2) == 1, (
        f"expected MfrPart-count to remain 1 after second call, got {len(mps2)}")

    print(f"  PASS  ensure_manufacturer_part backfill+idempotent "
          f"(pk={target.pk}, MfrPart pk={mps[0].pk})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _connect() -> InvenTreeAPI:
    if not os.environ.get("INVENTREE_API_HOST"):
        print("ERROR: INVENTREE_API_HOST not set", file=sys.stderr)
        sys.exit(2)
    return InvenTreeAPI()


def main() -> int:
    api = _connect()
    print(f"→ Run ID: {PREFIX}")
    failed = 0
    try:
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
                   test_minimum_stock_set_and_preserved,
                   test_generic_connector_mpn_disambiguation,
                   test_ensure_manufacturer_part_backfills_missing):
            try:
                tc(api)
            except AssertionError as e:
                print(f"  FAIL  {tc.__name__}: {e}", file=sys.stderr)
                failed += 1
            except Exception as e:
                safe = re.sub(r"Token\s+[A-Za-z0-9._-]+", "Token ***REDACTED***", str(e))
                print(f"  ERROR {tc.__name__}: {safe}", file=sys.stderr)
                failed += 1
    finally:
        if os.environ.get("KEEP_TEST_PARTS") == "1":
            print(f"\nKEEP_TEST_PARTS=1 — leaving {len(_created_parts)} Parts, "
                  f"{len(_created_companies)} Companies for inspection.")
        else:
            print(f"\n→ Cleanup: {len(_created_parts)} Parts, "
                  f"{len(_created_companies)} Companies, "
                  f"{len(_created_categories)} Categories ...")
            for p in _created_parts:
                _safe_delete(p)
            for c in _created_companies:
                _safe_delete(c)
            for cat in _created_categories:
                _safe_delete(cat)
            print("  done.")
    if failed:
        print(f"\nFAIL: {failed} test(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
