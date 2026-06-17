# Attach a Supplier to PCB & SMT Stencil Parts in `bom_export.py`

**Status:** Spec ready.
**Scope:** Bugfix in `scripts/bom_export.py`. `create_pcb_part` and
`create_stencil_part` currently create the InvenTree Part only, with no
`SupplierPart` linkage. When the operator later wants to reorder the PCB
or stencil from the fab (JLCPCB in our case), there's no record of which
supplier produced it — the only metadata is the design name + revision.
**Predecessor:** PR #32 `use supplier_reference + auto-reference`
(main @ 0a0eb92).
**Erstellt:** 2026-06-11.

---

## Motivation

After the recent v0.1 bulk-import we ended up with 4 PCBs (pks 1291,
1294, 1297, 1301) and 4 SMT Stencils (pks 1293, 1296, 1299, 1303) in
InvenTree — none with a SupplierPart. JLCPCB exists in InvenTree as
Company pk=381 (`is_supplier=true`, `is_manufacturer=false`), it just
isn't linked.

Consequences:
- Re-order path requires the operator to remember which fab made each
  PCB design — fine for 4 designs, not fine for 20+.
- InvenTree's "Purchasing" view shows no supplier for these parts, so
  they can't be added to a JLCPCB PurchaseOrder via the normal flow.
- Stock-low alerts can't link to a supplier for one-click reorder.

The fix is mechanically small: extend the create functions to also POST
a `SupplierPart` linking the Part to a configured fab supplier. Same
idempotent pattern as `ensure_supplier_parts` in `client.py`.

---

## Goals

- `create_pcb_part` and `create_stencil_part` attach a `SupplierPart`
  to a configured fab supplier (default JLCPCB) for every newly-created
  PCB and stencil Part.
- The supplier is selectable per-run via CLI flag (different fabs per
  project must work without editing code).
- The SKU is derived from the Part name + revision so it's stable
  across re-runs — same design rev always points to the same SKU.
- Idempotent: re-running `bom_export.py` for the same release doesn't
  produce duplicate SupplierParts.
- Opt-out: an explicit flag suppresses SupplierPart creation entirely
  (for users without a fab or who manage supplier linkage manually).
- The `Assembly` part stays without supplier (we assemble in-house).
- The implementation does NOT modify `inventree_sync/order_import.py`,
  `inventree_sync/dry_run.py`, or any unrelated code path.

## Non-Goals

- **No** `link` / `pricing` / `pack_quantity` field handling on the
  SupplierPart (deferred — re-order not planned in the near term).
- **No** support for multiple fabs per assembly (PCB from JLCPCB,
  stencil from PCBWay etc.). One supplier per run, both parts attach
  to it.
- **No** ManufacturerPart creation. Bare-PCB substrate has no MPN /
  manufacturer-side identifier worth tracking.
- **No** auto-derivation of the SupplierPart link from GitHub release
  URLs (consciously deferred — `--pcb-supplier-link` flag could be a
  future addition).
- **No** retroactive backfill of the 8 existing v0.1 PCB/Stencil
  records. That happens as a separate ad-hoc API run outside this PR.
- **No** TME or other 3rd-distributor handling (separate brainstorming
  thread, may or may not happen).

---

## Designentscheidungen

### Why CLI flag instead of hardcoded JLCPCB

We could hardcode `PCB_SUPPLIER_NAME = "JLCPCB"`. But the `bom_export.py`
script is consumer-repo-agnostic — `HW-Module-CI`'s reusable workflows
call it from `HW-Module-BusBoard`, `HW-Module-PowerBoard`, etc., and a
future project might want PCBWay or AISLER. Hardcoding would tie the
shared script to one consumer's choice.

CLI flag `--pcb-supplier NAME` (default `"JLCPCB"`) keeps the default
matching reality (everyone uses JLCPCB today) while leaving the door
open. The Auto-Release workflow at `create-release-docs.yaml` can be
updated to pass a project-specific value later if needed; today the
default suffices.

### Why SKU = `{full_name} rev {version}` (stable, deterministic)

A SupplierPart needs a SKU. JLCPCB itself assigns ephemeral order IDs
per production batch (no permanent design-side SKU). Options considered:

1. **`{full_name} rev {version}`** — e.g. `"v0.1 BusBoard PCB rev 0.1"`.
   Stable per design, idempotent on re-runs.
2. **Git SHA** — stable per commit. Burns through SupplierPart slots on
   every release. Wrong granularity.
3. **Random UUID** — never stable across runs. Defeats the point.
4. **No SKU** — InvenTree's SupplierPart model requires a non-empty
   SKU. Rejected at the API level.

