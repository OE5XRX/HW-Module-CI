# InvenTree-Sync Improvements — Backlog

**Status:** Backlog / Roadmap (kein einzelner Spec — jedes Feature kriegt
seinen eigenen Spec + Plan, wenn es drankommt).
**Scope:** `scripts/bom_export.py` und `scripts/inventree_sync/` Package.
**Context:** Aktuell ist die `bom_export`-Stufe im
`create-release-docs.yaml`-Workflow auskommentiert, weil der OE5XRX-
InvenTree-Server im Mai 2026 dekommissioniert wurde. Diese Liste sammelt
Verbesserungen, die bei der Re-Aktivierung umgesetzt werden sollen.
**Erstellt:** 2026-06-03 (Brainstorm-Review).

---

## Reihenfolge

Empfehlung für die **erste Re-Activation-PR**, sobald InvenTree zurück ist:
Punkte 1, 2, 3, 4 (Teilmenge: Schematic-PDF + Gerber als Attachment), 5.
Das ist ein zusammenhängendes Paket, lässt sich gegen einen Test-Server
mergen, und bringt sofortigen Mehrwert.

Danach in weiteren PRs: 6 → 7 → 8 → 9 → 10 → 11 → 12.
Mittlere Priorität (13–18) eingestreut nach Bedarf. Lo-Pri (19–21) on
demand.

---

## Hoch-Priorität (must-do bei Re-Activation)

### 1. Bug: Mouser-Image-Download schlägt immer fehl
**Symptom:** Bei Bauteilen, die nur einen Mouser-SKU haben (kein LCSC),
wird kein Bild in InvenTree gespeichert.

