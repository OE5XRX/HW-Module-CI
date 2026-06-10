# Supplier-Order-Import — Mouser & LCSC Historical Orders → InvenTree

**Status:** Spec ready.
**Scope:** Neues CLI-Tool `import_supplier_order.py`, das gelieferte Mouser-
XLS- und LCSC-CSV-Bestellbestätigungen in InvenTree als PurchaseOrder +
LineItems + StockItems importiert. Wiederverwendet die `inventree_sync`-
Library für Part-Dedup/Create.
**Predecessor:** PR-10 `ensure_related_parts` (main @ a947ad3).
**Erstellt:** 2026-06-09. Trigger: zwei alte Real-Bestellungen müssen ins
neue InvenTree gespiegelt werden (Mouser PO-0005 vom 07-Jul-25 mit 27
Lines, LCSC WM2504270070 mit 28 Lines).

---

## Motivation

Das InvenTree-Server-Setup ist post-Decommission-2026-05 wieder online. Der
Inventarstand muss aus zwei historischen Lieferantenbestellungen rekonstruiert
werden:

```
inventree_import/275708282.xls                       # Mouser, 27 Lines, EUR
inventree_import/LCSC__WM2504270070_20260610043835.csv  # LCSC, 28 Lines, USD
```

Manuelles Anlegen via UI würde bedeuten: ~55× Part-Suche, ~55× Part-Create
falls noch nicht da, 2× PO-Create, ~55× LineItem-Anlegen, ~55× Receive.
Fehleranfällig, und die Part-Erstellung würde Datasheet/Image/Parameter
nicht ziehen (das Recht-Klick-„from supplier"-Pattern existiert in InvenTree
nicht für Bulk-Inputs).

Die existierende `inventree_sync`-Library erledigt das part-seitig schon
für KiCad-BOMs. Was fehlt: ein File-zu-PO-Frontend, das die Library
ansteuert und PurchaseOrder/LineItems/StockItems erzeugt.

---

## Goals

- **Eine PurchaseOrder pro Lieferant** wird in InvenTree erzeugt, mit
  Reference aus dem Source-File (Mouser: Sales Order #, LCSC: aus Filename
  abgeleitete Order-ID).
- **Parts werden idempotent dedup'd** über SKU → MPN+Mfr — bestehende Lib-
  Funktionen wiederverwendet, keine Duplikate.
- **Fehlende Parts werden komplett angelegt** inklusive Datasheet, Image,
  Parameter, Price-Breaks, Manufacturer-Linkage, Supplier-Linkage — über
  bestehende `create_part_in_inventree`-Pipeline.
- **Bestellungen werden direkt als RECEIVED markiert** — die Ware ist
  physisch da, StockItems sollen angelegt sein.
- **Re-Runs sind sicher**: bei vorhandener PO wird zur Datei reconciled
  (File = Source of Truth) statt dupliziert.
- **Dry-Run-Modus** existiert für Vorab-Inspektion.
- **Tests** decken Parser, Reconciliation-Logik und CLI-Pfad ab.

## Non-Goals

- Kein automatischer KiCad-Symbol-Guess aus Description-Strings — die
  generierten Parts landen in „Miscellaneous" oder Supplier-Category;
  Re-Klassifizierung passiert im InvenTree-UI per Hand.
- Keine Currency-Conversion (USD↔EUR). Der LCSC-Preis bleibt USD, der
  Mouser-Preis bleibt EUR — InvenTree taggt Currency pro LineItem.
- Keine GitHub-Action-Integration. Das ist ein lokales One-Shot/Migration-
  Tool, keine Release-Pipeline.
- Kein State-File / Lock-DB. Idempotenz kommt aus InvenTree-Side-Lookups.
- Keine Roll-Back-Logik für bereits COMPLETE-POs. Diff-Erkennung + Loud-
  Fail; Hand-Reparatur im UI.
- Kein BOM-Bezug / Assembly-Linkage. Die importierten Parts sind nackte
  Komponenten ohne PCBA-Kontext.

---

## Architektur

