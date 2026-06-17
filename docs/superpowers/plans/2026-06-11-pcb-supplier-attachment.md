# PCB & SMT Stencil Supplier Attachment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bom_export.py` attach a `SupplierPart` (default JLCPCB) to every newly-created PCB and SMT Stencil Part — so re-order workflows in InvenTree just work.

**Architecture:** Three localized changes in `scripts/bom_export.py`: (1) a new `_ensure_fab_supplier_part` helper that idempotently links a Part to a Supplier with a derived SKU, (2) `create_pcb_part` and `create_stencil_part` gain a keyword-only `fab_supplier=None` argument and call the helper after Part create/reuse, (3) `main()` adds `--pcb-supplier` / `--no-pcb-supplier` CLI flags, resolves the supplier via `get_or_create_supplier`, and threads it through. `create_assembly_part` stays unchanged.

**Tech Stack:** Python 3.13, `inventree==0.23.1`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-11-pcb-supplier-attachment-design.md`

**Working directory for all commands:** `/home/pbuchegger/OE5XRX/HW-Module-CI`

---

## Conventions

- Tests use `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`.
- Activate venv before pytest: `source .venv/bin/activate && pytest <path>`.
- Commit messages: `feat(bom-export): <subject>` or `test(bom-export): <subject>`.
- TDD strict: failing test → confirm fail → implement → confirm pass → commit.

---

## File Structure

| File | Change |
|---|---|
| `scripts/bom_export.py` | New `_ensure_fab_supplier_part` helper; `create_pcb_part` / `create_stencil_part` accept `fab_supplier`; new constant `DEFAULT_PCB_SUPPLIER_NAME`; CLI flags `--pcb-supplier` / `--no-pcb-supplier`; `main()` resolves supplier and threads it through |
| `scripts/tests/test_bom_export_fab_supplier.py` | New file with 10 unit tests covering helper + create-function behaviour |

---

## Task 1: `_ensure_fab_supplier_part` helper

**Files:**
- Modify: `scripts/bom_export.py` (add helper near other Part-creation helpers, around line 240)
- Test: `scripts/tests/test_bom_export_fab_supplier.py` (new file with 5 helper tests)

- [ ] **Step 1: Write 5 failing tests**

Create `scripts/tests/test_bom_export_fab_supplier.py`:

```python
"""Unit tests for bom_export.py's fab-supplier-attachment helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bom_export import _ensure_fab_supplier_part  # noqa: E402


def _part(pk=100, name="v0.1 BusBoard PCB"):
    p = MagicMock()
    p.pk = pk
    p.name = name
    return p


def _supplier(pk=381, name="JLCPCB"):
    s = MagicMock()
    s.pk = pk
    s.name = name
    return s


def _supplier_part_mock(pk, sku):
    sp = MagicMock()
    sp.pk = pk
    sp.SKU = sku
    return sp


def test_ensure_fab_supplier_part_creates_when_missing():
    """No existing SupplierPart for (part, supplier) → create new."""
    api = MagicMock()
    part = _part(pk=1291)
    supplier = _supplier(pk=381)
    sku = "v0.1 BusBoard PCB rev 0.1"

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = []
        _ensure_fab_supplier_part(api, part, supplier, sku)

    SP.list.assert_called_once_with(api, part=1291, supplier=381)
    SP.create.assert_called_once()
    payload = SP.create.call_args[0][1]
    assert payload["part"] == 1291
    assert payload["supplier"] == 381
    assert payload["SKU"] == sku


def test_ensure_fab_supplier_part_skips_when_existing():
    """Matching SKU already linked → no new SupplierPart created."""
    api = MagicMock()
    part = _part(pk=1291)
    supplier = _supplier(pk=381)
    sku = "v0.1 BusBoard PCB rev 0.1"

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = [_supplier_part_mock(pk=500, sku=sku)]
        _ensure_fab_supplier_part(api, part, supplier, sku)

    SP.create.assert_not_called()


def test_ensure_fab_supplier_part_creates_when_existing_different_sku():
    """SP exists but for a different SKU (= different design rev) → create."""
    api = MagicMock()
    part = _part(pk=1291)
    supplier = _supplier(pk=381)

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = [
            _supplier_part_mock(pk=500, sku="OLD-DESIGN-PCB rev 0.0")
        ]
        _ensure_fab_supplier_part(api, part, supplier,
                                  "v0.1 BusBoard PCB rev 0.1")

    SP.create.assert_called_once()


def test_ensure_fab_supplier_part_list_failure_skips_gracefully():
    """API failure during list → no exception escapes, no create attempted."""
    api = MagicMock()
    part = _part()
    supplier = _supplier()

    with patch("bom_export.SupplierPart") as SP:
        SP.list.side_effect = ConnectionError("server down")
        _ensure_fab_supplier_part(api, part, supplier, "X")

    SP.create.assert_not_called()


def test_ensure_fab_supplier_part_create_failure_logged_not_raised():
    """API failure during create → logged, no exception escapes."""
    api = MagicMock()
    part = _part()
    supplier = _supplier()

    with patch("bom_export.SupplierPart") as SP:
        SP.list.return_value = []
        SP.create.side_effect = RuntimeError("conflict")
        # Must not raise:
        _ensure_fab_supplier_part(api, part, supplier, "X")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_bom_export_fab_supplier.py -v`
Expected: 5 fails with `ImportError: cannot import name '_ensure_fab_supplier_part'`.

- [ ] **Step 3: Implement the helper**

Open `scripts/bom_export.py`. Find the existing Part-creation helpers (around line 240, just before `def create_pcb_part`). Add the helper:

```python
def _ensure_fab_supplier_part(
    api: InvenTreeAPI,
    part: Part,
    supplier: Company,
    sku: str,
) -> None:
    """Idempotently link a Part to a fab Supplier with a derived SKU.

    Same defensive pattern as ``ensure_supplier_parts`` in
    ``inventree_sync/client.py``: list existing SupplierParts for
    ``(part, supplier)``, post-filter on SKU (the server-side filter
    has been observed unreliable on this InvenTree version), skip
    creation when a match already exists.

    Errors during list / create are logged and swallowed — fab-supplier
    linkage is best-effort metadata. The release artefacts (PCB Part,
    Assembly, BOM) are the primary outputs and must not fail because of
    a SupplierPart hiccup.
    """
    try:
        existing = SupplierPart.list(api, part=part.pk, supplier=supplier.pk)
    except Exception as exc:
        log.warning(
            "SupplierPart lookup for part=%s supplier=%s failed: %s; "
            "skipping fab linkage.", part.pk, supplier.pk, exc)
        return
    for sp in existing:
        if str(getattr(sp, "SKU", "") or "") == sku:
            log.info(
                "SupplierPart for part=%s supplier=%s SKU=%r already "
                "exists (pk=%s); skipping.",
                part.pk, supplier.pk, sku, sp.pk)
            return
    try:
        SupplierPart.create(api, {
            "part": part.pk,
            "supplier": supplier.pk,
            "SKU": sku,
        })
        log.info(
            "Linked SupplierPart for part=%s (%s) → %s SKU=%r",
            part.pk, part.name, supplier.name, sku)
    except Exception as exc:
        log.warning(
            "SupplierPart create failed for part=%s supplier=%s SKU=%r: "
            "%s; fab linkage skipped (add manually in the UI if needed).",
            part.pk, supplier.pk, sku, exc)
```

Also ensure `Company` is imported at the top of `bom_export.py`. Check the existing imports:
- `from inventree.company import SupplierPart` is already there (around line 22).
- Add `Company` to that import: `from inventree.company import Company, SupplierPart`.

- [ ] **Step 4: Run tests to verify pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_bom_export_fab_supplier.py -v -k "ensure_fab_supplier_part"`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/bom_export.py scripts/tests/test_bom_export_fab_supplier.py
git commit -m "feat(bom-export): _ensure_fab_supplier_part helper

Idempotently link a Part to a fab Supplier (JLCPCB by default) with a
derived SKU. Same defensive list-then-post-filter pattern as
ensure_supplier_parts in client.py. Failures during list / create are
logged and swallowed — fab linkage is best-effort metadata, never
blocks the release CI run.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: `create_pcb_part` / `create_stencil_part` attach the fab supplier

**Files:**
- Modify: `scripts/bom_export.py` (`create_pcb_part` ~line 246, `create_stencil_part` ~line 288)
- Test: `scripts/tests/test_bom_export_fab_supplier.py` (append 4 tests)

- [ ] **Step 1: Write 4 failing tests**

Append to `scripts/tests/test_bom_export_fab_supplier.py`:

```python
from bom_export import create_pcb_part, create_stencil_part, create_assembly_part  # noqa: E402


def _category(pk=10):
    c = MagicMock()
    c.pk = pk
    return c


def test_create_pcb_part_attaches_fab_supplier_on_new_part():
    """New PCB Part → helper called with SKU derived from name+revision."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1291, name="v0.1 BusBoard PCB")
    supplier = _supplier(pk=381, name="JLCPCB")

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)

        result = create_pcb_part(api, cat, "v0.1 BusBoard", "0.1",
                                 "/tmp/img.png", fab_supplier=supplier)

    assert result is new_part
    helper.assert_called_once_with(
        api, new_part, supplier, "v0.1 BusBoard PCB rev 0.1",
    )


