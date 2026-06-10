# Fix: Use `supplier_reference` Field + Server-Side `reference` Auto-Assignment

**Status:** Spec ready.
**Scope:** Bugfix in `scripts/inventree_sync/order_import.py`. The
importer was POSTing `reference=<mouser/lcsc order id>` directly into
InvenTree's PurchaseOrder.reference field, which violates the server's
mandatory `reference` pattern (e.g. `PO-{ref:04d}`). The correct field
for the supplier-side order ID is `supplier_reference`, and InvenTree
auto-generates a compliant `reference` (next in sequence) for us — we
read it from the OPTIONS endpoint's `default` value.
**Predecessor:** PR #31 `make --dry-run actually dry` (main @ 5d1b7fd).
**Erstellt:** 2026-06-10, direkt nach erstem Real-Run-Versuch gegen
Production-InvenTree.

---

## Motivation

Erster Real-Run gegen `parts.oe5xrx.org`:

```
INFO: Resolving 29 parts from LCSC order WM2504270070…
Traceback (most recent call last):
  ...
requests.exceptions.HTTPError: {'status_code': 400,
  'body': '{"reference":["Reference must match required pattern: PO-{ref:04d}"]}',
  'data': {'supplier': 258, 'reference': 'WM2504270070', ...}}
```

Drei Punkte aus dem Fehler:

1. **InvenTree erzwingt server-seitig ein Reference-Pattern** (`PO-{ref:04d}`
   default). Wir haben den supplier-side Order-ID (`WM2504270070`,
   `275708282`, …) als `reference` reingeworfen — natürlich crashed.
2. **`supplier_reference` ist das richtige Feld** für die Mouser/LCSC-
   Order-ID. Existiert als optionales String-Feld (max 64 Zeichen,
   allow_blank=True), und das InvenTree-UI rendert es als
   „Lieferanten-Referenz".
3. **Der Operator hat bereits 3 POs manuell angelegt** und die
   Supplier-Reference dort gesetzt:
   - `pk=1 PO-0001  supplier_reference="274770685"` (Mouser, 5 Lines)
   - `pk=3 PO-0002  supplier_reference="WM2504270070"` (LCSC, 5 Lines)
   - `pk=2 PO-0005  supplier_reference="275708282"` (Mouser, 25 Lines)

Der Importer findet diese existierenden POs nicht, weil `_find_po`
nach `reference` filtert (= server-generierte Sequenz) statt nach
`supplier_reference` (= unsere Order-ID). Pfad A würde versuchen, neue
POs anzulegen → 400-Crash siehe oben. Selbst wenn das durchginge,
hätten wir Duplikate.

Außerdem entdeckt:
**`PurchaseOrder.list(api, supplier_reference=X)` filtert server-seitig
nicht** — die OPTIONS-Schema sagt `supplier_reference` ist filterbar,
aber der Query-Parameter wird ignoriert (alle POs werden zurückgegeben,
unabhängig vom Wert). Bestätigt durch direkten `curl`-Test gegen die
Live-API. Defensive Lösung: gleiches Post-Filter-Pattern wie
`find_part_by_name` / `find_part_by_mpn_and_manufacturer` in
`client.py`.

---

## Goals

- POs werden **per `supplier_reference`** identifiziert, nicht per
  `reference` (die ist InvenTrees Sequenz-Number, keine semantische
  ID).
- Neue POs holen ihre `reference` **vom Server** via OPTIONS-Endpoint
  → matched garantiert das aktuelle Pattern auf jeder Instanz.
- Die drei existierenden manuell angelegten POs werden korrekt
  wiedererkannt; Pfad B reconciled sie gegen die Files.
- Bestehende Tests bleiben funktional; 4 neue Tests decken die neue
  Lookup- und Reference-Generation-Logik ab.
- `SupplierOrder.reference` Semantik bleibt unangerührt (= supplier-
  side Order-ID); nur das Mapping auf das InvenTree-Datenmodell ändert
  sich.

## Non-Goals

- **Kein Versuch**, das InvenTree-`reference`-Pattern client-seitig zu
  validieren. Der Server ist die Truth-Source.
- **Kein neuer CLI-Flag** (kein `--reference-prefix` o.ä.).
- **Kein Refactor von `LineItem.reference`** — die wird weiterhin auf
  den File-SKU gesetzt (`reference=line.sku`), wie bisher.
- **Kein Auto-Migrate** von POs die wir früher mit unserer falschen
  Reference angelegt hätten — solche existieren nicht, weil der erste
  Real-Run beim ersten POST gecrashed ist (siehe Motivation).
- **Keine Änderung an `UpsertReport`** außer dass `po_reference` jetzt
  den server-zugewiesenen Wert tragen kann (`"PO-0006"` statt
  `"WM2504270070"`). Das ist sogar gewünscht weil's mit dem InvenTree-
  UI übereinstimmt.

---

## Designentscheidungen

### Warum OPTIONS für die Reference statt POST-und-Fehler-parsen

Drei Optionen erwogen:

