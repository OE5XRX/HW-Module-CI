# PR-3: Rich Part Data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich every InvenTree-Part that bom_export.py creates with parameters (LCSC+Mouser), supplier-product-page links, and KiBot-fabrication-file attachments. Re-activate the bom_export step in the release workflow.

**Architecture:** Two new tiny helpers in `inventree_sync/client.py` (`upload_parameters`, `_supplier_url`) wired into the existing create/ensure flows. Extend `MouserFetcher` to parse `ProductAttributes`. New module `inventree_sync/attachments.py` does glob-based auto-discovery in a KiBot output directory and uploads idempotently. `bom_export.py` gains a `--output_dir` CLI arg. Workflow YAML re-enables the dormant InvenTree-sync step.

**Tech Stack:** Python 3.x, `requests` 2.34, `inventree` 0.23.1, KiBot outputs (filesystem), GitHub Actions YAML. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md`](../specs/2026-06-04-pr3-rich-part-data.md)

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `scripts/inventree_sync/fetchers.py` | Modify | `MouserFetcher._parse_attributes` populates `PartData.parameters` |
| `scripts/inventree_sync/client.py` | Modify | New `_find_or_create_parameter_template`, `upload_parameters`, `_supplier_url`; SupplierPart-Create-Payloads get `link`; `create_part_in_inventree`/`ensure_supplier_parts` call `upload_parameters` |
| `scripts/inventree_sync/attachments.py` | Create | `attach_kibot_outputs(api, pcb, assembly, stencil, output_dir)` + glob mapping + idempotency |
| `scripts/bom_export.py` | Modify | `--output_dir` CLI arg; after `populate_bom`, call `attach_kibot_outputs` |
| `scripts/tests/test_mouser_attributes.py` | Create | Pytest unit test for `_parse_attributes` (no network) |
| `scripts/e2e_revision_handling.py` | Modify | 3 new tests: parameter delta-sync, supplier link, attachment idempotency |
| `.github/workflows/create-release-docs.yaml` | Modify | Un-comment the bom_export step + add `--output_dir output/` arg |

---

## Task 1: Mouser `_parse_attributes` + pytest unit test

**Files:**
- Create: `scripts/tests/test_mouser_attributes.py`
- Modify: `scripts/inventree_sync/fetchers.py` (`MouserFetcher` class)

### Step 1.1: Write failing pytest

- [ ] Create `scripts/tests/test_mouser_attributes.py` with:

```python
"""Pytest unit tests for MouserFetcher._parse_attributes — no network access."""

from inventree_sync.fetchers import MouserFetcher


def test_parse_attributes_basic():
    """Mouser ProductAttributes → params dict."""
    product = {
        "ProductAttributes": [
            {"AttributeName": "Resistance", "AttributeValue": "10 kOhms"},
            {"AttributeName": "Tolerance", "AttributeValue": "1 %"},
            {"AttributeName": "Voltage Rating DC", "AttributeValue": "50 V"},
        ],
    }
    result = MouserFetcher._parse_attributes(product)
    assert result == {
        "Resistance": "10 kOhms",
        "Tolerance": "1 %",
        "Voltage Rating DC": "50 V",
    }


def test_parse_attributes_missing_field():
    """Product without ProductAttributes returns empty dict."""
    assert MouserFetcher._parse_attributes({}) == {}
    assert MouserFetcher._parse_attributes({"ProductAttributes": None}) == {}
    assert MouserFetcher._parse_attributes({"ProductAttributes": []}) == {}


def test_parse_attributes_empty_or_whitespace():
    """Skip rows with empty/whitespace name or value, and strip both."""
    product = {
        "ProductAttributes": [
            {"AttributeName": "  Resistance  ", "AttributeValue": "  10kΩ  "},
            {"AttributeName": "", "AttributeValue": "ignored"},
            {"AttributeName": "ignored2", "AttributeValue": ""},
            {"AttributeName": "Tolerance", "AttributeValue": None},
            {"AttributeName": None, "AttributeValue": "x"},
        ],
    }
    result = MouserFetcher._parse_attributes(product)
    assert result == {"Resistance": "10kΩ"}