def test_create_pcb_part_attaches_fab_supplier_on_reuse():
    """Existing PCB Part → helper STILL called (retroactive linkage)."""
    api = MagicMock()
    cat = _category()
    existing = _part(pk=999, name="v0.1 BusBoard PCB")
    supplier = _supplier()

    with patch("bom_export.find_part_by_name_and_revision",
               return_value=existing), \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        result = create_pcb_part(api, cat, "v0.1 BusBoard", "0.1",
                                 "/tmp/img.png", fab_supplier=supplier)

    assert result is existing
    helper.assert_called_once_with(
        api, existing, supplier, "v0.1 BusBoard PCB rev 0.1",
    )


def test_create_pcb_part_skips_fab_supplier_when_none():
    """fab_supplier=None → helper NOT called."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1291)

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)
        create_pcb_part(api, cat, "v0.1 BusBoard", "0.1",
                        "/tmp/img.png", fab_supplier=None)

    helper.assert_not_called()


def test_create_stencil_part_attaches_fab_supplier_on_new_part():
    """Stencil branch: SKU uses 'SMT Stencil' subtype."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1293, name="v0.1 BusBoard SMT Stencil")
    supplier = _supplier()

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)
        create_stencil_part(api, cat, "v0.1 BusBoard", "0.1",
                            "/tmp/img.png", fab_supplier=supplier)

    helper.assert_called_once_with(
        api, new_part, supplier, "v0.1 BusBoard SMT Stencil rev 0.1",
    )