Chosen: **Option 1**. Idempotent re-run finds the existing SupplierPart
by (part, supplier, SKU) and skips creation. Same pattern as
`ensure_supplier_parts` in `client.py` line ~720.

### Why opt-out flag instead of "supplier=None means skip"

A `--pcb-supplier ""` could mean "skip supplier linkage". But empty-
string-as-sentinel is confusing in CLI parsing (and quoting is messy in
YAML CI configs). Explicit `--no-pcb-supplier` (action="store_true") is
clearer and survives argparse normalization unambiguously.

### Why Assembly stays without supplier

The Assembly Module is built by OE5XRX itself (or whoever runs the
import). There's no external supplier. If a project ever outsources SMT
assembly to JLCPCB-Assembly or PCBWay-Assembly, that's a future
extension — add a separate `--assembly-supplier NAME` flag then,
following the same pattern. YAGNI for now.

---

## Komponenten

### `scripts/bom_export.py` — Änderungen

**New constants:**

```python
# Default fab supplier name. CLI --pcb-supplier overrides per-run; the
# JLCPCB Company is auto-created via get_or_create_supplier if missing.
DEFAULT_PCB_SUPPLIER_NAME = "JLCPCB"
```

**New CLI flags:**

```python
parser.add_argument(
    "--pcb-supplier", default=DEFAULT_PCB_SUPPLIER_NAME,
    help=(
        "Company name of the fab that produces the PCB + SMT stencil "
        f"(default: {DEFAULT_PCB_SUPPLIER_NAME!r}). The Company is "
        "auto-created if missing. Use --no-pcb-supplier to skip "
        "SupplierPart linkage entirely."
    ),
)
parser.add_argument(
    "--no-pcb-supplier", dest="pcb_supplier", action="store_const",
    const=None,
    help="Skip SupplierPart linkage on PCB + SMT stencil parts.",
)
```

After argparse: `args.pcb_supplier` is either a non-empty string (a
name) or `None` (opt-out). Both flags target the same dest so the order
in the command line is "last wins" — predictable behaviour.

**New helper** in `scripts/bom_export.py` (NOT in `inventree_sync`
package — this is bom_export-specific so far):

```python
def _ensure_fab_supplier_part(
    api: InvenTreeAPI,
    part: Part,
    supplier: Company,
    sku: str,
) -> None:
    """Idempotently link a Part to a fab Supplier with a derived SKU.

    Same defensive pattern as `ensure_supplier_parts` in client.py:
    list existing SupplierParts for (part, supplier), post-filter on SKU
    (server-side filter is unreliable on this InvenTree version), skip
    when a match already exists.

    Errors during create are logged but never raised — caller treats
    fab-supplier linkage as best-effort, not blocking. A missing
    SupplierPart can always be added later via the InvenTree UI.
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
            "SupplierPart create failed for part=%s supplier=%s SKU=%r: %s; "
            "fab linkage skipped (add manually in the UI if needed).",
            part.pk, supplier.pk, sku, exc)
```

**Modified create functions:**

`create_pcb_part` and `create_stencil_part` gain an optional
`fab_supplier: Optional[Company] = None` parameter. When non-None, the
function calls `_ensure_fab_supplier_part` after Part create / reuse
with SKU = `f"{full_name} rev {version}"`.

Existing-Part path (reuse branch) ALSO calls `_ensure_fab_supplier_part`
— this lets the operator add fab linkage retroactively by re-running
`bom_export.py` on a release where Parts already exist but were created
before this PR. Idempotent thanks to the SKU-existence check.

`create_assembly_part` stays unchanged (no fab supplier — built in-house).

**Modified `main()`:**

```python
fab_supplier: Optional[Company] = None
if args.pcb_supplier is not None:
    fab_supplier = get_or_create_supplier(api, name=args.pcb_supplier)
    if fab_supplier is None:
        log.error(
            "Could not get or create fab supplier %r — proceeding without "
            "SupplierPart linkage.", args.pcb_supplier)

# ... existing pcb / assembly / stencil creation ...
pcb = create_pcb_part(
    api, pcb_cat, args.name, args.version, args.pcb_image,
    fab_supplier=fab_supplier,
)
# assembly: unchanged
stencil = create_stencil_part(
    api, stencil_cat, args.name, args.version, args.stencil_image,
    fab_supplier=fab_supplier,
)
```

`get_or_create_supplier` already exists in `inventree_sync/client.py`
(imported at the top of `bom_export.py`). Re-use, no new import.

### Tests

Test file: `scripts/tests/test_bom_export_fab_supplier.py` (new).