```
HW-Module-CI/scripts/
├── import_supplier_order.py              # NEW — CLI entrypoint
├── inventree_sync/
│   ├── (bestehende Module, unverändert)
│   └── order_import.py                   # NEW — Parser + Reconciliation
└── tests/
    ├── (bestehende Tests)
    ├── test_order_import_parsers.py      # NEW — Parser-Unit-Tests
    └── test_order_import_reconcile.py    # NEW — Reconciliation-Logik
```

### Wieso `order_import.py` in `inventree_sync` und nicht standalone

Die Parsing- und Reconciliation-Logik teilt Datentypen (`PartData`) und
Helper (`find_existing_part`, `ensure_supplier_parts`, `create_part_in_
inventree`) mit dem KiCad-BOM-Pfad. Drinnen heißt: gleiche Test-Konventionen,
gleiche Import-Pfade, keine Code-Duplikation. Die CLI bleibt ein dünnes
Frontend wie `bom_export.py` auch — Library-Logik wandert in das Package.

### Datenmodell

```python
@dataclass
class SupplierOrderLine:
    """Eine Zeile aus einer Lieferantenbestellung (XLS oder CSV)."""
    sku: str                  # Mouser-No oder LCSC-Code (z.B. "576-0297003.L", "C1739")
    qty: int                  # bestellte Menge
    unit_price: float         # Preis pro Stück in der Order-Currency
    currency: str             # "EUR" für Mouser, "USD" für LCSC
    mpn: str                  # Manufacturer Part Number (aus File)
    mfr_name: str             # Hersteller (aus File)
    description: str          # Beschreibung (aus File)
    package: str = ""         # Package-Hint (LCSC liefert Spalte; Mouser nicht)

@dataclass
class SupplierOrder:
    """Eine komplette Bestellung."""
    supplier_name: str        # "Mouser" oder "LCSC"
    reference: str            # PO-Reference (Sales Order # bzw. Filename-Order-ID)
    order_date: str | None    # ISO-Datum aus File (Mouser hat es, LCSC nicht zuverlässig)
    currency: str             # Order-weite Default-Currency
    lines: list[SupplierOrderLine]
```

`SupplierOrderLine.sku` ist der **Lookup-Key** über Re-Runs hinweg — alle
Reconciliation-Diffs gehen über SKU.

### Parser

Zwei reine Funktionen ohne API-Aufrufe, voll unit-testbar:

```python
def parse_mouser_xls(path: Path) -> SupplierOrder
def parse_lcsc_csv(path: Path) -> SupplierOrder
```

