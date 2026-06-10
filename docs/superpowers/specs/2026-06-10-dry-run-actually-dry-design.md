# Fix: `--dry-run` macht Part-Resolution wirklich dry

**Status:** Spec ready.
**Scope:** Bugfix in `scripts/import_supplier_order.py` und
`scripts/inventree_sync/order_import.py`. Der `--dry-run`-Modus war nur
auf der `upsert_purchase_order`-Ebene wirksam; `ensure_part_for_order_line`
darüber hat unkonditional `Part.create`, `Category.create`,
`SupplierPart.create`, `ManufacturerPart.create`, Image-Uploads und
Parameter-Updates ausgeführt.
**Predecessor:** PR #30 supplier-order-import (main @ 9b64ebf).
**Erstellt:** 2026-06-10, direkt nach erstem `--dry-run` gegen Production-InvenTree.

---

## Motivation

Erster Real-Test des Importers gegen `https://parts.oe5xrx.org/` (Production-
InvenTree) mit `--dry-run` Flag:

```
INFO: Created part '0805B333K500NT' (pk=1279)
INFO: Uploaded image to part 1279 (from https://assets.lcsc.com/…)
INFO: Created ManufacturerPart FH / 0805B333K500NT for Part pk=1279
…
INFO: DRY_RUN_CREATE PO WM2504270070 — added=29 updated=0 deleted=0
```

Trotz `--dry-run`:

- 7 LCSC-Parts erzeugt (pk 1279–1285, mit Images + MfrParts)
- 3 Mouser-Parts erzeugt (pk 1286–1288)
- 2 Categories neu angelegt (Tantalkondensatoren, Schaltspannungsregler)
- N ManufacturerParts neu angelegt

Nur die PurchaseOrder/LineItem/StockItem-Schicht wurde korrekt geskippt
(0 POs, 0 LineItems, 0 StockItems).

Spec war eindeutig: *„Dry-Run: Erst alle Lookups + Print-Plan
(Would-CREATE/REUSE per Part, Würde PO X mit N Lines erzeugen). Kein
InvenTree-Write."* Die Implementierung hat das auf der Part-Resolution-
Ebene nicht durchgezogen.

Konsequenzen:
- **User-Trust gebrochen** — `--dry-run` ist die Sicherheitsleine vor
  Production-Schreibzugriffen. Verletzt → niemand traut sich mehr.
- **Wiederholbares Preview unmöglich** — eine zweite Dry-Run würde die
  pk-Nummern in den REUSE-Records mismatchen, weil die Parts schon
  da sind.
- **Skalierungsrisiko** — bei einer 200-zeiligen Bestellung würde
  `--dry-run` 200 Part-Creates plus dutzende Category-Creates plus 200
  Image-Downloads triggern, bevor irgendwas gemeldet wird.

---

## Goals

- `ensure_part_for_order_line` respektiert `--dry-run`: macht alle
  Read-Lookups (SKU → MPN+Mfr → Name), schreibt aber NICHTS in
  InvenTree.
- Dry-Run-Output zeigt für JEDE Line ein CREATE/REUSE-Record im
  bekannten `DryRunReporter`-Format (analog `bom_export.py`).
- `_import_one_order` und `main()` threaden den Reporter durch und
  geben am Schluss `print_report()` aus.
- Bestehender Real-Run-Code-Path und alle bestehenden Tests bleiben
  funktional unverändert (`reporter=None` als Default).
- Mindestens vier neue Tests decken die Dry-Run-Pfade in
  `ensure_part_for_order_line` ab.

## Non-Goals

- **Kein Rollback** der 10 Parts aus dem broken Dry-Run vom 10.6.
  Sie sind valide (SKU + MPN match) und werden vom gefixten
  Dry-Run / Real-Run als REUSE wiedergefunden.
- **Kein neuer Flag** wie `--no-fetch` oder `--offline`. Supplier-API-
  Calls bleiben auch im Dry-Run aktiv (sie sind Read-Only und liefern
  die Daten für eine vollständige Preview).
- **Keine Refactor** des Dry-Run-Mechanismus in `upsert_purchase_order`
  — der ist schon korrekt.
- **Keine API-Änderung an `DryRunReporter`** — die Klasse aus
  `inventree_sync/dry_run.py` wird unverändert wiederverwendet.

---

## Designentscheidungen

### Pattern-Wahl: `DryRunReporter` statt nackter `dry_run: bool`