1. **OPTIONS-Endpoint:** GET `OPTIONS /api/order/po/` →
   `actions.POST.reference.default` enthält die nächste Sequenz-Number
   (verifiziert: `PO-0006` bei aktueller InvenTree-Instanz).
2. **POST ohne `reference`** → Server-Validation lehnt ab mit der
   Pattern-Message → wir parsen die nächste daraus. Hacky.
3. **Client-seitige Sequenz** (eigenes Counter-File / Inkrement aus
   Last-PO) → kann mit anderen Tools/Manual-UI drift'en.

Gewählt: **Option 1**. Sauber, race-free für unsere Verwendung
(sequenzielle Single-Process-Importe), und der Server bleibt Truth-
Source. Der `actions.POST.reference.default` ist die offizielle
InvenTree-Methode, die Sequenz vorab zu lesen — gleicher Mechanismus
den das React-Frontend verwendet, wenn es das Create-Form vorausfüllt.

### Warum Post-Filter trotz vorhandenem Query-Param

Empirisch verifiziert: `GET /api/order/po/?supplier_reference=275708282`
gibt alle 3 POs zurück, nicht nur die mit matchender Reference. Der
Filter wird vom Backend ignoriert (oder existiert nur als nominelle
Schema-Definition ohne Implementation). Gleiches Verhalten wie bei
`Part.list(name=...)` — dort wird's auch defensive post-gefiltert
(siehe `client.py:find_part_by_name`). Wir folgen demselben Pattern.

### Warum nicht `SupplierOrder.reference` umbenennen

`SupplierOrder.reference` heißt auf unserer Seite weiterhin „supplier-
side Order-ID" (das was Mouser/LCSC vergeben). Das ist semantisch
korrekt für unseren Domain — wir parsen ja Mouser/LCSC-Files, da gibt's
nur eine Reference und das ist die supplier-side. Die InvenTree-
spezifische Trennung in `reference` (sequenz) und `supplier_reference`
(supplier-side) ist Implementation-Detail der Persistierung, nicht
unseres Domain-Modells. Umbenennung würde 6+ Test-Files anfassen ohne
Klarheits-Gewinn.

---

## Komponenten

### `inventree_sync/order_import.py` — Änderungen

**Neuer Helper:**

```python
def _next_po_reference(api: InvenTreeAPI) -> str:
    """Read the next valid PurchaseOrder.reference from the server.

    InvenTree's OPTIONS response for /api/order/po/ includes a computed
    ``actions.POST.reference.default`` that is the next reference matching
    the server's configured pattern (e.g. ``"PO-0006"`` for
    ``PO-{ref:04d}``).  This is the same mechanism the React UI uses to
    pre-fill the Create-PO form.

    Raises RuntimeError if the OPTIONS response is missing the field or
    the request fails — the importer can't reliably create POs without
    knowing the next valid reference, so we fail loud rather than guess.
    """
    try:
        resp = api.request(PurchaseOrder.URL, method="OPTIONS")
        body = resp.json()
        return body["actions"]["POST"]["reference"]["default"]
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read next PurchaseOrder.reference from "
            f"OPTIONS {PurchaseOrder.URL}: {exc}"
        ) from exc
```

**`_find_po` umbauen:**

```python
def _find_po(api: InvenTreeAPI, supplier_pk: int, supplier_reference: str):
    """Locate a PurchaseOrder by supplier + supplier_reference.

    The server-side ``?supplier_reference=`` filter is silently ignored
    (verified empirically against InvenTree 1.3.4), so we list all POs
    for the supplier and post-filter on supplier_reference. Same
    defensive pattern as ``find_part_by_name`` in ``client.py``.

    Returns the first match (or None). Multiple matches are not
    expected — supplier_reference is the operational identifier — and
    we don't warn on them to keep the path simple; in practice a
    duplicate would be a data-quality issue the operator should
    resolve in the UI.
    """
    matches = PurchaseOrder.list(api, supplier=supplier_pk)
    for po in matches:
        # Server-side filter is unreliable for both supplier and
        # supplier_reference — apply both post-filters. Skip when the
        # PO's supplier is a concrete int that differs (same pattern as
        # before).
        po_supplier = getattr(po, "supplier", None)
        if po_supplier is not None and not isinstance(po_supplier, bool):
            try:
                po_supplier_pk = int(po_supplier)
            except (TypeError, ValueError):
                po_supplier_pk = None
            if po_supplier_pk is not None and po_supplier_pk != supplier_pk:
                continue
        if str(getattr(po, "supplier_reference", "") or "") == supplier_reference:
            return po
    return None
```

**`upsert_purchase_order` Pfad A:**

```python
if existing is None:
    # Pfad A
    if dry_run:
        next_ref = "(server-assigned)"   # don't probe in dry-run
        return UpsertReport(
            action="DRY_RUN_CREATE", po_reference=next_ref,
            lines_added=len(deduped_lines),
        )
    next_ref = _next_po_reference(api)
    po = PurchaseOrder.create(api, {
        "supplier": supplier.pk,
        "reference": next_ref,
        "supplier_reference": order.reference,
        "description": f"Imported from {order.supplier_name} order {order.reference}",
        **({"target_date": order.order_date} if order.order_date else {}),
    })
    ...
```

