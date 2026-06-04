# PR-2: Re-Activation Bug-Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make InvenTree-Sync idempotent (re-runnable without producing duplicate/conflicting Parts) and fix Multi-SKU data loss + lax part lookups.

**Architecture:** Add two helpers in `inventree_sync/client.py` (`find_part_by_name_and_revision`, exact-filter `find_part_by_name`). Wire them into `bom_export.py` for PCB/Stencil/Assembly silently-reuse + BOM idempotency. Switch `match_supplier_parts` to a batch SKU-filter query. Switch `find_existing_part` / `create_part_in_inventree` / `ensure_supplier_parts` / `part_manager.py` to operate on SKU **lists**.

**Tech Stack:** Python 3.x, `requests` 2.34, `inventree` 0.23.1 client lib. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-06-04-pr2-reactivation-bugs.md`](../specs/2026-06-04-pr2-reactivation-bugs.md)

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `scripts/inventree_sync/client.py` | Modify | New helper `find_part_by_name_and_revision`; exact-filter `find_part_by_name`; SKU-Listen für `find_existing_part`, `ensure_supplier_parts`, `create_part_in_inventree`. |
| `scripts/inventree_sync/part_manager.py` | Modify | SKU-Listen statt `[0]` an client.py-Funktionen durchreichen. |
| `scripts/bom_export.py` | Modify | `create_pcb_part`/`create_stencil_part`/`create_assembly_part` mit silently-reuse; `populate_bom` idempotent; `match_supplier_parts` mit batch SKU__in-Query + per-SKU-Fallback. |
| `scripts/e2e_revision_handling.py` | Create | E2E-Smoke-Test (analog `e2e_image_upload.py`) der die neuen Verhaltensweisen gegen einen echten InvenTree-Server prüft. |

---

## Task 1: Helpers — `find_part_by_name_and_revision` + exact `find_part_by_name`, plus E2E-Scaffold

**Files:**
- Modify: `scripts/inventree_sync/client.py` (add `find_part_by_name_and_revision`; rewrite `find_part_by_name`)
- Create: `scripts/e2e_revision_handling.py`

### Step 1.1: Write failing E2E test scaffold

- [ ] Create `scripts/e2e_revision_handling.py` with this content:

```python
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
                   test_find_part_by_name_and_revision):
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
                  f"{len(_created_companies)} Companies ...")
            for p in _created_parts:
                _safe_delete(p)
            for c in _created_companies:
                _safe_delete(c)
            print("  done.")
    if failed:
        print(f"\nFAIL: {failed} test(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] Make executable:

```bash
chmod +x scripts/e2e_revision_handling.py
```

### Step 1.2: Run E2E — expect ImportError

- [ ] Run:

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
source ~/.inventree_test.env
source .venv/bin/activate
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: **`ImportError: cannot import name 'find_part_by_name_and_revision' from 'inventree_sync.client'`** — the helper does not exist yet. Confirm by reading the traceback. If any other error: stop and diagnose.

### Step 1.3: Add `find_part_by_name_and_revision` + fix `find_part_by_name`

- [ ] Open `scripts/inventree_sync/client.py`. Locate the existing `find_part_by_name` function (around line 201). Replace it AND add the new helper directly below it:

```python
def find_part_by_name(api: InvenTreeAPI, name: str) -> Optional[Part]:
    """Return the InvenTree Part with an exact name match, or None.

    Uses InvenTree's ``name=`` exact-filter (not ``search=`` which is a
    substring match) so part names that share a prefix or substring don't
    collide.  If multiple Parts have the same exact name (legal — e.g.
    same name in different categories) the first is returned.
    """
    if not name:
        return None
    try:
        results = Part.list(api, name=name)
    except Exception as exc:
        logger.debug("Part name lookup failed for '%s': %s", name, exc)
        return None
    return results[0] if results else None


def find_part_by_name_and_revision(
    api: InvenTreeAPI, name: str, revision: str
) -> Optional[Part]:
    """Return the Part matching BOTH name AND revision, or None.

    Used by ``bom_export.py`` to make PCB/Stencil/Assembly anlage
    idempotent — if the same release tag is processed twice, the
    second run should re-use the existing Part instead of trying to
    create a duplicate (which would fail InvenTree's unique-together
    constraint on name+revision).
    """
    if not name or not revision:
        return None
    try:
        results = Part.list(api, name=name, revision=revision)
    except Exception as exc:
        logger.debug("Part name+revision lookup failed for '%s' rev %s: %s",
                     name, revision, exc)
        return None
    return results[0] if results else None
```

### Step 1.4: Syntax check

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/client.py scripts/e2e_revision_handling.py
```

- [ ] Expected: no output (exit 0).

### Step 1.5: Run E2E — expect both tests pass

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected output:

```
→ Run ID: E2E-PR2-<timestamp>
  PASS  find_part_by_name exact ('E2E-PR2-<ts> ExactA')
  PASS  find_part_by_name_and_revision (name + revision)

→ Cleanup: 4 Parts, 0 Companies ...
  done.

All tests passed.
```

Exit code 0.

### Step 1.6: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/client.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): find_part_by_name exact-filter + find_part_by_name_and_revision

Bug #3 fix: find_part_by_name() bisher Substring-search (Part.list(search=name))
was bei Pagination den exakten Treffer übersehen kann. Jetzt name= exact-filter.

Neuer Helper find_part_by_name_and_revision() für Bug #2 — wird in Task 2
(bom_export.py PCB/Stencil/Assembly silently-reuse) verwendet.

scripts/e2e_revision_handling.py: neuer E2E-Smoke-Test analog
e2e_image_upload.py — testet hier die zwei Helper, wird in nachfolgenden
Tasks um Reuse/Idempotency/Multi-SKU-Tests erweitert.

Refs: docs/superpowers/specs/2026-06-04-pr2-reactivation-bugs.md
EOF
)"
```

---

## Task 2: PCB/Stencil/Assembly silently-reuse

**Files:**
- Modify: `scripts/bom_export.py` (`create_pcb_part`, `create_stencil_part`, `create_assembly_part`)
- Modify: `scripts/e2e_revision_handling.py` (add reuse test cases)

### Step 2.1: Extend E2E test with reuse assertions

- [ ] In `scripts/e2e_revision_handling.py`, add an import and three new test functions BEFORE `def _connect():`. Insert after `test_find_part_by_name_and_revision`:

```python
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
```

- [ ] In the same file, add this category helper BEFORE the test functions (after the `_track_company` definition):

```python
_created_categories: list[PartCategory] = []


def _ensure_category(api: InvenTreeAPI, name: str) -> PartCategory:
    """Find-or-create a throwaway PartCategory, track for cleanup."""
    existing = PartCategory.list(api, name=name)
    if existing:
        return existing[0]
    cat = PartCategory.create(api, {"name": name, "description": "e2e test"})
    _created_categories.append(cat)
    return cat
```

- [ ] In `main()`, EXTEND the test-case tuple to include the new tests:

```python
        for tc in (test_find_part_by_name_exact,
                   test_find_part_by_name_and_revision,
                   test_pcb_silently_reuse,
                   test_stencil_silently_reuse,
                   test_assembly_silently_reuse):
```

- [ ] In the `finally` cleanup block, add category cleanup AFTER the companies loop:

```python
            for cat in _created_categories:
                _safe_delete(cat)
```

- [ ] Update the cleanup-status print to also mention categories. Replace the existing print line with:

```python
            print(f"\n→ Cleanup: {len(_created_parts)} Parts, "
                  f"{len(_created_companies)} Companies, "
                  f"{len(_created_categories)} Categories ...")
```

- [ ] Update the `sys.path.insert` call to also expose the `scripts/` dir for `from bom_export import ...`. Find the line `sys.path.insert(0, str(Path(__file__).parent))` and leave it (already does that — `scripts/` is `Path(__file__).parent`).

### Step 2.2: Run E2E — expect 3 new tests fail with `UNIQUE constraint`

- [ ] Run:

```bash
python3 -m py_compile scripts/e2e_revision_handling.py
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: first 2 tests PASS, the 3 new reuse tests ERROR (the second `Part.create` call will raise an HTTP 400 from InvenTree with a unique-constraint message like `"name with revision must be unique"` or similar). Exit 1.

If any of the reuse tests PASS at this point, **stop and diagnose** — that would mean InvenTree silently overwrites or generates a new pk, both unexpected.

### Step 2.3: Modify `create_pcb_part`/`create_stencil_part`/`create_assembly_part`

- [ ] Open `scripts/bom_export.py`. At the imports section near the top, ADD this import after the `from inventree_sync.categories import load_category_map` line:

```python
from inventree_sync.client import find_part_by_name_and_revision
```

- [ ] Replace `create_pcb_part` with:

```python
def create_pcb_part(api: InvenTreeAPI, category: PartCategory, name: str, version: str, image: str) -> Part:
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
    if image:
        assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created PCB part '%s' rev %s (pk=%s)", full_name, version, part.pk)
    return part
```

- [ ] Replace `create_assembly_part` with:

```python
def create_assembly_part(api: InvenTreeAPI, category: PartCategory, name: str, version: str, image: str) -> Part:
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
    if image:
        assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created assembly part '%s' rev %s (pk=%s)", full_name, version, part.pk)
    return part
```

- [ ] Replace `create_stencil_part` with:

```python
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
    if image:
        assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created stencil part '%s' rev %s (pk=%s)", full_name, version, part.pk)
    return part
```

Note: the existing `create_pcb_part` and `create_assembly_part` had unconditional image upload via `assert part.uploadImage(image) is not None`. In the new version we wrap that in `if image:` because the E2E test passes `image=None` for these. Existing callers always pass a non-None image, so behavior unchanged for them.

### Step 2.4: Syntax check

- [ ] Run:

```bash
python3 -m py_compile scripts/bom_export.py scripts/e2e_revision_handling.py
```

- [ ] Expected: no output.

### Step 2.5: Run E2E — expect all 5 tests pass

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected:

```
→ Run ID: E2E-PR2-<ts>
  PASS  find_part_by_name exact (...)
  PASS  find_part_by_name_and_revision (name + revision)
  PASS  PCB silently-reuse (both runs returned pk=<n>)
  PASS  Stencil silently-reuse (both runs returned pk=<n>)
  PASS  Assembly silently-reuse (both runs returned pk=<n>)

→ Cleanup: 7 Parts, 0 Companies, 1 Categories ...
  done.

All tests passed.
```

Exit 0. Number of "Parts" in cleanup may vary — the assertion that matters is `All tests passed.`

### Step 2.6: Commit

- [ ] Run:

```bash
git add scripts/bom_export.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
fix(bom-export): PCB/Stencil/Assembly silently-reuse on re-run

Bug #2 fix: bisher hat ein Re-Run desselben Release-Tags die
Auto-Release-Workflow gebrochen weil InvenTree's unique-Constraint auf
(name, revision) gefeuert hat — selbst wenn der physische PCB byte-identisch
ist und der erste Run nur transient gefailt war.

Jetzt: vor Part.create() ein find_part_by_name_and_revision()-Lookup.
Existiert schon → reuse + log.info; nicht → create wie bisher.

Macht die Workflow vollständig re-runnable. BOM-idempotenz (Task 3)
schließt die Lücke für Re-Run nach successful PCB-/Stencil-Anlage.

Plus: image-Upload jetzt durch `if image:` guardiert (Test-Pfad nutzt
image=None; Produktions-Call-Sites passen weiterhin non-None image durch).

Refs: docs/superpowers/specs/2026-06-04-pr2-reactivation-bugs.md
EOF
)"
```

---

## Task 3: BOM idempotency in `populate_bom`

**Files:**
- Modify: `scripts/bom_export.py` (`populate_bom`)
- Modify: `scripts/e2e_revision_handling.py` (add BOM idempotency test)

### Step 3.1: Extend E2E test with BOM idempotency assertion

- [ ] In `scripts/e2e_revision_handling.py`, ADD a new test function after `test_assembly_silently_reuse`:

```python
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
```

- [ ] Add `test_bom_idempotent` to the test-case tuple in `main()`:

```python
        for tc in (test_find_part_by_name_exact,
                   test_find_part_by_name_and_revision,
                   test_pcb_silently_reuse,
                   test_stencil_silently_reuse,
                   test_assembly_silently_reuse,
                   test_bom_idempotent):