def test_create_assembly_part_does_not_attach_fab_supplier():
    """Assembly Module never gets supplier (built in-house)."""
    api = MagicMock()
    cat = _category()
    new_part = _part(pk=1292, name="v0.1 BusBoard Module")

    with patch("bom_export.find_part_by_name_and_revision", return_value=None), \
         patch("bom_export.Part") as PART, \
         patch("bom_export._ensure_fab_supplier_part") as helper:
        PART.create.return_value = new_part
        new_part.uploadImage = MagicMock(return_value=True)
        create_assembly_part(api, cat, "v0.1 BusBoard", "0.1", "/tmp/img.png")

    helper.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest scripts/tests/test_bom_export_fab_supplier.py -v -k "create_"`
Expected: 5 fails — `create_pcb_part` doesn't accept `fab_supplier` kwarg yet.

- [ ] **Step 3: Modify the three create functions**

In `scripts/bom_export.py`, find `create_pcb_part` (around line 246). Add `fab_supplier` keyword-only parameter and the helper call:

```python
def create_pcb_part(
    api: InvenTreeAPI,
    category: PartCategory,
    name: str,
    version: str,
    image: str | None,
    *,
    fab_supplier: Optional[Company] = None,
) -> Part:
    full_name = f"{name} PCB"
    existing = find_part_by_name_and_revision(api, full_name, version)
    if existing is not None:
        log.info("Reusing existing PCB part '%s' rev %s (pk=%s)",
                 full_name, version, existing.pk)
        if fab_supplier is not None:
            _ensure_fab_supplier_part(
                api, existing, fab_supplier, f"{full_name} rev {version}",
            )
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
    if fab_supplier is not None:
        _ensure_fab_supplier_part(
            api, part, fab_supplier, f"{full_name} rev {version}",
        )
    return part
```

Do the analogous change in `create_stencil_part` (around line 288). Keep `create_assembly_part` unchanged.

Make sure `Optional` is imported at the top of `bom_export.py` (it likely is — verify).

- [ ] **Step 4: Run tests to verify pass**

Run: `source .venv/bin/activate && pytest scripts/tests/test_bom_export_fab_supplier.py -v`
Expected: all 10 pass (5 helper + 5 create-function tests).

- [ ] **Step 5: Run the whole suite for regression check**

Run: `source .venv/bin/activate && pytest scripts/tests/`
Expected: previous 211 passed + 10 new = 221 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/bom_export.py scripts/tests/test_bom_export_fab_supplier.py
git commit -m "feat(bom-export): create_pcb_part / create_stencil_part attach fab supplier

Both create functions gain a keyword-only fab_supplier=None argument.
When non-None, _ensure_fab_supplier_part is called after Part create OR
reuse — the reuse path enables retroactive linkage by re-running
bom_export.py on releases that predate this change.

SKU is derived as f'{full_name} rev {version}', stable per design
revision, idempotent across re-runs.

create_assembly_part is intentionally NOT modified: the assembly is
built in-house, no external supplier applies.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: CLI flags + `main()` wiring

**Files:**
- Modify: `scripts/bom_export.py` (`parse_args` around line 418, `main()` around line 469)

- [ ] **Step 1: Add the constant and CLI flags**

In `scripts/bom_export.py`, near the top after the existing category-name constants (around line 38):

```python
# Default fab supplier name. CLI --pcb-supplier overrides per-run; the
# Company is auto-created via get_or_create_supplier if missing.
DEFAULT_PCB_SUPPLIER_NAME = "JLCPCB"
```

In `parse_args` (around line 418), add the two CLI flags inside the parser block. Place them between the `--categories` and `--planned-builds` flags:

```python
    parser.add_argument(
        "--pcb-supplier",
        default=DEFAULT_PCB_SUPPLIER_NAME,
        help=(
            "Company name of the fab that produces the PCB + SMT stencil "
            f"(default: {DEFAULT_PCB_SUPPLIER_NAME!r}). The Company is "
            "auto-created if missing. Use --no-pcb-supplier to skip "
            "SupplierPart linkage entirely."
        ),
    )
    parser.add_argument(
        "--no-pcb-supplier",
        dest="pcb_supplier", action="store_const", const=None,
        help="Skip SupplierPart linkage on PCB + SMT stencil parts.",
    )