The dry-run branch deliberately does NOT probe OPTIONS — staying in
dry-run mode contract "no API state probes that count as side-effects
for compliance". Reading the next reference is *technically* read-only,
but the value mutates between runs (advances after each real POST), so
reporting `"(server-assigned)"` is honest about what the operator will
see.

### `UpsertReport` Docstring-Update

```python
@dataclass
class UpsertReport:
    """...
    po_reference holds the InvenTree PurchaseOrder.reference. For a
    freshly created PO this is the server-assigned sequence value (e.g.
    "PO-0006"), not the supplier-side order ID (which lives in the
    PurchaseOrder.supplier_reference field). For an existing PO this
    is the value already stored.
    """
```

---

## Tests

### Mock-based unit tests (in `test_order_import_upsert.py`)

| Test | Was wird verifiziert |
|---|---|
| `test_next_po_reference_reads_default_from_options` | Mock `api.request(URL, method="OPTIONS")` → response.json() with `actions.POST.reference.default = "PO-0006"`. `_next_po_reference(api) == "PO-0006"`. |
| `test_next_po_reference_raises_when_default_missing` | Mock returns body without the nested key. `_next_po_reference(api)` raises `RuntimeError` mentioning OPTIONS path. |
| `test_next_po_reference_raises_when_options_request_fails` | Mock raises connection error. `_next_po_reference(api)` raises `RuntimeError`. |
| `test_find_po_post_filters_by_supplier_reference` | Mock `PurchaseOrder.list` returns 3 POs with different supplier_references. `_find_po(api, supplier_pk=1, "275708282")` returns only the matching one (skipping the others). |
| `test_find_po_returns_none_when_no_match` | All 3 POs have non-matching supplier_references. Returns None. |
| `test_upsert_path_a_uses_server_assigned_reference` | Mock `PurchaseOrder.list` returns [] (no existing PO). Mock OPTIONS → `"PO-0006"`. Assert `PurchaseOrder.create` is called with both `reference="PO-0006"` AND `supplier_reference=<order.reference>`. |
| `test_upsert_path_a_dry_run_does_not_probe_options` | Dry-run + no existing PO → no `api.request("OPTIONS", ...)` call, no `PurchaseOrder.create` call, returns `UpsertReport(action="DRY_RUN_CREATE", po_reference="(server-assigned)", lines_added=N)`. |
| `test_upsert_path_b_finds_existing_via_supplier_reference` | Mock `PurchaseOrder.list` returns existing PO with matching supplier_reference. Reconcile path taken (not Pfad A). No OPTIONS request. |

### Real-Run-Regression

Existing `test_path_a_creates_po_and_lines_and_receives` and friends
in `test_order_import_upsert.py` are updated to:
1. Mock `PurchaseOrder.list` (the new lookup) returns empty.
2. Mock `_next_po_reference` (or the OPTIONS call) returns `"PO-0006"`.
3. Assert `PurchaseOrder.create` is called with the new payload shape.

Other existing Pfad-B/C tests stay structurally similar — the
existing test fixtures already mock `PurchaseOrder.list` returning the
existing PO, so they continue to work once the lookup is wired through
the new helper.

---

## Error Handling

| Failure | Verhalten |
|---|---|
| OPTIONS request fails (network, 5xx) | `_next_po_reference` raises RuntimeError; bubbles up through `upsert_purchase_order` to `_import_one_order`'s RuntimeError-catcher → records FAIL on reporter (dry-run path: not triggered because we skip OPTIONS in dry-run). |
| OPTIONS response missing `actions.POST.reference.default` | Same path: RuntimeError, FAIL record, rc=1. |
| Server rejects POST with new pattern violation (server pattern changed between OPTIONS and POST — race) | InvenTree returns HTTP 400 → InvenTreeAPI raises HTTPError → bubbles up like any other create failure. |
| Multiple POs with the same supplier_reference (data-quality issue) | `_find_po` returns the first. We don't warn — operator-side cleanup. |

---

## Backwards-Compatibility

- `SupplierOrder` dataclass: unchanged.
- `UpsertReport.po_reference`: semantic shift for Pfad A — now holds
  server-assigned (e.g. `"PO-0006"`) rather than supplier-side
  (`"WM2504270070"`). Docstring updated. Existing tests that assert
  on `report.po_reference` are updated to match the new value.
- Pfad B (PO exists, status PENDING/PLACED): `report.po_reference`
  reflects the existing PO's reference (was the case before too).
- Pfad C (PO exists, COMPLETE): same.

---

## Out-of-Scope (für ein späteres Tool, wenn überhaupt)

- Vorab-Validierung dass das server-side Pattern unsere geplanten
  Refs würde akzeptieren (pre-flight check). Macht der Server selbst
  beim POST.
- Konfigurierbares Pattern-Override im CLI.
- Bulk-Lookup für viele Files (wir machen 1 OPTIONS-Call pro PO, was
  bei 2-3 Files vernachlässigbar ist).
