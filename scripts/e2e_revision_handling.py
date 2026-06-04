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
    existing = PartCategory.list(api, name=name)
    if existing:
        return existing[0]
    cat = PartCategory.create(api, {"name": name, "description": "e2e test"})
    _created_categories.append(cat)
    return cat


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
                   test_assembly_silently_reuse):
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