def test_parse_attributes_duplicate_name_last_wins():
    """If Mouser returns the same name twice, last value wins."""
    product = {
        "ProductAttributes": [
            {"AttributeName": "Resistance", "AttributeValue": "10 kOhms"},
            {"AttributeName": "Resistance", "AttributeValue": "10.1 kOhms"},
        ],
    }
    assert MouserFetcher._parse_attributes(product) == {"Resistance": "10.1 kOhms"}
```

### Step 1.2: Run pytest — expect 4 fails

- [ ] Run:

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
source .venv/bin/activate
pytest scripts/tests/test_mouser_attributes.py -v
```

- [ ] Expected: 4 tests fail with `AttributeError: type object 'MouserFetcher' has no attribute '_parse_attributes'`. If any other error, STOP and report BLOCKED.

### Step 1.3: Add `_parse_attributes` + wire into `fetch()`

- [ ] In `scripts/inventree_sync/fetchers.py`, add this method to the `MouserFetcher` class, inserting it between `fetch` and `_parse_price` (or any consistent location inside the class):

```python
    @staticmethod
    def _parse_attributes(product: dict) -> dict[str, str]:
        """Extract parameters from Mouser ProductAttributes list.

        Mouser API v2 returns attributes as a list of {"AttributeName": str,
        "AttributeValue": str} pairs.  We strip both sides and skip empty
        rows.  If a name appears multiple times, the last value wins
        (Mouser does emit duplicates occasionally for unit-aware fields).
        """
        params: dict[str, str] = {}
        for attr in product.get("ProductAttributes") or []:
            name = (attr.get("AttributeName") or "").strip()
            value = (attr.get("AttributeValue") or "").strip()
            if not name or not value:
                continue
            params[name] = value
        return params
```

- [ ] In the same file, in `MouserFetcher.fetch`, find the `return PartData(...)` block (around line 236 in the current file). Add `parameters=self._parse_attributes(p),` as a new keyword argument. The block should now look like:

```python
        return PartData(
            mpn=p.get("ManufacturerPartNumber", ""),
            manufacturer=p.get("Manufacturer", ""),
            description=description,
            image_url=p.get("ImagePath", ""),
            datasheet_url=p.get("DataSheetUrl", ""),
            mouser_sku=mouser_sku,
            category_path=category_path,
            price_breaks=price_breaks,
            currency=currency,
            parameters=self._parse_attributes(p),
        )
```

### Step 1.4: Run pytest — expect 4 pass

- [ ] Run:

```bash
pytest scripts/tests/test_mouser_attributes.py -v
```

- [ ] Expected: 4 passed.

### Step 1.5: Full pytest sweep — no regressions

- [ ] Run:

```bash
pytest scripts/tests/ -q
```

- [ ] Expected: `68 passed` (the previous 64 + 4 new).

### Step 1.6: Commit

- [ ] Run:

```bash
git add scripts/tests/test_mouser_attributes.py scripts/inventree_sync/fetchers.py
git commit -m "$(cat <<'EOF'
feat(fetchers): Mouser _parse_attributes → PartData.parameters

Backlog #6 (part 1): Mouser API v2 returns parameter data in
ProductAttributes which we ignored. New static _parse_attributes()
method extracts (name, value) pairs into the existing
PartData.parameters dict — same format LCSCFetcher already populates,
so the downstream merge logic in _fetch_and_merge picks Mouser as a
fallback for parts where LCSC has no params.

Unit tested with 4 pytest cases covering: basic mapping, missing/None/
empty fields, whitespace stripping, duplicate-name-last-wins.

Refs: docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md
EOF
)"
```

---

## Task 2: `upload_parameters` + `_find_or_create_parameter_template` helpers + E2E

**Files:**
- Modify: `scripts/inventree_sync/client.py` (add two new helpers)
- Modify: `scripts/e2e_revision_handling.py` (new test + helper)

### Step 2.1: Write failing E2E test

- [ ] In `scripts/e2e_revision_handling.py`, add this import near the existing inventree imports:

```python
from inventree.part import BomItem, Part, PartCategory, PartParameter, PartParameterTemplate
```