| Test | Verifies |
|---|---|
| `test_ensure_fab_supplier_part_creates_when_missing` | Mock `SupplierPart.list` returns `[]`. Call helper. Assert `SupplierPart.create` called with `{part, supplier, SKU}`. |
| `test_ensure_fab_supplier_part_skips_when_existing` | Mock `SupplierPart.list` returns one SP with matching SKU. Assert `SupplierPart.create` NOT called. |
| `test_ensure_fab_supplier_part_creates_when_existing_different_sku` | Mock returns SP with different SKU. Assert `create` IS called (different design rev). |
| `test_ensure_fab_supplier_part_list_failure_skips_gracefully` | Mock `list` raises. Assert no exception escapes and no create attempted. |
| `test_ensure_fab_supplier_part_create_failure_logged_not_raised` | Mock `create` raises. Assert warning logged, exception swallowed. |
| `test_create_pcb_part_attaches_fab_supplier_on_new_part` | Full path: new Part. Assert `_ensure_fab_supplier_part` called with `SKU="<name> PCB rev <ver>"`. |
| `test_create_pcb_part_attaches_fab_supplier_on_reuse` | Existing Part returned. Helper STILL called (retroactive linkage). |
| `test_create_pcb_part_skips_fab_supplier_when_none` | `fab_supplier=None`. Helper NOT called. |
| `test_create_stencil_part_attaches_fab_supplier_on_new_part` | Same as PCB but with `"<name> SMT Stencil rev <ver>"` SKU. |
| `test_create_assembly_part_does_not_attach_fab_supplier` | Assembly never gets supplier (verify no helper call). |

CLI-integration test added to existing `scripts/tests/` if appropriate
(or skipped — the existing `bom_export.py` doesn't have a CLI test
file). Decision: skip the CLI integration test; the unit tests cover
the logic, and the CLI flag itself is mechanical argparse.

---

## Idempotenz-Garantien

| Re-Run-Szenario | Verhalten |
|---|---|
| First-ever run of v1.0 | PCB+Stencil Parts created. SupplierPart created for each linking to JLCPCB. |
| Re-run of same v1.0 release | Existing Parts found via `find_part_by_name_and_revision`. SupplierPart found via SKU match. Both skipped. |
| Re-run of v1.0 with `--pcb-supplier PCBWay` | Existing PCB Part reused. New SupplierPart created for PCBWay (different supplier). Old JLCPCB SupplierPart unchanged. |
| Re-run with `--no-pcb-supplier` | Existing Parts reused. No SupplierPart action. Existing SupplierParts (from previous runs) remain. |
| Backfill: Part exists, was created before this PR (no SupplierPart) | First call to the modified `create_pcb_part` adds the SupplierPart. |

---

## Error Handling

| Failure | Verhalten |
|---|---|
| `get_or_create_supplier` returns None (Company API down or 403) | `main()` logs error, sets `fab_supplier=None`, proceeds. PCB+Stencil created without SupplierPart. |
| `SupplierPart.list` raises in helper | Logged as warning, helper returns without create. |
| `SupplierPart.create` raises in helper | Logged as warning, helper returns. PCB/Stencil Part already exists at this point. |
| User passes invalid name to `--pcb-supplier` (e.g. with `is_customer=true`) | `get_or_create_supplier` always creates with `is_supplier=true`, `is_manufacturer=false`. If a Company with that name + wrong flag already exists, it gets returned — operator can re-classify in UI. |

The contract: **fab-supplier linkage is best-effort**. The script must
never fail the release CI run due to a fab-supplier issue. The release
docs / assembly creation are the primary outputs; SupplierPart is
secondary metadata.

---

## Backwards-Compatibility

- `create_pcb_part` / `create_stencil_part` gain a keyword-only
  `fab_supplier=None` argument. Existing call sites in `main()` are
  updated; any external caller (none today) would default to no
  supplier.
- Default CLI behaviour: SupplierPart linkage IS created (`--pcb-supplier`
  defaults to `"JLCPCB"`). This is a behaviour change vs. main, but the
  current behaviour is the bug — no opposition expected.
- `--no-pcb-supplier` provides the explicit opt-out for users who don't
  want this.
- Existing unit tests for `create_pcb_part` etc. (if any) pass
  `fab_supplier=None` implicitly via the default.

---

## Out-of-Scope

- ManufacturerPart linkage (PCB substrate maker, e.g. FR4 vendor).
- `link` field on SupplierPart pointing to GitHub release asset.
- `pack_quantity` / `pricing` fields on SupplierPart.
- Multi-fab support (PCB from one fab, stencil from another).
- Assembly-side supplier (JLCPCB SMT-Assembly).
- Retroactive backfill of existing v0.1 PCBs/Stencils — done outside
  this PR as a one-shot API run.