`bom_export.py` nutzt seit PR-4 `DryRunReporter` zum Sammeln und Drucken
von Decisions. `part_manager.ensure_parts_exist` akzeptiert das schon
als `reporter: Optional[DryRunReporter]`. Der gleiche Stil hier hält das
Mental-Model konsistent: ein Helper-Objekt sammelt Records, ruft am Ende
`print_report()`, und der Switch zwischen Real- und Dry-Mode ist
`reporter is None` vs. `reporter is not None`.

Vorteil gegenüber zusätzlichem `dry_run: bool` Param überall: weniger
zu threaden (ein Optional statt einem Bool + einem Reporter), und das
Reporting kommt frei mit.

### Sentinel-Return für Dry-Run

`ensure_part_for_order_line` returnt aktuell `tuple[Part, SupplierPart]`.
Im Dry-Run gibt's keine echten Objekte. Drei Optionen:

1. Eigene Sentinel-Klasse `_DryRunPlaceholder` mit `.pk = None`.
2. Refactor: Dry-Run-Pfad lebt in eigener `preview_part_for_order_line`-
   Funktion, Real-Pfad bleibt unverändert.
3. Return-Type auf `tuple[Optional[Part], Optional[SupplierPart]]`
   relaxieren, Caller checkt auf None.

**Gewählt: Option 3.** Einfachste Signatur-Änderung, Type-Hint reflektiert
die Realität ehrlich. Caller (`_import_one_order`) prüft `if sp is not
None:` bevor er `sku_to_sp[line.sku] = sp` einträgt — wenn None drin
steht, läuft der Real-Run-Code in `upsert_purchase_order` ohnehin nicht,
weil dort `if dry_run:` vor jedem Mapping-Zugriff steht.

Option 1 verschmutzt den Type-Space; Option 2 duplikatet die Lookup-
Logik.

### `upsert_purchase_order` mit leerem `sku_to_supplier_part`

Im Dry-Run wird `sku_to_supplier_part` als leerer Dict übergeben. Drei
Pfade müssen damit umgehen:

- **Pfad A (PO existiert nicht)**: `if dry_run: return
  UpsertReport(action="DRY_RUN_CREATE", ...)` steht vor dem AddLineItem-
  Loop, der `sku_to_supplier_part[line.sku]` brauchen würde. ✅ Bereits ok.
- **Pfad B (PO PENDING/PLACED)**: `compute_po_line_diff` produziert
  `to_add`/`to_update`/`to_delete` aus den File-Lines und PO-Lines.
  Im Dry-Run-Branch `if dry_run: return UpsertReport(action=
  "DRY_RUN_RECONCILE", ...)` steht VOR dem `existing.addLineItem(part=
  sp.pk, ...)` Loop. ✅ Bereits ok.
- **Pfad C (PO COMPLETE)**: berechnet Diff. `compute_po_line_diff` nutzt
  `sku_to_supplier_part_pk` nur als Fallback wenn `po_line.reference`
  leer ist (für POs ohne Reference angelegt). Bei leerem Dict greift
  der Fallback nicht, d.h. PO-Lines ohne Reference werden in
  `by_sku_po` nicht eingetragen → als `to_delete` gewertet. Das ist
  ein **akzeptabler Edge-Case**: tritt nur auf wenn der User eine PO
  manuell ohne `reference` in InvenTree angelegt hat UND dasselbe
  Skript im Dry-Run gegen sie laufen lässt. **Wird im Docstring
  dokumentiert**, kein Code-Change nötig.

### Logging-Verhalten im Dry-Run

Im Real-Run loggt jeder Part-Create eine INFO-Zeile (`Created part X
(pk=Y)`). Im Dry-Run gibt's diese Logs nicht — stattdessen sammelt
`DryRunReporter` die Records und gibt am Ende einen kompakten Report
aus. **Beide Modi haben weiterhin den initialen INFO-Log** mit
„Importing supplier-order parts without KiCad context…".

Der Reporter-Output ist die einzige Quelle für „was würde passieren"
im Dry-Run.

---

## Komponenten

### `inventree_sync/order_import.py` — Änderungen

```python
def ensure_part_for_order_line(
    api: InvenTreeAPI,
    line: SupplierOrderLine,
    supplier_kind: str,
    lcsc_fetcher: Optional[LCSCFetcher],
    mouser_fetcher: Optional[MouserFetcher],
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
    category_map: dict,
    *,
    reporter: Optional["DryRunReporter"] = None,   # NEU
) -> tuple[Optional[Part], Optional[SupplierPart]]:  # geändert: nullable
    """
    …existing docstring…

    Dry-Run (``reporter is not None``):
      - Alle Read-Lookups (SKU/MPN/Name) laufen normal.
      - Supplier-Fetcher werden auch im Dry-Run gerufen (Read-only, liefern
        Daten für ein vollständiges Preview-Naming).
      - Statt ``create_part_in_inventree`` / ``ensure_supplier_parts`` /
        ``resolve_part_category`` wird ``reporter.record(...)`` gerufen
        und ``(None, None)`` zurückgegeben — der Caller hängt die Line
        dann NICHT in den ``sku_to_supplier_part`` Dict.
    """
```