**Mouser-XLS-Spalten** (Sheet `Order Details`):
`Sales Order No:` → `reference`. `Order Date:` → `order_date`. Pro Row:
`Mouser No:` → `sku`. `Order Qty.` → `qty`. `Price (EUR):` → `unit_price`
(String wie „€ 0,381" → 0.381 via `_parse_price` aus `fetchers.py`-Style).
`Mfr. No:` → `mpn`. `Desc.:` → `description`. `currency = "EUR"`.
`mfr_name` ist im File **nicht da** — wir lassen leer und ziehen es später
aus dem Mouser-API-Fetch (`MouserFetcher.fetch(sku).manufacturer`).

**LCSC-CSV-Spalten:** `Customer NO.` ist häufig leer; PO-Reference kommt
aus dem **Dateinamen** (`LCSC__WM2504270070_<timestamp>.csv` → reference
`WM2504270070`). Per Row: `LCSC Part Number` → `sku`. `Quantity` → `qty`.
`Unit Price($)` → `unit_price` (float-parse). `Manufacture Part Number` →
`mpn`. `Manufacturer` → `mfr_name`. `Package` → `package`. `Description` →
`description`. `currency = "USD"`. `order_date = None` (CSV hat keine
zuverlässige Datumsangabe, nur Updated-Lead-Time).

### Part-Resolution-Pipeline

```python
def ensure_part_for_order_line(
    api: InvenTreeAPI,
    line: SupplierOrderLine,
    lcsc_fetcher: LCSCFetcher,
    mouser_fetcher: MouserFetcher,
    lcsc_supplier: Company,
    mouser_supplier: Company,
    category_map: dict,
) -> tuple[Part, SupplierPart]:
    """
    Resolve eine SupplierOrderLine zu (Part, SupplierPart) — beides werden
    erzeugt falls nötig. Reuses bestehende Lib-Funktionen.
    """
```

Algorithmus (pro Line):

1. **SKU-basierter Existenz-Check** über `find_existing_part(api, lcsc_skus,
   mouser_skus)` aus `client.py`. Routing nach Line-Supplier:
   - Line aus Mouser → `mouser_skus=[sku]`, `lcsc_skus=[]`
   - Line aus LCSC  → `lcsc_skus=[sku]`, `mouser_skus=[]`
   Wenn Hit → Part da, nur SupplierPart-PK über `SupplierPart.list(api, SKU=sku)`
   zurückgeben.

2. **Supplier-API-Fetch** über `LCSCFetcher.fetch_by_sku(sku)` bzw.
   `MouserFetcher.fetch(sku)` → `PartData`. Bei Fail (API down, SKU unbekannt):
   PartData mit minimalen Feldern aus File-Row synthesisieren, Warning loggen
   — der Part wird trotzdem angelegt, halt ohne Datasheet/Image.

3. **MPN+Mfr-Dedup** über `find_part_by_mpn_and_manufacturer`. Hit → 
   `ensure_supplier_parts(api, existing_part, part_data, ..., 
   lcsc_skus=[sku] if LCSC else [], mouser_skus=[sku] if Mouser else [])`.
   Returnt den Part.

4. **Name-Dedup**: synthesisiere `name = part_data.mpn or line.mpn or sku`.
   `find_part_by_name(api, name)`. Hit → `ensure_supplier_parts` (analog Schritt 3).

5. **Create**: `resolve_part_category(api, kicad_part="", part_data, 
   footprint=line.package, category_map)` — der Lib-Path mit leerem
   kicad_part nutzt automatisch Supplier-Category bzw. „Miscellaneous"-
   Fallback. Dann `create_part_in_inventree(api, name, part_data, 
   category, lcsc_supplier, mouser_supplier, lcsc_skus, mouser_skus)`.

**Hinweis zum Logging:** `resolve_part_category` loggt bei `kicad_part=""`
ein WARNING („KiCad symbol '' not found in category map"). Das ist beim
Supplier-Import erwartet (keine KiCad-Symbole vorhanden) und würde pro
Part einmal feuern. Damit das Log nicht zugespammt wird, setzen wir vor
der Schleife einen Logger-Filter auf `inventree_sync.categories` der
genau diese Message auf DEBUG runterstuft. Ein einmaliges INFO am Anfang
informiert den User: „Importing supplier-order parts without KiCad context
— categories will fall back to supplier-provided or 'Miscellaneous'."

**SupplierPart-PK zurückgeben:** `create_part_in_inventree` returnt
`Part`, nicht den `SupplierPart`. Nach dem Create lookup per
`SupplierPart.list(api, SKU=sku)` → `[0].pk`. Bei den Existing-Pfaden
genauso, weil PurchaseOrderLineItem.part die `SupplierPart`-PK braucht,
nicht die `Part`-PK.

### PurchaseOrder-Reconciliation

```python
def upsert_purchase_order(
    api: InvenTreeAPI,
    order: SupplierOrder,
    supplier: Company,
    resolved_lines: list[tuple[SupplierOrderLine, SupplierPart]],
    receive_location: StockLocation,
    *,
    dry_run: bool = False,
) -> ReconciliationReport
```

**Schritt 1 — PO-Lookup**: `PurchaseOrder.list(api, supplier=supplier.pk,
reference=order.reference)`. Drei Pfade:

#### Pfad A — PO existiert nicht
1. `PurchaseOrder.create(api, {supplier: supplier.pk, reference: 
   order.reference, description: f"Imported from {supplier_name} order 
   {reference}", target_date: order_date or None})`.
2. PO ist in Status `PENDING` (10). Per `po.issue()` → `PLACED` (20).
3. Für jede `(line, supplier_part)`: `po.addLineItem(part=supplier_part.pk,
   quantity=line.qty, purchase_price=line.unit_price, 
   purchase_price_currency=line.currency, reference=line.sku)`.
4. `po.receiveAll(location=receive_location.pk, status=10)`. StockItems
   werden erzeugt; PO geht auf `COMPLETE` (30).

#### Pfad B — PO existiert in Status `PENDING` oder `PLACED`
1. Aktuelle LineItems holen: `existing = po.getLineItems()`.
2. Index nach `existing.reference` (entspricht der SKU, wir setzen sie
   beim Create in Pfad A so).
   **Fallback** für PO's, die ursprünglich ohne `reference` angelegt
   wurden: nach `existing.part` (SupplierPart-PK) indizieren und über die
   SupplierPart-SKU zurückmappen.
3. Diff zur Datei:
   - **SKU in Datei, nicht in PO** → `po.addLineItem(...)`.
   - **SKU in PO, nicht in Datei** → `LineItem.delete()`.
   - **SKU in beiden, `qty` oder `purchase_price` weicht ab** →
     `LineItem.save({"quantity": line.qty, "purchase_price": 
     line.unit_price})`.
   - **SKU in beiden, identisch** → no-op.
4. `po.issue()` falls noch `PENDING`. `po.receiveAll(...)` wie Pfad A.

#### Pfad C — PO existiert in Status `COMPLETE` (30) oder höher
1. Diff berechnen (gleiche Logik wie Pfad B Schritt 3).
2. **Diff leer** → `reconciliation.status = "in-sync"`, exit 0.
3. **Diff non-empty** → Loud Fail:
   ```
   ERROR: PO 275708282 (Mouser) ist COMPLETE, weicht aber von der Datei ab:
     ADD    576-XXX qty=5 € 0.42
     REMOVE 595-YYY qty=10 € 1.16 (10 StockItem(s) wären verwaist)
     UPDATE 667-ERA-6AEB221V qty 20→25 € 0.052
   Bitte im InvenTree-UI manuell auflösen oder PO + zugehörige
   StockItems löschen und Skript neu laufen lassen.
   ```
   `sys.exit(1)`. Kein automatischer Roll-Back von Receive (StockItems
   könnten in Builds verwendet werden — irreversibler Datenverlust).

### Receive-Location

InvenTree braucht für `receiveAll` eine `StockLocation`. Default-Strategie:
1. Lookup `StockLocation.list(api, name="Lager")` (Standard-Lager-Name
   in OE5XRX-DE-Setup).
2. Fallback: erstes Top-Level-Result von `StockLocation.list(api)`.
3. CLI-Override via `--location <name>` für Sonderfälle.

Empty-Result → Fail-Fast mit Hinweis "Lege erst eine StockLocation an,
sonst gibt's keine Receive-Destination".

### CLI

```
usage: import_supplier_order.py [-h]
    [--mouser-xls PATH] [--lcsc-csv PATH]
    [--location NAME] [--dry-run]
    [--categories YAML]

required: at least one of --mouser-xls or --lcsc-csv

env: INVENTREE_API_HOST, INVENTREE_API_TOKEN, MOUSER_API_KEY
```

Beide Flags optional, damit man nur Mouser ODER nur LCSC importieren kann,
oder beide auf einmal. `--categories` für custom Mapping (gleicher YAML-
Pfad wie `bom_export.py`). Kein `--planned-builds` — irrelevant, weil
keine Assembly betrachtet wird.

**Dry-Run-Output** (analog zu `DryRunReporter` in `inventree_sync/dry_run.py`):
```
=== Mouser PO 275708282 — Dry Run ===
Parts:
  REUSE   576-0297003.L          (existing pk=1234)
  REUSE   637-2N7002             (existing pk=1235, via MPN+Mfr)
  CREATE  511-STM32F302CBT7      (name='STM32F302CBT7')
  ...
PurchaseOrder:
  CREATE  PO ref=275708282 supplier=Mouser
  CREATE  27 LineItems
  RECEIVE 27 items into location 'Lager'
=== LCSC PO WM2504270070 — Dry Run ===
  ...
```

---

## Idempotenz-Garantien

| Re-Run-Szenario | Verhalten |
|---|---|
| Identische Datei, PO noch nicht da | PO + LineItems + StockItems werden angelegt. Exit 0. |
| Identische Datei, PO `PENDING`/`PLACED` mit gleichen Lines | No-op-Diff → Receive → COMPLETE. Exit 0. |
| Identische Datei, PO `COMPLETE`, Diff leer | „in-sync"-Log. Exit 0. |
| Geänderte Datei, PO `PENDING`/`PLACED` | Reconciliation: ADD/UPDATE/REMOVE LineItems → Receive. Exit 0. |
| Geänderte Datei, PO `COMPLETE` | Diff-Report + Exit 1, kein Write. |
| Datei mit neuem Part | Part wird angelegt (über volle Lib-Pipeline), LineItem auch. |
| Datei mit Part, der in InvenTree existiert aber ohne SupplierPart | `ensure_supplier_parts` linkt nach, LineItem auf neuen SupplierPart. |

---

## Error Handling

| Failure | Verhalten |
|---|---|
| Mouser-/LCSC-API down beim Fetch | Lib-Default: Warning loggen, Part wird mit File-Daten angelegt (kein Datasheet/Image). |
| `MOUSER_API_KEY` nicht gesetzt | Fail-Fast in `MouserFetcher.__init__` (bestehend) — Skript exit 1, falls Mouser-File übergeben wurde. Bei nur-LCSC-Run: kein Fail. |
| InvenTree-API down | Exception bubblet hoch, exit 1. Kein Partial-Write riskiert (PO-Create und Line-Adds sind separate Roundtrips). |
| Unbekannter SKU bei Mouser | Mouser-API liefert leer → `PartData` minimal aus File, Warning. Part wird trotzdem angelegt. |
| StockLocation nicht gefunden | Fail-Fast mit klarem Hinweis vor PO-Create. |
| PO-Create klappt, LineItem-Create fail | PO bleibt leer/teilweise befüllt. Re-Run macht Reconciliation Pfad B → vervollständigt. Daher exit 1 mit Hinweis "Re-Run um zu vervollständigen". |
| Receive-Step fail | PO bleibt in `PLACED` mit kompletten Lines. Re-Run vervollständigt das Receive. |
| Dry-Run mit fehlendem InvenTree-Token | Fail-Fast (Lib braucht API für Lookups auch im Dry-Run). |

---

## Tests

### Parser-Unit-Tests (`test_order_import_parsers.py`)

Pflichtfälle für `parse_mouser_xls` mit einem Fixture-XLS (10-zeiliger
Auszug aus der echten Datei, committed):

- Header-Felder: `reference == "275708282"`, `order_date == "2025-07-07"`
  (ISO-format aus „07-Jul-25"), `currency == "EUR"`.
- Preis-Parse: „€ 0,381" → 0.381. „€ 0,02" → 0.02. „€ 1,16" → 1.16.
- Empty-rows in XLS werden ignoriert (Excel hängt manchmal leere Rows an).
- MPN-Spalte mit Whitespace → strip.

Pflichtfälle für `parse_lcsc_csv` mit einem Fixture-CSV (10-zeiliger Auszug):

- Header-Felder: `reference == "WM2504270070"` (aus Filename), 
  `currency == "USD"`.
- Filename-Parse: `LCSC__<reference>_<timestamp>.csv` → reference,
  Fallback "lcsc-unknown" wenn Pattern nicht matched.
- Preis-Parse: „0.0074" → 0.0074. „1.2885" → 1.2885.
- Empty `Customer NO.` Spalte wird ignoriert.
- Trailing comma am Zeilen-Ende → keine Phantom-Spalte.

### Reconciliation-Tests (`test_order_import_reconcile.py`)

Mock-based gegen `inventree.purchase_order.PurchaseOrder`:

- Pfad A (kein PO da): Asserts auf `PurchaseOrder.create`-Call + N×
  `addLineItem` + 1× `receiveAll` + Status-Transition `PENDING → PLACED → COMPLETE`.
- Pfad B mit hinzukommender Line: bestehende N Lines, Datei hat N+1 →
  Assert 1× `addLineItem`, 0× delete.
- Pfad B mit entfallender Line: bestehende N Lines, Datei N-1 → 1× delete,
  0× addLineItem.
- Pfad B mit qty-Update: 1× `save({quantity, purchase_price})`.
- Pfad B No-op: alle Lines identisch → 0× Mutation, dann `receiveAll`.
- Pfad C in-sync: PO COMPLETE, Diff leer → 0× Mutation, exit 0.
- Pfad C drift: PO COMPLETE, Diff non-empty → kein Write, raises 
  `SystemExit(1)`. Stderr-Report enthält ADD/REMOVE/UPDATE-Zeilen.

### Integration-Test (manueller Run, nicht im CI-Pytest)

Gegen Local-InvenTree oder Staging-Instanz:
1. `./import_supplier_order.py --mouser-xls inventree_import/275708282.xls --dry-run`
   → Erwartet: Plan-Output mit 27 CREATE/REUSE-Decisions + 1 PO + 27 LineItems.
2. Ohne `--dry-run` ausführen → InvenTree-UI prüfen: PO 275708282 mit
   Status COMPLETE, 27 LineItems, alle Stock-Items angelegt.
3. Re-Run mit identischer Datei → „in-sync", keine Side-Effects.
4. Eine qty-Spalte im File händisch ändern → Re-Run → 
   Erwartet: Fail mit Drift-Report, weil PO bereits COMPLETE.

Test 4 explizit dokumentieren weil's das einzige nicht-idempotente Verhalten
ist und User es kennen müssen.

---

## Edge-Cases & Notes

### Mouser-SKU-Prefixe sind nicht MPNs
Mouser-SKUs wie `637-2N7002` enthalten einen Distributor-Prefix. Die Lib
hat `_strip_mouser_prefix` in `part_manager.py` — wir nutzen das **nicht**
für die SKU selbst (Mouser-SKUs werden as-is in InvenTree gespeichert als
`SupplierPart.SKU`), aber der Mouser-API-Fetcher kriegt die volle SKU
übergeben und sucht damit (`partSearchOptions: "Exact"`). Die `mpn`
kommt aus dem File-Feld `Mfr. No:` direkt — kein Prefix-Strip nötig.

### LCSC-Description ist gut, Mouser-Description ist redundant
Mouser-File-Desc hat oft das Category-Wort zweimal:
„Automotive Fuses Automotive Fuses 32V 3A MINI". Beim Part-Create lassen
wir File-Desc fallen und nutzen `PartData.description` aus der API
(sauberer). Bei API-Fail: File-Desc bleibt — wir trimmen leading-
duplicate-words nicht (zu fragil), den User stört's nicht in der
Übergangsphase.

### Currency-Tag pro LineItem
InvenTree's `purchase_price_currency` ist pro LineItem speicherbar.
Auch wenn die PO eine Default-Currency hat, taggt jedes LineItem
explizit. So bleiben Mouser-EUR und LCSC-USD getrennt nachvollziehbar.

### Connection mit Recipe-Server / SRCREV
Dieses Tool hat **keine** Berührung mit `linux-image` oder `station-agent`
— pure InvenTree-Migration. Keine SRCREV-Pin-Implikationen, keine OTA-
Auswirkungen.

### „Lager" hardcoded vs. Konfig
Der Default-StockLocation-Name „Lager" ist Domain-Konvention für die
deutsche InvenTree-Instanz. Falls die OE5XRX-Instanz das mal englisch
fährt, fällt `--location <name>` als Override ein. Eingebauter Fallback
auf erstes Top-Level-Result verhindert harte Crashes.

---

## Out-of-Scope (für ein späteres Tool)

- Bulk-Import von Bestellungen via Verzeichnis-Watch (`--input-dir`).
- Stocktake-Reconciliation (StockItem-Mengen gegen physische Zählung).
- Cost-Report über die Importe (bestehend in `cost_report.py` für BOMs;
  Order-Cost macht InvenTree's UI nativ).
- Sales-Order-Counterpart (`sales_order.py` in `inventree`-lib).