**Root Cause (am 2026-06-03 lokal verifiziert mit ESP32-D0WDRH2-V3 via
mouser.at):** Mouser betreibt PerimeterX-Bot-Protection vor
`www.mouser.com/images/...` und `www.mouser.at/images/...`. PerimeterX
prüft auf einen kompletten Browser-Fingerprint inkl. moderner Fetch-
Metadata-Header. Der aktuelle Code sendet einen iOS-Safari-UA und nur
einen (falschen, cross-origin) Referer — das reicht PerimeterX nicht
und es kommt eine 4592-Byte „Access denied"-HTML-Seite mit HTTP 200
zurück (die dann fälschlicherweise als „Bild" hochgeladen würde).

**Fix-Strategie (Simple Header-Update):**
Das minimale Header-Set für Mouser ist exakt isoliert worden (Drop-One-
und Value-Variation-Tests). Sechs Header sind **alle mandatory** — aber
fast alle Werte sind völlig egal, PerimeterX prüft nur die *Präsenz*.

| Header | Wert egal? | Was funktioniert | Was blockt |
|---|---|---|---|
| `User-Agent` | **Nein** | Browser-shaped UA (Chrome, Firefox, Safari, iOS) | `curl/X`, `python-requests/X` (Connection wird vorm Body gekillt), bloßes `Mozilla/5.0` |
| `Accept-Language` | Ja | `en`, `xx`, `garbage`, `*`, `0` | Header nicht senden |
| `Referer` | Ja | `https://www.mouser.com/`, `https://www.lcsc.com/`, `garbage`, `x` | Header nicht senden |
| `Sec-Fetch-Dest` | Ja | `image`, `xxx`, `garbage` | Header nicht senden |
| `Sec-Fetch-Mode` | Ja | `no-cors`, `cors`, `xxx` | Header nicht senden |
| `Sec-Fetch-Site` | Ja | `same-origin`, `cross-site`, `none`, `xxx` | Header nicht senden |

`Accept: image/*` ist optional; mit `image/webp` schickt Mouser WebP,
ohne JPG (beides ist ein echtes Bild).

PerimeterX prüft *Browser-Fingerprint*-Strukturen: ein „echter" Browser
sendet seit Chrome 76 (2019) `Sec-Fetch-*` automatisch und hat
zwangsläufig UA + Accept-Language + Referer. Bots/Scripts haben das
typischerweise nicht — billiger, effektiver Filter.

Auch `wget` funktioniert mit denselben Headern (verifiziert).

**Konkrete Änderungen:**
- `client.py:upload_image_from_url`:
  - Desktop-Chrome-UA statt `_IOS_UA` für nicht-LCSC-URLs.
  - `Accept-Language` und die drei `Sec-Fetch-*` Header hinzufügen.
  - Host-abhängiger Referer: LCSC → `lcsc.com`, Mouser → `mouser.com`,
    sonst weglassen.
- **Validierung nach Download:** `Content-Type` muss mit `image/`
  starten. Sonst war's der PerimeterX-Block, nicht hochladen, WARN.
- Optional Bonus: Mouser-API liefert `ImagePath` typischerweise als
  `_SPL.jpg` (klein, 150px). URL-Transform `/images/` → `/hd/` holt
  die ~1000px-Variante (auch verifiziert).

**Files:** `scripts/inventree_sync/client.py`.

---

### 2. Bug: PCB- und Stencil-Part-Revision dupliziert sich pro Minor-Release
**Symptom:** Bei jedem Minor-Release (z.B. v1.1 → v1.2) entsteht ein neuer
PCB-Part und ein neuer Stencil-Part in InvenTree, obwohl die physische
Platine byte-identisch ist.

**Root Cause:** `bom_export.py:create_pcb_part` und
`create_stencil_part` setzen `revision=args.version` — also den vollen
MAJOR.MINOR-String. Verletzt die in der HW-Module-CI-README dokumentierte
Konvention („PCB is byte-identical across all Minor bumps", silkscreen
zeigt `v<MAJOR>`).

**Fix:**
- PCB- und Stencil-Part-Revision = `v<MAJOR>` (z.B. `v1`), nicht `1.2`.
- Vor dem `Part.create` prüfen, ob `<name> PCB rev v1` schon existiert.
  Wenn ja → wiederverwenden und nur die BOM neu verlinken.
- Assembly-Part-Revision bleibt MAJOR.MINOR (z.B. `1.2`).

**Files:** `scripts/bom_export.py` (Funktionen `create_pcb_part`,
`create_stencil_part`), evtl. neue Helper in `inventree_sync/client.py`
für das „existiert schon?" Lookup.

---

### 3. Bug: `find_part_by_name` matcht zu lax + lädt zuviel
**Symptom:** Potenziell falsche Treffer bei Substring-Matches; bei
SupplierPart-Lookup wird die komplette Liste geladen.

**Root Cause:**
- `client.py:find_part_by_name` nutzt `Part.list(api, search=name)` —
  das ist InvenTree-seitig eine Substring-Search. Nachfilter mit
  `if part.name == name` ist da, aber bei Pagination kommt der exakte
  Treffer evtl. gar nicht in den ersten N Ergebnissen.
- `bom_export.py:match_supplier_parts` lädt `SupplierPart.list(api)`
  ohne Filter → O(N) über alle Server-SKUs bei jedem Release.

**Fix:**
- `Part.list(api, name=name)` (exact filter im InvenTree-API).
- SupplierPart-Lookup mit `SKU__in=[...]`-Filter, statt All-fetch.

**Files:** `scripts/inventree_sync/client.py`, `scripts/bom_export.py`.

---

### 4. Bug: Nur erster SKU einer Mehrfach-Liste wird benutzt
**Symptom:** Wenn ein BOM-Eintrag mehrere LCSC- oder Mouser-SKUs als
Alternativen listet, wird nur der erste angelegt. Alternativen gehen
still verloren.

**Root Cause:** `part_manager.py:125-126`:
```python
lcsc_sku = lcsc_skus[0] if lcsc_skus else ""
mouser_sku = mouser_skus[0] if mouser_skus else ""
```

**Fix:** Für jeden zusätzlichen SKU einen weiteren `SupplierPart`
anlegen (gleicher Part, anderer SKU). Die Logik in
`create_part_in_inventree` muss eine SKU-Liste statt eines Single-SKU
nehmen.

**Files:** `scripts/inventree_sync/part_manager.py`,
`scripts/inventree_sync/client.py`.

---

### 5. Bug: `match_supplier_parts` lädt alle SupplierParts in Speicher
**Symptom:** Performance / API-Last bei großem InvenTree-Katalog.

**Fix:** Siehe Punkt 3 — gemeinsam fixen.

---

### 6. Feature: Parameter aus LCSC/Mouser nach InvenTree übertragen ⭐
**Pain:** `LCSCFetcher._parse` füllt `PartData.parameters` mit
Resistance, Tolerance, Voltage, Package, Temperature-Range, …; dieser
Wert wird **nirgends** in InvenTree gespeichert. Damit ist die InvenTree-
Suchfunktion („alle 100nF/0805/X7R/50V") nutzlos.

**Fix:**
- `PartParameterTemplate` für jeden vorkommenden Parameter-Namen
  on-the-fly anlegen (idempotent).
- `PartParameter`-Records pro Part anlegen mit (template, value).
- Mouser-Fetcher um `ProductAttributes`-Parsing erweitern (Mouser API
  liefert dieselben Felder unter anderem Namen).

**Files:** `scripts/inventree_sync/client.py` (neue Funktion
`upload_parameters`), `scripts/inventree_sync/fetchers.py`
(Mouser-Parameter-Mapping).

---

### 7. Feature: Supplier-Link auf SupplierPart befüllen
**Pain:** `PartData.supplier_link` ist im Dataclass deklariert, wird nie
befüllt und nie genutzt. InvenTree-SupplierPart hat ein `link`-Feld für
die Produktseiten-URL → Click-through zur LCSC/Mouser-Seite direkt aus
InvenTree.

**Fix:**
- LCSC: `https://www.lcsc.com/product-detail/{lcsc_sku}.html` (oder
  was die API als `productUrl` liefert).
- Mouser: aus `ProductDetailUrl` oder konstruiert
  `https://www.mouser.com/ProductDetail/{mouser_sku}`.
- Beim `SupplierPart.create` als `link` mitgeben.

**Files:** `scripts/inventree_sync/fetchers.py`,
`scripts/inventree_sync/client.py`.

---

### 8. Feature: KiBot-Outputs als InvenTree-Attachments
**Pain:** KiBot produziert Schematic-PDF, BOM-HTML, iBOM-HTML, Gerber-
ZIP, STEP, 3D-Renderings. Nichts davon landet in InvenTree.

**Fix:**
- **PCB-Part:** Gerber-ZIP, STEP, Pick-&-Place CSV, 3D-Top/Bottom-PNGs.
- **Assembly-Part:** Schematic-PDF, BOM-HTML, iBOM-HTML, GitHub-Release-
  URL, 3D-Renderings inkl. „with"/„without" components.
- **Stencil-Part:** Stencil-SVG/PNG.

InvenTree API: `Part.uploadAttachment(file, comment)`.

**Files:** `scripts/bom_export.py` (neue CLI-Argumente für die Pfade,
neuer Loop), evtl. neue `attach_outputs.py`.

---

### 9. Feature: Update-/Refresh-Modus
**Pain:** Existiert ein Part schon, werden nur fehlende SupplierParts
hinzugefügt. Preise, Bilder, Datasheets, Parameter werden **nie**
aktualisiert. Nach 1 Jahr ist der Katalog mit toten LCSC-Bildlinks und
2024er-Preisen voll.

**Fix:**
- CLI-Flag `--refresh` an `bom_export.py` (oder eigenes Script
  `inventree_refresh.py`).
- Über alle existierenden Parts iterieren, Supplier-API neu abfragen,
  Preisbreaks/Bilder/Datasheets/Parameter aktualisieren.
- Optional als nightly cron-Job laufen lassen (eigener GH-Actions-
  Workflow `scheduled-inventree-refresh.yaml`).

**Files:** neuer Script + Workflow.

---

### 10. Feature: Dry-Run / Preview-Modus
**Pain:** Bei fehlerhafter Categories-YAML kippen 80 Parts in
„Miscellaneous" und müssen händisch aufgeräumt werden.

**Fix:** CLI-Flag `--dry-run` an `bom_export.py`. Statt zu erstellen,
Tabelle ausgeben:
```
Would CREATE: 'R 10k 0805' in Resistors/Surface Mount/0805
              (LCSC C17414, €0.0012@100)
Would REUSE:  'C 100nF 0805' (existing pk=4221)
Would SKIP:   'Conn_01x02' (no SKU)
Would FAIL:   'BAT54' (no supplier data found)
```

**Files:** `scripts/bom_export.py`, `scripts/inventree_sync/part_manager.py`.

---

### 11. Feature: Cost-Report nach Sync
**Pain:** Die Preisstaffeln sind komplett gefetcht. Vereinsmitglieder
wollen sehen, was eine Platine kostet.

**Fix:** Nach `populate_bom` einen Markdown-Cost-Report generieren:
```
| Qty | Total | per-Board |
|-----|-------|-----------|
| 1   | €58.20 | €58.20  |
| 10  | €38.50 | €3.85   |
| 100 | €24.10 | €0.241  |
```
- Als GitHub-Step-Summary ausgeben (`$GITHUB_STEP_SUMMARY`).
- Als InvenTree-Notiz am Assembly-Part hinterlegen.
- Optional: als JSON-Artifact für maschinelle Weiterverarbeitung.

**Files:** neue `cost_report.py`, Aufruf aus `bom_export.py`.

---

### 12. Feature: Stencil-Geometrie als Attachment am Stencil-Part
**Pain:** Stencil-Part ist als „SMT Stencil"-Kategorie eingerichtet,
hat ein Render-PNG, aber **keine** Stencil-SVG / DXF als Attachment.
Der Part ist eine Karteileiche.

**Fix:** Stencil-SVG (Output von KiBot) und die JLCPCB-Stencil-Spec
als Attachments am Stencil-Part anhängen.

**Files:** Teil von Punkt 8.

---

## Mittel-Priorität (eingestreut nach Bedarf)

### 13. Feature: Dedup-Key = MPN+Manufacturer
**Pain:** Heute matched `find_part_by_name` über den generierten Namen
— `10k` vs `10K` vs `10kΩ` erzeugt Duplikate.

**Fix:** Nach `ManufacturerPart(MPN, Manufacturer)` suchen — eindeutiger
Schlüssel. Fallback bleibt der generierte Name.

**Files:** `scripts/inventree_sync/client.py`.

---

### 14. Feature: Pattern-/Regex-basierte Category-Map
**Pain:** Pro neuem MCU-Variant manuelles YAML-Editing
(`STM32U575CITx` → MCU, `STM32U575RIT6` → ?).

**Fix:** Zusätzlich zu exakten Keys auch Regex-Patterns in der YAML
erlauben:
```yaml
patterns:
  - { regex: '^STM32.*',   category: [Integrated Circuits, Microcontroller] }
  - { regex: '^LMR\d.*',   category: [Power Management, Buck] }
```
Exakte Keys haben Vorrang, dann werden Patterns durchprobiert.

**Files:** `scripts/inventree_sync/categories.py`,
`scripts/inventree_sync/default_categories.yaml`.

---

### 15. Feature: Minimum-Stock aus BOM-Qty setzen
**Pain:** „Low Stock"-Page in InvenTree ist nutzlos, weil nichts
einen Minimum-Bestand definiert.

**Fix:** Beim Anlegen eines BomItems den `minimum_stock`-Wert am
Part setzen = `Quantity Per PCB` × `geplante Stückzahl` (Default 10,
per CLI überschreibbar). Wenn der Part schon einen
`minimum_stock` > 0 hat, höher gewinnt.

**Files:** `scripts/bom_export.py`.

---

### 16. Feature: Aggregierte Fehlerausgabe statt früher `sys.exit(1)`
**Pain:** Im 80-Zeilen-BOM stoppt das Skript beim ersten nicht-
matchbaren Part. Man sieht nicht, wieviele weitere noch fehlen.

**Fix:** Fehler sammeln, am Ende einen Bericht ausgeben, dann erst
non-zero exiten.

**Files:** `scripts/bom_export.py`, `scripts/inventree_sync/part_manager.py`.

---

### 17. Feature: Retry mit Backoff auf LCSC/Mouser-APIs
**Pain:** Ein 502 von LCSC bei einem von 80 Parts → Part fehlt →
`sys.exit(1)`.

**Fix:** `urllib3.util.Retry` mit 3 Versuchen + exponentiellem Backoff,
sowohl im `LCSCFetcher` als auch im `MouserFetcher`.

**Files:** `scripts/inventree_sync/fetchers.py`.

---

### 18. Feature: Source-Commit / Release-URL als Custom-PartParameter
**Pain:** Wer in 3 Jahren ein Modul reparieren will, hat von InvenTree
keinen Pfad zur Quell-Repo-Revision.

**Fix:** Custom-Parameter am Assembly-Part:
- `source_commit = "OE5XRX/HW-Module-FMTransceiver@abc1234"`
- `release_url = "https://github.com/.../releases/tag/1.2"`

Werte aus `$GITHUB_SHA` und `$GITHUB_REF_NAME` im Workflow ableiten und
als zusätzliche CLI-Args übergeben.

**Files:** `scripts/bom_export.py`, Workflow-YAML.

---

## Niedrig-Priorität (wenn Zeit übrig)

### 19. Feature: Wert-Normalisierung im Namensgenerator
**Pain:** `10k`, `10K`, `10 k`, `10kΩ` ergeben verschiedene Part-Namen.

**Fix:** In `categories.generate_part_name`:
- Unicode-Omega entfernen
- `K` → `k`, `M` → `M` (wegen Mega vs milli — vorsicht)
- Whitespace komplett raus zwischen Zahl und Einheit
- Optional auch SI-Prefix-Vereinheitlichung (`1000` → `1k`).

**Files:** `scripts/inventree_sync/categories.py`.

---

### 20. Feature: Webhook / PR-Comment nach erfolgreichem Sync
**Pain:** Sync läuft durch, keine Sichtbarkeit auf den frisch
angelegten Assembly-Part.

**Fix:** Nach erfolgreichem Sync ein `gh pr comment` oder
GitHub-Step-Summary mit:
- Link zum Assembly-Part in InvenTree
- Cost-Report (Punkt 11)
- Liste der neu angelegten Parts

**Files:** `scripts/bom_export.py` (oder reiner Workflow-Step).

---

### 21. Pytest-Tests für `inventree_sync`
**Pain:** `scripts/tests/` enthält heute nur Tests für
`archive_previous_major` und `compute_next_version`. Der gesamte
Inventree-Sync-Pfad ist ungetestet.

**Fix:**
- Mock-Responses für LCSC und Mouser (gespeicherte JSON-Files).
- Mock-InvenTreeAPI über `unittest.mock`.
- Tests pro Datei: `fetchers`, `categories`, `client`, `part_manager`.
- Mindest-Coverage: alle Happy-Paths, alle Bug-Reproducer aus Punkten 1-5.

**Files:** `scripts/tests/test_inventree_sync_*.py`,
neue Fixture-Verzeichnisse mit JSON-Snapshots.

---

## Nicht in der Auswahl (zur Referenz; bewusst zurückgestellt)

Diese Punkte aus dem ursprünglichen Brainstorm wurden **nicht** in die
Auswahl aufgenommen — können später nachgezogen werden, wenn sie
relevant werden:

- DigiKey-Fetcher (nicht primärer Distributor)
- MPN-Spalte in BOM-CSV (Schaltplan-Konvention müsste mit-geändert
  werden)
- Octopart/Nexar als Aggregat-Fallback (kostet Geld)
- Preferred-Supplier-Flag via Preis (Komfort, nicht Kern)
- JSON-Report (durch Punkt 11 teilweise abgedeckt)
- MOQ / minBuyNumber speichern
- Stock-Settings sind durch Punkt 15 abgedeckt