Dry-Run-Pfade pro Zweig:

| Zweig | Real-Run | Dry-Run |
|---|---|---|
| SKU-Hit  | `return existing, _lookup_supplier_part(api, sku)` | `reporter.record("REUSE", "Parts", sku, f"existing pk={existing.pk}")`<br>`return (None, None)` |
| MPN-Hit  | `ensure_supplier_parts(...)` + `return existing, _lookup_supplier_part(...)` | `reporter.record("REUSE", "Parts", sku, f"via MPN+Mfr pk={existing.pk}")`<br>`return (None, None)` |
| Name-Hit | `ensure_supplier_parts(...)` + `return existing, _lookup_supplier_part(...)` | `reporter.record("REUSE", "Parts", sku, f"via name {name!r} pk={existing.pk}")`<br>`return (None, None)` |
| Create   | `resolve_part_category(...) + create_part_in_inventree(...)` + `return created, _lookup_supplier_part(...)` | `reporter.record("CREATE", "Parts", sku, f"name={name!r}")`<br>`return (None, None)` |

Fetcher-Failure-Fallback (`part_data = _partdata_from_line(line)`)
bleibt erhalten — wird auch in Dry-Run benötigt damit das Naming
funktioniert.

### `import_supplier_order.py` — Änderungen

```python
def _import_one_order(
    api, order,
    lcsc_fetcher, mouser_fetcher,
    lcsc_supplier, mouser_supplier,
    category_map, receive_location,
    dry_run: bool,
    reporter: Optional[DryRunReporter] = None,    # NEU
) -> int:
    supplier_kind = order.supplier_name
    supplier = lcsc_supplier if supplier_kind == "LCSC" else mouser_supplier
    log.info("Resolving %d parts from %s order %s…",
             len(order.lines), supplier_kind, order.reference)

    sku_to_sp: dict = {}
    for line in order.lines:
        try:
            part, sp = ensure_part_for_order_line(
                api, line, supplier_kind,
                lcsc_fetcher, mouser_fetcher,
                lcsc_supplier, mouser_supplier,
                category_map,
                reporter=reporter,     # NEU
            )
        except Exception as exc:
            log.error("Failed to resolve %s line %s: %s",
                      supplier_kind, line.sku, exc)
            return 1
        if sp is not None:               # NEU: skip im Dry-Run
            sku_to_sp[line.sku] = sp

    # upsert_purchase_order arbeitet auch mit leerem sku_to_sp wenn
    # dry_run=True, weil alle drei Pfade dort vor dem Mapping-Zugriff
    # short-circuiten.
    try:
        report = upsert_purchase_order(
            api=api, order=order, supplier=supplier,
            sku_to_supplier_part=sku_to_sp,
            receive_location=receive_location,
            dry_run=dry_run,
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if reporter is not None:
        reporter.record(
            "CREATE" if report.action.startswith("DRY_RUN_CREATE") else "REUSE",
            "PurchaseOrder", order.reference,
            f"{report.action.removeprefix('DRY_RUN_')} "
            f"added={report.lines_added} "
            f"updated={report.lines_updated} "
            f"deleted={report.lines_deleted}",
        )
    else:
        log.info(
            "%s PO %s — added=%d updated=%d deleted=%d",
            report.action, report.po_reference,
            report.lines_added, report.lines_updated, report.lines_deleted,
        )
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    _suppress_category_warning()
    log.info(
        "Importing supplier-order parts without KiCad context — "
        "categories will fall back to supplier-provided or 'Miscellaneous'.")

    reporter = DryRunReporter() if args.dry_run else None    # NEU

    # …existing setup…

    rc = 0
    if args.lcsc_csv:
        order = parse_lcsc_csv(Path(args.lcsc_csv))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
            reporter=reporter,       # NEU
        )
    if args.mouser_xls:
        order = parse_mouser_xls(Path(args.mouser_xls))
        rc |= _import_one_order(
            api, order, lcsc_fetcher, mouser_fetcher,
            lcsc_supplier, mouser_supplier, category_map,
            receive_location, args.dry_run,
            reporter=reporter,       # NEU
        )

    if reporter is not None:
        reporter.print_report(title="Supplier Order Import (dry run)")

    return rc
```