(Add `PartParameter, PartParameterTemplate` to the existing line.)

- [ ] In the same file, ADD a new test function AFTER `test_multi_sku_supplier_parts`:

```python
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
```

- [ ] In the same file, BEFORE the test functions, add this helper (alongside `_ensure_category`, near line 90):

```python
def _params_by_name(api: InvenTreeAPI, part: Part) -> dict[str, str]:
    """Return {template_name: data} for all PartParameters on *part*."""
    params = PartParameter.list(api, part=part.pk)
    out: dict[str, str] = {}
    for p in params:
        try:
            tpl = PartParameterTemplate(api, pk=int(p.template))
            out[tpl.name] = p.data
        except Exception:
            out[f"<pk={p.template}>"] = p.data
    return out
```

- [ ] Add `test_parameter_sync_delta` to the test-case tuple in `main()`:

```python
        for tc in (test_find_part_by_name_exact,
                   test_find_part_by_name_and_revision,
                   test_pcb_silently_reuse,
                   test_stencil_silently_reuse,
                   test_assembly_silently_reuse,
                   test_bom_idempotent,
                   test_multi_sku_supplier_parts,
                   test_parameter_sync_delta):
```

### Step 2.2: Run — expect `test_parameter_sync_delta` to ERROR

- [ ] Run:

```bash
source ~/.inventree_test.env
source .venv/bin/activate
python3 -m py_compile scripts/e2e_revision_handling.py
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: 7/7 previous tests PASS; `test_parameter_sync_delta` ERROR with `ImportError: cannot import name 'upload_parameters' from 'inventree_sync.client'`.

### Step 2.3: Add helpers to `client.py`

- [ ] In `scripts/inventree_sync/client.py`, near the existing `from inventree.part import Part, PartCategory` line, add `PartParameter, PartParameterTemplate`:

```python
from inventree.part import Part, PartCategory, PartParameter, PartParameterTemplate
```

- [ ] In the same file, find the END of `find_part_by_name_and_revision` (around line 367). After that function (BEFORE `ensure_supplier_parts` which is the next function), insert these two helpers:

```python
def _find_or_create_parameter_template(
    api: InvenTreeAPI, name: str
) -> Optional[PartParameterTemplate]:
    """Find PartParameterTemplate by exact name, create if missing.

    Idempotent: same defensive post-filter pattern as `find_part_by_name`
    because this InvenTree server version silently ignores ``name=``.
    """
    if not name:
        return None
    try:
        candidates = [
            t for t in PartParameterTemplate.list(api, name=name)
            if t.name == name
        ]
        if candidates:
            return candidates[0]
        return PartParameterTemplate.create(api, {"name": name})
    except Exception as exc:
        logger.warning(
            "PartParameterTemplate find-or-create failed for %r: %s", name, exc)
        return None


def upload_parameters(
    api: InvenTreeAPI, part: Part, params: dict[str, str]
) -> None:
    """Delta-sync a parameter dict to an InvenTree Part.

    Behavior per PR-3 spec:
      - For each (name, value) in *params*: find/create the
        PartParameterTemplate and create-or-update the PartParameter.
      - Keys NOT present in *params* are left untouched on *part*
        (delta-sync, not full replacement).
      - Supplier is source of truth for keys IN *params* — any manual
        UI edit to those keys is overwritten.

    Errors per parameter are logged and skipped so a single bad template
    can't break the whole sync.
    """
    if not params:
        return
    for name, value in params.items():
        if not name or value is None or value == "":
            continue
        template = _find_or_create_parameter_template(api, name)
        if template is None:
            continue
        try:
            existing = PartParameter.list(api, part=part.pk, template=template.pk)
        except Exception as exc:
            logger.warning(
                "PartParameter lookup failed for part=%s template=%s: %s",
                part.pk, template.pk, exc)
            continue
        try:
            if existing:
                existing[0].save({"data": value})
            else:
                PartParameter.create(api, {
                    "part": part.pk,
                    "template": template.pk,
                    "data": value,
                })
        except Exception as exc:
            logger.warning(
                "PartParameter save/create failed for part=%s template=%r: %s",
                part.pk, name, exc)