```

### Step 3.2: Run — expect `test_bom_idempotent` to fail with duplicate items

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: the previous 5 tests still PASS; `test_bom_idempotent` fails because the second `populate_bom` either raises (duplicate constraint) or doubles the BomItem count. Either failure mode is acceptable proof the bug exists.

### Step 3.3: Modify `populate_bom` to skip existing BomItems

- [ ] Open `scripts/bom_export.py`. Find the existing `populate_bom` function (around line 154). Replace it with:

```python
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
```

### Step 3.4: Syntax check

- [ ] Run:

```bash
python3 -m py_compile scripts/bom_export.py scripts/e2e_revision_handling.py
```

- [ ] Expected: no output.

### Step 3.5: Run E2E — expect all 6 tests pass

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: all 6 tests PASS, exit 0. The `populate_bom idempotent` test prints the number of BomItems (must be identical between first and second run).

### Step 3.6: Commit

- [ ] Run:

```bash
git add scripts/bom_export.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
fix(bom-export): populate_bom idempotent

Bug #2 (part 2): nach dem PCB/Stencil/Assembly-reuse-fix war ein Re-Run
gegen denselben Assembly-pk noch problematisch weil populate_bom blind
BomItem.create() für jede Entry aufgerufen hat — InvenTree's unique-
together auf (part, sub_part, reference) hätte das gerejected oder
(je nach Version) duplicate items zugelassen.

