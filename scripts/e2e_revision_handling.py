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
                   test_attachment_idempotent):
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