```

### Step 2.4: Syntax check

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/client.py scripts/e2e_revision_handling.py
```

- [ ] Expected: no output.

### Step 2.5: Run E2E — expect 8/8 PASS

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: 8 PASS lines, the new one being `parameter sync delta ({'Resistance': '11kΩ', 'Tolerance': '1%', 'Voltage': '50V'})`. Exit 0.

### Step 2.6: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/client.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): upload_parameters + delta-sync semantics

Backlog #6 (part 2): new helpers in client.py for syncing a
parameter dict to an InvenTree Part.

upload_parameters() iterates the dict, find-or-creates a
PartParameterTemplate per name, and create-or-updates the
PartParameter. Delta semantics: keys NOT in the dict are left
untouched on the Part — user-added Custom-Parameters survive a sync,
only Supplier-provided keys get overwritten.

_find_or_create_parameter_template() uses the same defensive post-
filter pattern as find_part_by_name since this InvenTree server
silently ignores name= filters.

Per-parameter try/except — one bad template doesn't break the whole
sync. Wiring into create_part_in_inventree + ensure_supplier_parts
follows in Task 3.

E2E test_parameter_sync_delta exercises the overwrite + leave-alone
semantics against the real server.

Refs: docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md
EOF
)"
```

---

## Task 3: Wire `upload_parameters` into create + ensure flows

**Files:**
- Modify: `scripts/inventree_sync/client.py` (`create_part_in_inventree`, `ensure_supplier_parts`)

### Step 3.1: Wire into `create_part_in_inventree`

- [ ] In `scripts/inventree_sync/client.py`, find `create_part_in_inventree`. Locate the `return part` statement at the bottom of the function. **Immediately before** the `return part`, ADD:

```python
    # 6. Parameters (LCSC + Mouser merged in part_data.parameters)
    if part_data.parameters:
        upload_parameters(api, part, part_data.parameters)
```

The end of the function should now look like:

```python
            except Exception as exc:
                logger.warning("Mouser SupplierPart creation failed (%s): %s", sku, exc)

    # 6. Parameters (LCSC + Mouser merged in part_data.parameters)
    if part_data.parameters:
        upload_parameters(api, part, part_data.parameters)

    return part
```

### Step 3.2: Wire into `ensure_supplier_parts`

- [ ] In the same file, find `ensure_supplier_parts`. Locate the final closing of the Mouser-supplier loop. **After the entire `if mouser_supplier:` block and before the function ends**, ADD:

```python
    # Sync parameters on re-sync too — keeps existing Parts current.
    if part_data.parameters:
        upload_parameters(api, part, part_data.parameters)
```

Trace: this is the function body after the two supplier loops; it currently ends with the Mouser `if mouser_supplier:` block. Add the new block right after that.

### Step 3.3: Syntax + E2E (already-existing test still passes)

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/*.py
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: no output from py_compile; E2E 8/8 PASS still. (No new test here; this task just wires existing helper into existing flow.)

### Step 3.4: pytest still green

- [ ] Run:

```bash
pytest scripts/tests/ -q
```

- [ ] Expected: 68 passed.

### Step 3.5: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/client.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): wire upload_parameters into create+ensure flows

Backlog #6 (part 3): create_part_in_inventree and ensure_supplier_parts
now call upload_parameters(api, part, part_data.parameters) so every
Part — newly-created or re-synced — gets its supplier-side parameters
landed in InvenTree.

The fetchers populate part_data.parameters today already (LCSC since
day-one, Mouser since Task 1 of this PR). The merge in _fetch_and_merge
already prefers LCSC and falls back to Mouser, so a multi-supplier
part gets the LCSC view by default.

Refs: docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md
EOF
)"
```

---

## Task 4: `_supplier_url` helper + populate `link` + E2E

**Files:**
- Modify: `scripts/inventree_sync/client.py` (new helper + 4 SupplierPart.create payloads)
- Modify: `scripts/e2e_revision_handling.py` (new test)

### Step 4.1: Add failing E2E test