`DryRunReporter` Import oben:
```python
from inventree_sync.dry_run import DryRunReporter
```

---

## Tests

### Unit-Tests (Erweiterung von `test_order_import_part_resolution.py`)

| Test | Was wird verifiziert |
|---|---|
| `test_dry_run_sku_hit_records_reuse` | SKU-Lookup-Hit → reporter.records hat `("REUSE", "Parts", sku, "existing pk=...")`. `_lookup_supplier_part` wird gerufen (Read), aber `create_part_in_inventree`/`ensure_supplier_parts`/`resolve_part_category` NICHT. Return ist `(None, None)`. |
| `test_dry_run_mpn_hit_records_reuse` | MPN+Mfr-Hit → Record mit `"via MPN+Mfr pk=..."`. `ensure_supplier_parts` NICHT gecalled. |
| `test_dry_run_name_hit_records_reuse` | Name-Lookup-Hit → Record mit `"via name 'X' pk=..."`. `ensure_supplier_parts` NICHT gecalled. |
| `test_dry_run_create_records_create` | Keiner der Lookups trifft → Record `("CREATE", "Parts", sku, f"name='X'")`. `create_part_in_inventree`, `ensure_supplier_parts`, `resolve_part_category` NICHT gecalled. Return ist `(None, None)`. |
| `test_dry_run_fetcher_failure_still_records_create` | Supplier-API liefert None → Fallback-PartData aus Line. Record CREATE mit `name=line.mpn`. |

### CLI-Test (Erweiterung von `test_order_import_cli.py`)

| Test | Was wird verifiziert |
|---|---|
| `test_dry_run_main_creates_reporter_and_prints_report` | `main(["--dry-run", "--lcsc-csv", X])` → `DryRunReporter` wird instanziiert, `reporter.print_report` wird gerufen, exit-code 0. `ensure_part_for_order_line` wird mit `reporter=<reporter>` als kwarg gecalled. |

### Real-Run-Regression

Bestehende Tests in `test_order_import_part_resolution.py` und
`test_order_import_cli.py` rufen `ensure_part_for_order_line` ohne
`reporter`-Kwarg — Default `None`, Real-Run-Path, unverändertes
Verhalten. Keine Test-Anpassung nötig.

---

## Error Handling

| Failure | Verhalten |
|---|---|
| Supplier-Fetcher Failure im Dry-Run | Wie Real-Run: Warning loggen, Fallback-PartData aus Line. Record entsteht trotzdem. |
| `_lookup_supplier_part` Failure im Dry-Run (SKU-Hit-Pfad) | `RuntimeError` wie Real-Run — der Reader-Pfad muss korrekt sein, sonst stimmt das Preview nicht. |
| `find_existing_part`-API-Exception | bubbelt hoch wie bisher, exit-1. Dry-Run hat keine Sonderbehandlung — wenn die Lookups fehlschlagen, ist das Preview nicht aussagekräftig. |

---

## Out-of-Scope

- Reporter-Records für `Categories` (im Real-Run würden neue
  Categories angelegt werden — im Dry-Run skippen wir die Resolution
  komplett, daher kein Record). Acceptable, weil die Categories aus
  dem Part-Naming abgeleitet sind und sich der User die im UI eh
  nachträglich umsortieren würde.
- Reporter-Records für SupplierParts und ManufacturerParts. Implizit
  durch die Part-Records abgedeckt (jeder CREATE-Record steht auch für
  „SupplierPart + MfrPart würden angelegt").
- Reporter-Records für StockItems. `upsert_purchase_order` returnt
  `lines_added` — daraus leitet die CLI im Reporter-Modus den
  PurchaseOrder-Record ab.

---

## Backwards-Compatibility

- `ensure_part_for_order_line` neue Param ist **keyword-only mit
  Default None** → bestehende Aufrufe (alle 8 in den Tests) brechen
  nicht.
- Return-Type-Lockerung von `tuple[Part, SupplierPart]` zu
  `tuple[Optional[Part], Optional[SupplierPart]]` ist im Real-Run
  immer noch `(Part, SupplierPart)` — Caller, die direkt `.pk` zugreifen
  ohne None-Check, sind nur in unseren eigenen 2 Aufrufstellen
  betroffen, beide werden in diesem PR angepasst.
- `_import_one_order` neue Param `reporter` ist optional → kein
  externer Caller (es ist `_`-prefixed, also intern).
- `main()` Signatur unverändert.