```

- [ ] **Step 2: Resolve the supplier and thread through `main()`**

In `main()` (around line 469), right after `api = InvenTreeAPI()` and the existing `reporter = ...` line, add:

```python
    fab_supplier: Optional[Company] = None
    if args.pcb_supplier is not None:
        fab_supplier = get_or_create_supplier(api, name=args.pcb_supplier)
        if fab_supplier is None:
            log.error(
                "Could not get or create fab supplier %r — proceeding "
                "without SupplierPart linkage.", args.pcb_supplier)
```

Then find the calls to `create_pcb_part`, `create_assembly_part`, `create_stencil_part` (around line 556-558) and pass `fab_supplier=fab_supplier` to the PCB and stencil ones (assembly stays as-is):

```python
    pcb      = create_pcb_part(
        api, pcb_cat, args.name, args.version, args.pcb_image,
        fab_supplier=fab_supplier,
    )
    assembly = create_assembly_part(api, assembly_cat, args.name, args.version, args.assembly_image)
    stencil  = create_stencil_part(
        api, stencil_cat, args.name, args.version, args.stencil_image,
        fab_supplier=fab_supplier,
    )
```

The dry-run branch (the long `if reporter is not None:` block around line 482-540) doesn't currently model SupplierPart decisions. Leave it as-is for this PR; dry-run accurately reports PCB/Assembly/Stencil decisions but is silent on the SupplierPart side. That's an acceptable known limitation — the helper is best-effort anyway. Note this in a code comment near the dry-run block:

```python
    if reporter is not None:
        # Dry-run path: record decisions, skip side-effecting operations
        # ...
        # Note: SupplierPart linkage (--pcb-supplier) is NOT modelled in
        # the dry-run report. The helper itself is best-effort
        # (failures are logged, never raised), so dry-run silence is
        # acceptable. Real-run output covers it via the helper's
        # info/warning log lines.
```

- [ ] **Step 3: Verify `--help` still works and shows the new flags**

Run: `source .venv/bin/activate && python3 scripts/bom_export.py --help`
Expected: usage text includes `--pcb-supplier PCB_SUPPLIER` and `--no-pcb-supplier`, no import errors.

- [ ] **Step 4: Run the whole suite**

Run: `source .venv/bin/activate && pytest scripts/tests/`
Expected: 221 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/bom_export.py
git commit -m "feat(bom-export): --pcb-supplier / --no-pcb-supplier CLI flags

main() resolves the fab supplier Company via get_or_create_supplier
when --pcb-supplier is set (default: JLCPCB). The resolved Company is
threaded into create_pcb_part and create_stencil_part as a keyword
argument. create_assembly_part is intentionally not touched (assembly
is built in-house).

--no-pcb-supplier opts out: PCB+stencil Parts get no SupplierPart
attached, matching pre-PR behaviour for users who don't want this.

Dry-run path doesn't model SupplierPart decisions — the linkage is
best-effort metadata, real-run logs cover the outcome.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- ✅ Goal 1 (attach SupplierPart to PCB+Stencil) → Task 2.
- ✅ Goal 2 (CLI flag selects supplier) → Task 3.
- ✅ Goal 3 (derived SKU = `{full_name} rev {version}`) → Task 2 implementation.
- ✅ Goal 4 (idempotency) → Task 1 helper.
- ✅ Goal 5 (`--no-pcb-supplier` opt-out) → Task 3.
- ✅ Goal 6 (Assembly unchanged) → Task 2 explicit test + commit message.
- ✅ Goal 7 (no unrelated changes) → File list scope.

**Placeholder scan:** no TBD/TODO. All code blocks contain concrete code.

**Type consistency:**
- `_ensure_fab_supplier_part(api, part, supplier, sku) -> None` — used consistently in tests and create-function calls.
- `fab_supplier: Optional[Company] = None` keyword-only — same signature in `create_pcb_part`, `create_stencil_part`. `create_assembly_part` does NOT add this param (verified in Goal 6 test).
- `DEFAULT_PCB_SUPPLIER_NAME: str` constant referenced in argparse default + help string.

No gaps found.