- [ ] In `scripts/e2e_revision_handling.py`, add this test function AFTER `test_parameter_sync_delta`:

```python
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
```

- [ ] Add `test_supplier_link_populated` to the test-case tuple in `main()`:

```python
        for tc in (test_find_part_by_name_exact,
                   test_find_part_by_name_and_revision,
                   test_pcb_silently_reuse,
                   test_stencil_silently_reuse,
                   test_assembly_silently_reuse,
                   test_bom_idempotent,
                   test_multi_sku_supplier_parts,
                   test_parameter_sync_delta,
                   test_supplier_link_populated):
```

### Step 4.2: Run — expect FAIL on the link assertions

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: 8/8 previous PASS, `test_supplier_link_populated` FAILS with `assert by_sku[lcsc_sku].link == ...` because `link` is currently empty.

### Step 4.3: Add `_supplier_url` helper

- [ ] In `scripts/inventree_sync/client.py`, find the END of `_find_or_create_parameter_template` (added in Task 2). **Immediately before** the start of `upload_parameters`, INSERT:

```python
def _supplier_url(supplier_name: str, sku: str) -> str:
    """Construct a stable product-page URL from supplier name + SKU.

    Pattern-based (not API-based) so this is robust against supplier-
    API schema changes.  Unknown suppliers return "" — caller passes
    that to ``SupplierPart.link`` unchanged, leaving the field empty.
    """
    name = (supplier_name or "").lower()
    sku = (sku or "").strip()
    if not sku:
        return ""
    if "lcsc" in name:
        return f"https://www.lcsc.com/product-detail/{sku}.html"
    if "mouser" in name:
        return f"https://www.mouser.com/ProductDetail/{sku}"
    return ""
```

### Step 4.4: Populate `link` in all 4 SupplierPart.create payloads

- [ ] In the same file, find `create_part_in_inventree`. There is an LCSC loop and a Mouser loop, each calling `SupplierPart.create`. Add `"link": _supplier_url(lcsc_supplier.name, sku),` to the LCSC payload and `"link": _supplier_url(mouser_supplier.name, sku),` to the Mouser payload.

The LCSC block should look like:

```python
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
                    "link": _supplier_url(lcsc_supplier.name, sku),
                })
                if part_data.price_breaks:
                    _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
            except Exception as exc:
                logger.warning("LCSC SupplierPart creation failed (%s): %s", sku, exc)
```

The Mouser block should look like:

```python
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
                    "link": _supplier_url(mouser_supplier.name, sku),
                })
                if attach_mouser_prices:
                    _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
            except Exception as exc:
                logger.warning("Mouser SupplierPart creation failed (%s): %s", sku, exc)
```

- [ ] Same treatment for `ensure_supplier_parts` in the same file (both LCSC and Mouser create-calls). The LCSC block in `ensure_supplier_parts`:

```python
    if lcsc_supplier:
        for sku in lcsc_skus:
            if not sku or sku in existing_skus:
                continue
            try:
                sp = SupplierPart.create(api, {
                    "part": part.pk,
                    "supplier": lcsc_supplier.pk,
                    "SKU": sku,
                    "link": _supplier_url(lcsc_supplier.name, sku),
                })
                existing_skus.add(sku)
                if part_data.price_breaks:
                    _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
            except Exception as exc:
                logger.warning("Could not add LCSC supplier part %s: %s", sku, exc)
```

The Mouser block in `ensure_supplier_parts`:

```python
    if mouser_supplier:
        # Mirror create_part_in_inventree: Mouser prices only when no LCSC
        # SKU contributed (LCSC is the primary price source when present).
        attach_mouser_prices = part_data.price_breaks and not lcsc_skus
        for sku in mouser_skus:
            if not sku or sku in existing_skus:
                continue
            try:
                sp = SupplierPart.create(api, {
                    "part": part.pk,
                    "supplier": mouser_supplier.pk,
                    "SKU": sku,
                    "link": _supplier_url(mouser_supplier.name, sku),
                })
                existing_skus.add(sku)
                if attach_mouser_prices:
                    _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
            except Exception as exc:
                logger.warning("Could not add Mouser supplier part %s: %s", sku, exc)
```