Jetzt: einmal BomItem.list(part=assembly.pk) am Anfang, Set von
(sub_part_pk, reference)-Keys aufbauen, vor jedem create skippen wenn
Key schon drin. Loggt am Ende "X new, Y skipped" als Run-Statistik.

Damit ist die komplette Auto-Release-Workflow re-runnable: PCB/Stencil/
Assembly werden reusen, BOM-population macht Idempotenz selber.

Refs: docs/superpowers/specs/2026-06-04-pr2-reactivation-bugs.md
EOF
)"
```

---

## Task 4: Batch SKU lookup in `match_supplier_parts`

**Files:**
- Modify: `scripts/bom_export.py` (`match_supplier_parts`)

### Step 4.1: Modify `match_supplier_parts`

This task has no easy E2E test scaffold — the behavior change is internal (faster lookup, no observable difference unless you measure API timing). We rely on:
1. `py_compile` clean.
2. Existing pytest suite green.
3. Running the E2E script (Task 5 will exercise this code path indirectly via Multi-SKU test).
4. Functional verification post-merge with real BOM.

- [ ] Open `scripts/bom_export.py`. Replace `match_supplier_parts` (around line 75) with:

```python
def match_supplier_parts(api: InvenTreeAPI, entries: list[BomEntry]) -> None:
    """
    Match each BomEntry to its InvenTree Part via SupplierPart SKU lookup.
    Populates entry.inventree_part for every entry that has a supplier SKU.

    Uses a batch ``SKU__in=[...]`` filter (one API call covering every SKU
    referenced by the BOM) instead of fetching the full SupplierPart table.
    Falls back to per-SKU queries if the API version does not support the
    ``__in`` lookup — still N queries instead of one full-table scan.
    """
    all_skus = sorted({
        sku for entry in entries
        for sku in entry.lcsc + entry.mouser
        if sku
    })
    if not all_skus:
        sku_to_part: dict[str, Part] = {}
    else:
        try:
            supplier_parts = SupplierPart.list(api, SKU__in=all_skus)
        except Exception as exc:
            log.warning(
                "SKU__in batch query failed (%s); falling back to per-SKU",
                exc)
            supplier_parts = []
            for sku in all_skus:
                try:
                    supplier_parts.extend(SupplierPart.list(api, SKU=sku))
                except Exception as exc2:
                    log.debug("per-SKU lookup failed for %s: %s", sku, exc2)
        sku_to_part = {
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
```

### Step 4.2: Syntax check + run E2E to confirm no regression

- [ ] Run:

```bash
python3 -m py_compile scripts/bom_export.py
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: no output from py_compile; E2E still 6/6 PASS (this task doesn't add E2E cases; verifies nothing broke).

### Step 4.3: Run existing pytest

- [ ] Run:

```bash
pytest scripts/tests/ -q
```

- [ ] Expected: `64 passed in 0.4s` (or similar — same count as before).

### Step 4.4: Commit

- [ ] Run:

```bash
git add scripts/bom_export.py
git commit -m "$(cat <<'EOF'
perf(bom-export): batch SKU__in query in match_supplier_parts

Bugs #3 + #5: match_supplier_parts() lädt bisher die KOMPLETTE
SupplierPart-Tabelle via SupplierPart.list(api). O(catalog_size) pro
Release. Bei wachsendem Katalog spürbar slower und memory-hungrig.

Jetzt: alle SKUs aus den BomEntries upfront sammeln, EINE gefilterte
Query SupplierPart.list(api, SKU__in=[...]). Skaliert mit BOM-Größe
statt mit Katalog-Größe.

Fallback wenn die InvenTree-Version den __in-Lookup nicht unterstützt:
per-SKU-Query in einer Schleife (immer noch N statt N*catalog).

Refs: docs/superpowers/specs/2026-06-04-pr2-reactivation-bugs.md
EOF
)"
```

---

## Task 5: Multi-SKU SupplierPart anlage

**Files:**
- Modify: `scripts/inventree_sync/client.py` (`find_existing_part`, `ensure_supplier_parts`, `create_part_in_inventree`)
- Modify: `scripts/inventree_sync/part_manager.py` (pass SKU lists instead of `[0]`)
- Modify: `scripts/e2e_revision_handling.py` (add Multi-SKU test)

### Step 5.1: Extend E2E test with Multi-SKU assertion

- [ ] In `scripts/e2e_revision_handling.py`, add this test function after `test_bom_idempotent`:

```python
def test_multi_sku_supplier_parts(api: InvenTreeAPI) -> None:
    """create_part_in_inventree(): two LCSC SKUs → two SupplierParts."""
    from inventree_sync.client import create_part_in_inventree
    from inventree_sync.models import PartData

    supplier = _track_company(Company.create(api, {
        "name": f"{PREFIX} TestSupplier", "is_supplier": True,
    }))

    pdata = PartData(
        mpn=f"{PREFIX}-MPN",
        manufacturer=f"{PREFIX} TestMfr",
        description="multi-sku test",
        lcsc_sku="DUMMY-LCSC-A",   # used as the "primary" by current code,
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
        lcsc_skus=["DUMMY-LCSC-A", "DUMMY-LCSC-B"],
        mouser_skus=[],
    )
    assert part is not None, "create_part_in_inventree returned None"
    _track(part)

    sps = SupplierPart.list(api, part=part.pk)
    skus = sorted(sp.SKU for sp in sps)
    assert skus == ["DUMMY-LCSC-A", "DUMMY-LCSC-B"], (
        f"expected both SKUs as SupplierParts, got {skus}")
    print(f"  PASS  Multi-SKU SupplierParts ({skus})")
```

- [ ] Add to the test-case tuple in `main()`:

```python
        for tc in (test_find_part_by_name_exact,
                   test_find_part_by_name_and_revision,
                   test_pcb_silently_reuse,
                   test_stencil_silently_reuse,
                   test_assembly_silently_reuse,
                   test_bom_idempotent,
                   test_multi_sku_supplier_parts):
```

### Step 5.2: Run — expect ERROR (unexpected keyword argument `lcsc_skus`)

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: first 6 tests PASS, `test_multi_sku_supplier_parts` fails with `TypeError: create_part_in_inventree() got an unexpected keyword argument 'lcsc_skus'`.

### Step 5.3: Modify `find_existing_part` (SKU lists)

- [ ] Open `scripts/inventree_sync/client.py`. Replace `find_existing_part` (around line 187) with:

```python
def find_existing_part(
    api: InvenTreeAPI,
    lcsc_skus: list[str],
    mouser_skus: list[str],
) -> Optional[Part]:
    """Return the InvenTree Part if a SupplierPart matching ANY of the
    given SKUs already exists.

    Bug #4 fix: bisher pro Supplier nur ein einzelner SKU geprüft —
    BOM-Entries mit mehreren Alternativen wurden ggf. als „neuer Part"
    misinterpretiert obwohl ein Alternativ-SKU schon angelegt war.
    """
    for sku in [s for s in (lcsc_skus or []) + (mouser_skus or []) if s]:
        try:
            sp_list = SupplierPart.list(api, SKU=sku)
            if sp_list:
                return Part(api, pk=sp_list[0].part)
        except Exception as exc:
            logger.debug("SupplierPart lookup failed for SKU=%s: %s", sku, exc)
    return None
```

### Step 5.4: Modify `ensure_supplier_parts` (SKU lists)

- [ ] In the same file, replace `ensure_supplier_parts` (around line 215) with:

```python
def ensure_supplier_parts(
    api: InvenTreeAPI,
    part: Part,
    part_data: PartData,
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
    lcsc_skus: Optional[list[str]] = None,
    mouser_skus: Optional[list[str]] = None,
) -> None:
    """Add any missing SupplierParts to an already-existing InvenTree Part.

    If *lcsc_skus*/*mouser_skus* are None, falls back to single SKUs from
    *part_data* (backwards-compat for callers that haven't been migrated
    to lists yet).  Idempotent: only creates SupplierParts whose SKU isn't
    already attached to *part*.
    """
    lcsc_skus = list(lcsc_skus) if lcsc_skus is not None else (
        [part_data.lcsc_sku] if part_data.lcsc_sku else [])
    mouser_skus = list(mouser_skus) if mouser_skus is not None else (
        [part_data.mouser_sku] if part_data.mouser_sku else [])

    try:
        existing_skus = {sp.SKU for sp in SupplierPart.list(api, part=part.pk)}
    except Exception:
        existing_skus = set()

    if lcsc_supplier:
        for sku in lcsc_skus:
            if not sku or sku in existing_skus:
                continue
            try:
                sp = SupplierPart.create(api, {
                    "part": part.pk,
                    "supplier": lcsc_supplier.pk,
                    "SKU": sku,
                })
                existing_skus.add(sku)
                if part_data.price_breaks:
                    _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
            except Exception as exc:
                logger.warning("Could not add LCSC supplier part %s: %s", sku, exc)

    if mouser_supplier:
        for sku in mouser_skus:
            if not sku or sku in existing_skus:
                continue
            try:
                SupplierPart.create(api, {
                    "part": part.pk,
                    "supplier": mouser_supplier.pk,
                    "SKU": sku,
                })
                existing_skus.add(sku)
            except Exception as exc:
                logger.warning("Could not add Mouser supplier part %s: %s", sku, exc)
```

### Step 5.5: Modify `create_part_in_inventree` (SKU lists)

- [ ] In the same file, replace `create_part_in_inventree` (around line 106) with:

```python
def create_part_in_inventree(
    api: InvenTreeAPI,
    name: str,
    part_data: PartData,
    category: Optional[PartCategory],
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
    lcsc_skus: Optional[list[str]] = None,
    mouser_skus: Optional[list[str]] = None,
) -> Optional[Part]:
    """
    Create an InvenTree Part (with manufacturer/supplier parts) from
    *part_data*.  *lcsc_skus*/*mouser_skus* may list multiple distributor
    SKUs that all map to the same MPN; one SupplierPart is created per
    SKU.  If omitted, falls back to single SKUs from *part_data*.

    Returns the created Part, or None on failure.
    """
    lcsc_skus = list(lcsc_skus) if lcsc_skus is not None else (
        [part_data.lcsc_sku] if part_data.lcsc_sku else [])
    mouser_skus = list(mouser_skus) if mouser_skus is not None else (
        [part_data.mouser_sku] if part_data.mouser_sku else [])

    # 1. Create the base part
    part_payload = {
        "name": name,
        "description": part_data.description or name,
        "component": True,
        "purchaseable": True,
        "active": True,
    }
    if category:
        part_payload["category"] = category.pk
    if part_data.datasheet_url:
        part_payload["link"] = part_data.datasheet_url

    try:
        part = Part.create(api, part_payload)
        logger.info("Created part '%s' (pk=%s)", name, part.pk)
    except Exception as exc:
        logger.error("Part creation failed for '%s': %s", name, exc)
        return None

    # 2. Upload image
    if part_data.image_url:
        upload_image_from_url(part, part_data.image_url)

    # 3. Manufacturer part
    if part_data.mpn and part_data.manufacturer:
        manufacturer = get_or_create_manufacturer(api, part_data.manufacturer)
        if manufacturer:
            try:
                ManufacturerPart.create(api, {
                    "part": part.pk,
                    "manufacturer": manufacturer.pk,
                    "MPN": part_data.mpn,
                })
                logger.info("Created ManufacturerPart %s / %s", part_data.manufacturer, part_data.mpn)
            except Exception as exc:
                logger.warning("ManufacturerPart creation failed: %s", exc)

    # 4. LCSC supplier parts (one per SKU)
    if lcsc_supplier:
        for sku in lcsc_skus:
            if not sku:
                continue
            try:
                sp = SupplierPart.create(api, {
                    "part": part.pk,
                    "supplier": lcsc_supplier.pk,
                    "SKU": sku,
                    "manufacturer_part": None,
                })
                if part_data.price_breaks:
                    _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
            except Exception as exc:
                logger.warning("LCSC SupplierPart creation failed (%s): %s", sku, exc)

    # 5. Mouser supplier parts (one per SKU)
    if mouser_supplier:
        # Mouser price breaks only when no LCSC SKU contributed prices.
        attach_mouser_prices = part_data.price_breaks and not lcsc_skus
        for sku in mouser_skus:
            if not sku:
                continue
            try:
                sp = SupplierPart.create(api, {
                    "part": part.pk,
                    "supplier": mouser_supplier.pk,
                    "SKU": sku,
                })
                if attach_mouser_prices:
                    _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
            except Exception as exc:
                logger.warning("Mouser SupplierPart creation failed (%s): %s", sku, exc)

    return part
```

### Step 5.6: Modify `part_manager.py` callsite

- [ ] Open `scripts/inventree_sync/part_manager.py`. Find the block around lines 125–157. Replace lines `lcsc_sku = ...` through the final `create_part_in_inventree(...)` call (the body of the for-loop after the SKU extraction) with:

```python
        lcsc_sku = lcsc_skus[0] if lcsc_skus else ""
        mouser_sku = mouser_skus[0] if mouser_skus else ""

        # Check if a matching SupplierPart already exists in InvenTree —
        # iterates ALL SKUs in the entry, so any alternate that's already
        # in InvenTree resolves the entry.
        existing = find_existing_part(api, lcsc_skus, mouser_skus)
        if existing:
            entry.inventree_part.append(existing)
            logger.info("Found existing part for %s: pk=%s", entry.reference, existing.pk)
            # Ensure all alternative SKUs are attached to that existing part.
            part_data = _fetch_and_merge(lcsc_fetcher, mouser_fetcher, lcsc_sku, mouser_sku)
            if part_data is not None:
                ensure_supplier_parts(
                    api, existing, part_data,
                    lcsc_supplier, mouser_supplier,
                    lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
                )
            continue

        # Fetch data from suppliers (primary SKU)
        part_data = _fetch_and_merge(lcsc_fetcher, mouser_fetcher, lcsc_sku, mouser_sku)
        if part_data is None:
            logger.warning(
                "No supplier data found for %s (LCSC=%s, Mouser=%s)",
                entry.reference, lcsc_skus, mouser_skus,
            )
            continue

        # Generate name; reuse if an InvenTree part with that name already exists
        name = generate_part_name(kicad_part, kicad_value, kicad_footprint)
        existing_by_name = find_part_by_name(api, name)
        if existing_by_name:
            logger.info(
                "Part '%s' already exists (pk=%s); adding missing supplier parts",
                name, existing_by_name.pk,
            )
            ensure_supplier_parts(
                api, existing_by_name, part_data,
                lcsc_supplier, mouser_supplier,
                lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
            )
            entry.inventree_part.append(existing_by_name)
            continue

        category = resolve_part_category(api, kicad_part, part_data, kicad_footprint, category_map)
        inv_part = create_part_in_inventree(
            api, name, part_data, category,
            lcsc_supplier, mouser_supplier,
            lcsc_skus=lcsc_skus, mouser_skus=mouser_skus,
        )
        if inv_part:
            entry.inventree_part.append(inv_part)
        else:
            logger.error("Failed to create part for %s", entry.reference)
```

### Step 5.7: Syntax check + E2E + pytest

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/*.py scripts/bom_export.py scripts/e2e_revision_handling.py
python3 scripts/e2e_revision_handling.py
pytest scripts/tests/ -q
```

- [ ] Expected:
  - `py_compile`: no output.
  - E2E: all 7 tests PASS (including `Multi-SKU SupplierParts (['DUMMY-LCSC-A', 'DUMMY-LCSC-B'])`).
  - pytest: `64 passed`.

### Step 5.8: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/client.py scripts/inventree_sync/part_manager.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
fix(inventree-sync): Multi-SKU SupplierPart anlage (Bug #4)

Bug #4: BOM-Entries mit mehreren LCSC- oder Mouser-SKUs (z.B. zwei
alternative Distributor-Codes für dieselbe MPN) hatten bisher nur den
[0]-SKU als SupplierPart in InvenTree gelandet. Alternativen weg.

Jetzt: create_part_in_inventree(), ensure_supplier_parts() und
find_existing_part() nehmen Listen statt einzelner Strings entgegen.
Pro SKU ein SupplierPart, alle unter dem (einen) ManufacturerPart.

find_existing_part() iteriert jetzt ALLE SKUs für den Lookup — wenn ein
Alternativ-SKU bereits in InvenTree existiert findet er die Part.

part_manager.py reicht jetzt die kompletten lcsc/mouser Listen aus der
BomEntry an client.py durch (statt nur [0]).

Annahme: alle SKUs in einem BOM-Entry zeigen auf denselben MPN. Echt
unterschiedliche MPNs als BOM-Alternates (e.g. „Yageo OR KOA") sind
explizit out-of-scope für PR-2 (Spec dokumentiert).

Refs: docs/superpowers/specs/2026-06-04-pr2-reactivation-bugs.md
EOF
)"
```

---

## Task 6: Final Verification

**Files:** no code changes — verification only.

### Step 6.1: Full py_compile sweep

- [ ] Run:

```bash
python3 -m py_compile \
  scripts/inventree_sync/*.py \
  scripts/bom_export.py \
  scripts/e2e_revision_handling.py \
  scripts/e2e_image_upload.py \
  scripts/probe_supplier_images.py
```

- [ ] Expected: no output.

### Step 6.2: Full E2E run

- [ ] Run:

```bash
source ~/.inventree_test.env
source .venv/bin/activate
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: 7 PASS, exit 0, cleanup happens.

### Step 6.3: Existing pytest suite

- [ ] Run:

```bash
pytest scripts/tests/ -q
```

- [ ] Expected: `64 passed` (no regressions from the existing tests).

### Step 6.4: Image-pipeline regression check

- [ ] Run:

```bash
python3 scripts/probe_supplier_images.py
```

- [ ] Expected: 4 PASS (`HD-URL transform`, `Mouser SPL`, `Mouser HD`, `LCSC 900x900`), exit 0. Confirms PR-1 wasn't broken.

### Step 6.5: YAML lint of workflow files (self-CI parity)

- [ ] Run:

```bash
python3 -c "
import yaml
for f in [
    '.github/workflows/kibot-check.yaml',
    '.github/workflows/create-release-docs.yaml',
    '.github/workflows/create-debug-docs.yaml',
    '.github/workflows/auto-release.yaml',
    '.github/workflows/ci.yaml',
]:
    yaml.safe_load(open(f))
    print(f'OK: {f}')
"
```

- [ ] Expected: 5 `OK:` lines.

### Step 6.6: Branch summary

- [ ] Run:

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

- [ ] Expected: 6–7 commits on `fix/reactivation-bugs` (1 spec + 5 task commits + possibly plan commit). Diff stat shows changes in:
  - `docs/superpowers/specs/2026-06-04-pr2-reactivation-bugs.md` — new
  - `docs/superpowers/plans/2026-06-04-pr2-reactivation-bugs.md` — new
  - `scripts/inventree_sync/client.py` — modified
  - `scripts/inventree_sync/part_manager.py` — modified
  - `scripts/bom_export.py` — modified
  - `scripts/e2e_revision_handling.py` — new

### Step 6.7: Done

- [ ] Branch is ready for PR creation in a separate step.

---

## Akzeptanzkriterien (mirror of spec)

- [x] `find_part_by_name_and_revision` Helper existiert in `client.py` (Task 1).
- [x] PCB/Stencil/Assembly verwenden MAJOR.MINOR-Revision und re-usen existierende Parts (Task 2).
- [x] `populate_bom` ist idempotent (Task 3).
- [x] `find_part_by_name` nutzt `name=`-exact-Filter (Task 1).
- [x] `match_supplier_parts` nutzt batch `SKU__in=[...]` mit per-SKU-Fallback (Task 4).
- [x] Multi-SKU: ALLE SKUs werden als separate SupplierParts angelegt (Task 5).
- [x] `find_existing_part` iteriert alle SKUs für Lookup (Task 5).
- [x] `scripts/e2e_revision_handling.py` exists und exit 0 (Task 6).
- [x] `pytest scripts/tests/` weiterhin 64 grün (Task 6).
- [x] `py_compile` clean (Task 6).