### Step 4.5: Syntax + E2E (9/9 PASS)

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/*.py
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: all 9 tests PASS including `supplier link populated (LCSC + Mouser)`.

### Step 4.6: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/client.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): populate SupplierPart.link via _supplier_url

Backlog #7: every SupplierPart created via create_part_in_inventree or
ensure_supplier_parts now gets the link field populated with a
pattern-constructed URL — LCSC product-detail page or Mouser
ProductDetail page. One link per SKU.

URL templates are pattern-based (not API-fetched) so they survive
supplier-API schema changes. Unknown suppliers default to "" so the
field stays unset rather than mis-linking.

PartData.supplier_link stays unused — kept in the dataclass for
backwards compat with any external consumers; cleanup is its own PR.

Refs: docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md
EOF
)"
```

---

## Task 5: `attachments.py` module + E2E test

**Files:**
- Create: `scripts/inventree_sync/attachments.py`
- Modify: `scripts/e2e_revision_handling.py` (new test)

### Step 5.1: Write failing E2E test

- [ ] In `scripts/e2e_revision_handling.py`, add this test function AFTER `test_supplier_link_populated`:

```python
def test_attachment_idempotent(api: InvenTreeAPI) -> None:
    """attach_kibot_outputs(): idempotent — second call adds nothing."""
    import tempfile
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
```

- [ ] Add `test_attachment_idempotent` to the test-case tuple in `main()`:

```python
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
```

### Step 5.2: Run — expect ImportError

- [ ] Run:

```bash
python3 -m py_compile scripts/e2e_revision_handling.py
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: 9 previous tests PASS; `test_attachment_idempotent` ERRORS with `ImportError: cannot import name 'attach_kibot_outputs' from 'inventree_sync.attachments'`.

### Step 5.3: Create `scripts/inventree_sync/attachments.py`

- [ ] Create the file with this exact content:

```python
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
            names = {a.filename for a in target.getAttachments()}
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
```

### Step 5.4: Syntax + E2E (10/10 PASS)

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/*.py scripts/e2e_revision_handling.py
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: all 10 tests PASS including `attachment idempotent (total=9, second run no-op)`. Exit 0.

### Step 5.5: pytest still green

- [ ] Run:

```bash
pytest scripts/tests/ -q
```

- [ ] Expected: `68 passed`.

### Step 5.6: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/attachments.py scripts/e2e_revision_handling.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): attach_kibot_outputs auto-discovery + idempotency

Backlog #8 + #12: new scripts/inventree_sync/attachments.py module.

attach_kibot_outputs(api, pcb, assembly, stencil, output_dir) walks
*output_dir* with a small mapping of (glob, target_part, comment)
triples and uploads each match via Part.uploadAttachment(). The
three image files already used as Part.image are NOT in the mapping,
so they don't get double-attached.

Idempotent: before each upload, the function checks the target Part's
existing attachments; matching basenames are skipped with a log line.

Per-file try/except so one bad file doesn't break the whole sync.
Summary log at the end: 'N uploaded, M skipped, K patterns with no
match' — surfaces KiBot-output-pattern drift quickly.

E2E test_attachment_idempotent verifies the full flow against the
real server with a synthetic tempdir of KiBot-shaped files.

Wiring into bom_export.py (CLI arg + actual call) follows in Task 6.

Refs: docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md
EOF
)"
```

---

## Task 6: `bom_export.py --output_dir` + workflow YAML reactivation

**Files:**
- Modify: `scripts/bom_export.py`
- Modify: `.github/workflows/create-release-docs.yaml`

### Step 6.1: Add CLI arg to `bom_export.py`

- [ ] In `scripts/bom_export.py`, at the top of the file, **add** this import near the existing `from inventree_sync.client import find_part_by_name_and_revision`:

```python
from inventree_sync.attachments import attach_kibot_outputs
```

- [ ] In `scripts/bom_export.py`, find `parse_args` (around line 278). Locate the `--stencil_image` argument. **After** it, ADD:

```python
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
```

### Step 6.2: Call `attach_kibot_outputs` after `populate_bom`

- [ ] In `scripts/bom_export.py`, find `main()`. Locate the existing call `populate_bom(api, assembly, pcb, entries)` (toward the end). **Immediately after** that call, ADD:

```python
    if args.output_dir:
        attach_kibot_outputs(api, pcb, assembly, stencil, args.output_dir)
```

(`pcb`, `assembly`, and `stencil` are already in scope from `create_*_part` calls earlier in `main()`.)

### Step 6.3: Syntax check + run pytest

- [ ] Run:

```bash
python3 -m py_compile scripts/bom_export.py
pytest scripts/tests/ -q
```

- [ ] Expected: no output from py_compile; `68 passed`.

### Step 6.4: Re-activate the workflow step

- [ ] Open `.github/workflows/create-release-docs.yaml`. Find the commented-out block that starts with `# - name: Generate parts on InvenTree server` (a few lines above the end of the `build:` job's steps).

- [ ] **Uncomment** the entire block: remove the leading `# ` (or `#` followed by a single space) on each line of the block. The block currently looks like (abbreviated):

```yaml
      # - name: Generate parts on InvenTree server
      #   if: always()
      #   continue-on-error: true
      #   shell: bash
      #   env:
      #     INVENTREE_API_TOKEN: ${{ secrets.INVENTREE_API_TOKEN }}
      #     INVENTREE_API_HOST:  ${{ secrets.INVENTREE_API_HOST }}
      #     MOUSER_API_KEY:      ${{ secrets.MOUSER_API_KEY }}
      #   run: |
      #     set -euo pipefail
      #     pip install -r _ci/scripts/requirements.txt
      #     python3 _ci/scripts/bom_export.py \
      #       --csv_file       "output/${{ steps.setup.outputs.project-name }}-bom.csv" \
      #       --name           "${{ github.event.repository.name }}" \
      #       --version        "${{ github.ref_name }}" \
      #       --pcb_image      "output/${{ steps.setup.outputs.project-name }}-3D_top-without.png" \
      #       --assembly_image "output/${{ steps.setup.outputs.project-name }}-3D_top-with.png" \
      #       --stencil_image  "output/${{ steps.setup.outputs.project-name }}-stencil_top.png"
```

After un-commenting AND appending the new `--output_dir` arg, it should look like:

```yaml
      - name: Generate parts on InvenTree server
        if: always()
        continue-on-error: true
        shell: bash
        env:
          INVENTREE_API_TOKEN: ${{ secrets.INVENTREE_API_TOKEN }}
          INVENTREE_API_HOST:  ${{ secrets.INVENTREE_API_HOST }}
          MOUSER_API_KEY:      ${{ secrets.MOUSER_API_KEY }}
        run: |
          set -euo pipefail
          pip install -r _ci/scripts/requirements.txt
          python3 _ci/scripts/bom_export.py \
            --csv_file       "output/${{ steps.setup.outputs.project-name }}-bom.csv" \
            --name           "${{ github.event.repository.name }}" \
            --version        "${{ github.ref_name }}" \
            --pcb_image      "output/${{ steps.setup.outputs.project-name }}-3D_top-without.png" \
            --assembly_image "output/${{ steps.setup.outputs.project-name }}-3D_top-with.png" \
            --stencil_image  "output/${{ steps.setup.outputs.project-name }}-stencil_top.png" \
            --output_dir     "output/"
```

Verify indentation is consistent with surrounding steps (the leading 6 spaces for `- name:`, 8 spaces for the field names).

### Step 6.5: YAML lint sweep

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

- [ ] Expected: 5 `OK:` lines. If any fail with a YAML parse error, the un-comment introduced an indentation bug — fix and re-run.

### Step 6.6: Final E2E run (still 10/10 PASS)

- [ ] Run:

```bash
source ~/.inventree_test.env
source .venv/bin/activate
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: 10/10 PASS, exit 0. (No new E2E here — bom_export.py CLI changes can't be exercised from e2e without a real BOM CSV, so the integration is verified by the YAML-lint above and the per-helper E2E tests already in place.)

### Step 6.7: Commit

- [ ] Run:

```bash
git add scripts/bom_export.py .github/workflows/create-release-docs.yaml
git commit -m "$(cat <<'EOF'
feat(release-docs): re-enable bom_export step + --output_dir attachment auto-discovery

Backlog #8 (final wiring): bom_export.py gets a new optional
--output_dir CLI argument; when given, attach_kibot_outputs() is
invoked after populate_bom() to upload every matched KiBot artifact
to the relevant Parts.

create-release-docs.yaml: the InvenTree-sync step has been
commented out since the OE5XRX-InvenTree-Server dekommissionierung
(05/2026, see README gotcha). Reactivated, with --output_dir output/
appended so the new attachment flow runs on every release.

Org-secrets INVENTREE_API_HOST / INVENTREE_API_TOKEN / MOUSER_API_KEY
must point at the operational InvenTree server before the first
release merges this change.

Refs: docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md
EOF
)"
```

---

## Task 7: Final Verification

**Files:** no code changes — verification only.

### Step 7.1: Full py_compile sweep

- [ ] Run:

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
source ~/.inventree_test.env
source .venv/bin/activate
python3 -m py_compile \
  scripts/inventree_sync/*.py \
  scripts/bom_export.py \
  scripts/e2e_revision_handling.py \
  scripts/e2e_image_upload.py \
  scripts/probe_supplier_images.py
```

- [ ] Expected: no output.

### Step 7.2: Pytest sweep

- [ ] Run:

```bash
pytest scripts/tests/ -q
```

- [ ] Expected: `68 passed` (64 previous + 4 new Mouser-attribute tests).

### Step 7.3: Full E2E run

- [ ] Run:

```bash
python3 scripts/e2e_revision_handling.py
```

- [ ] Expected: 10/10 PASS, exit 0, cleanup happens.

### Step 7.4: PR-1 regression check

- [ ] Run:

```bash
python3 scripts/probe_supplier_images.py
```

- [ ] Expected: 4/4 PASS, exit 0.

### Step 7.5: Self-CI YAML lint

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

### Step 7.6: Branch summary

- [ ] Run:

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

- [ ] Expected: 8 commits on `feat/rich-part-data` (1 spec + 1 plan + 6 task commits — there is no separate plan commit until you commit the plan file; depending on workflow either 7 or 8 commits).

  Diff-Stat shows:
  - `docs/superpowers/specs/2026-06-04-pr3-rich-part-data.md` — already in
  - `docs/superpowers/plans/2026-06-04-pr3-rich-part-data.md` — added during planning
  - `scripts/inventree_sync/client.py` — modified
  - `scripts/inventree_sync/fetchers.py` — modified
  - `scripts/inventree_sync/attachments.py` — new
  - `scripts/bom_export.py` — modified
  - `scripts/e2e_revision_handling.py` — modified
  - `scripts/tests/test_mouser_attributes.py` — new
  - `.github/workflows/create-release-docs.yaml` — modified

### Step 7.7: Done

- [ ] Branch is ready for PR creation in a separate step.

---

## Akzeptanzkriterien (mirror of spec)

- [x] `upload_parameters` helper exists in `client.py` with delta-sync semantics (Task 2)
- [x] Mouser-Fetcher parses `ProductAttributes` → `parameters` dict (Task 1)
- [x] LCSC + Mouser parameters synced on both create + ensure paths (Task 3)
- [x] `_supplier_url` helper constructs LCSC + Mouser URLs from SKU (Task 4)
- [x] All SupplierPart-Create-Payloads contain populated `link` field (Task 4)
- [x] `scripts/inventree_sync/attachments.py` with `attach_kibot_outputs` exists (Task 5)
- [x] Auto-Discovery Mapping per spec (Task 5)
- [x] Idempotency via existing-filenames check (Task 5)
- [x] `bom_export.py` has new `--output_dir` CLI arg (Task 6)
- [x] `create-release-docs.yaml` reaktiviert mit `--output_dir output/` (Task 6)
- [x] 3 neue E2E-Tests grün (Tasks 2, 4, 5)
- [x] pytest-Suite 68 grün (Task 7)
- [x] py_compile clean (Task 7)
