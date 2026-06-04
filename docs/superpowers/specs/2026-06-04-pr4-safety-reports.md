# PR-4: Safety & Reports — Spec

**Branch:** `feat/safety-reports`
**Backlog-Refs:** [`2026-06-03-inventree-sync-improvements-backlog.md`](./2026-06-03-inventree-sync-improvements-backlog.md) Punkte #9, #10, #11
**Status:** Spec — bereit für Plan

## Scope

Drei Features die die Sicherheit und Sichtbarkeit der InvenTree-Sync-Pipeline erhöhen:

- **#9 — Refresh-Mode:** Standalone-Skript + nightly cron, refreshed Preise/Bilder/Parameter aller existierenden LCSC- und Mouser-Parts.
- **#10 — Dry-Run / Preview:** `--dry-run` Flag an `bom_export.py`, simuliert den ganzen Flow ohne Side-Effects.
- **#11 — Cost-Report:** Nach `populate_bom` generiert ein Markdown-Cost-Report, landet in GH-Actions Step-Summary und im Notes-Feld des Assembly-Parts.

## Section 1: Refresh-Mode (#9)

### Architektur

Eigenständiges Skript `scripts/inventree_refresh.py`. Klare Separation of Concerns: `bom_export.py` ist Release-trigger, `inventree_refresh.py` ist Maintenance.

### Flow

1. Connect zu InvenTree.
2. Hole alle `SupplierPart` mit Supplier-Namen ∈ {LCSC, Mouser}.
3. Gruppiere nach `Part.pk` → unique Parts-Set.
4. Pro Part:
   - Iteriere über die zugeh. SupplierParts, sammle SKUs pro Supplier.
   - Fetch frische Daten via `_fetch_and_merge(lcsc_fetcher, mouser_fetcher, lcsc_skus[0], mouser_skus[0])` (Wiederverwendung des PR-2/PR-3 Code-Pfads).
   - Wenn fetch erfolgreich:
     - **Image:** re-download wenn der CDN-Link aktualisiert wurde — via `upload_image_from_url(part, part_data.image_url)`.
     - **Datasheet-URL:** aktualisiert `Part.link` wenn neuer Wert.
     - **Description:** nur setzen wenn aktuell leer (konservativ — manuelle Beschreibungen erhalten).
     - **Parameters:** `upload_parameters(api, part, part_data.parameters)` mit delta-sync (gleiches Pattern wie PR-3).
     - **Price-Breaks pro SupplierPart:** alte löschen + neue setzen via `_add_price_breaks`.
5. Loggt am Ende `N parts refreshed, M skipped (no supplier data), K errors`.

### Re-Sync-Semantik

Dieselbe wie PR-3 Parameter-Sync: Supplier ist Source of Truth. Manuelle UI-Edits an Parameter-Werten werden überschrieben. Description bleibt unverändert wenn schon befüllt (konservativ).

### Workflow

Neuer `.github/workflows/scheduled-inventree-refresh.yaml`:
- `on: schedule: cron: '0 3 * * *'` (nightly 03:00 UTC) + `workflow_dispatch` für manuell.
- Step: `python3 scripts/inventree_refresh.py`.
- `continue-on-error: true` (Wartungs-Run, kein Release-Blocker).
- Secrets: `INVENTREE_API_HOST`, `INVENTREE_API_TOKEN`, `MOUSER_API_KEY`.

### Rate-Limiting

Keine custom limits — verlassen uns auf `_fetch_and_merge`. Mouser-API-Cap ~30 req/min; bei 200 Parts ~6-7 min Fetch. Falls Rate-Limit-Issues auftauchen, ein simples `time.sleep(2)` zwischen Parts. Anfangs nicht eingebaut; Backlog-Punkt #17 (Retry mit Backoff) deckt das ab wenn nötig.

### Out of Scope für #9

- Diff-Reporting (zeigen was sich geändert hat) — eigene PR
- Notification (Slack/Discord wenn Refresh-Errors) — eigene PR
- Per-Category filtering — bewusst rausgehalten, einfache Semantik first

## Section 2: Dry-Run-Mode (#10)

### Architektur

Neuer `--dry-run` CLI-Flag an `bom_export.py`. Plus neuer `DryRunReporter`-Klasse in `scripts/inventree_sync/dry_run.py`.

### Mechanismus

Statt InvenTree-API-Calls auszuführen werden Decisions als "would-do" Records gesammelt. Code-Pfade in `ensure_parts_exist` + `match_supplier_parts` + Assembly/PCB/Stencil-Creation prüfen einen optionalen `reporter` Parameter:

- Wenn `reporter is not None`: `reporter.record(action, target, detail)` statt API-Call. Return Stub-Objekt mit predictable `pk=-1`.
- Wenn `reporter is None`: bisheriges Verhalten unverändert.

### Output-Format

Auf stdout (Pretty-Print Markdown-ish):

```
DRY-RUN: bom_export FMTransceiver v1.2

Parts:
  Would REUSE:  R 10k 0805 (existing pk=4221)
  Would CREATE: STM32U575RIT6 in Integrated Circuits/Microcontroller (LCSC C4567890)
  Would SKIP:   Mounting_Hole — no SKU
  Would FAIL:   CUSTOM_PART — no supplier data found

PCB / Assembly / Stencil:
  Would REUSE:  FMTransceiver PCB rev 1.2 (pk=42)
  Would CREATE: FMTransceiver Module rev 1.2
  Would REUSE:  FMTransceiver SMT Stencil rev 1.2 (pk=43)

BOM-Items (Assembly):
  Would CREATE: 47 items, would SKIP: 2 (already present)

Summary: 12 new parts, 45 reused, 1 skipped, 1 would-fail.
EXIT: 1 (would-fail present)
```

### Side-Effects-Free

Im Dry-Run **kein** Supplier-Fetch (kein LCSC/Mouser API-Call). Wir wollen Speed + Repeatability, nicht Korrektheit der Daten-Vorhersage. Daher zeigen wir nur die Lookup-Entscheidungen (REUSE/CREATE/SKIP/FAIL), nicht die geplanten Feld-Werte.

### Klarstellung: Exit-Code

"Would-FAIL" entspricht dem heutigen `sys.exit(1)` bei Missing-Parts. Dry-Run gibt am Ende Exit-Code 1 zurück wenn eine FAIL-Zeile produziert wurde — so kann CI vor dem echten Release vor-prüfen ob alle Parts auflösbar sind.

### Idempotency

Da Dry-Run side-effect-free ist, ist Idempotency trivial — beliebig oft re-runnbar ohne Risiko.

## Section 3: Cost-Report (#11)

### Architektur

Neues Module `scripts/inventree_sync/cost_report.py`. Wird am Ende von `bom_export.py:main` (nach `populate_bom`) aufgerufen wenn nicht im Dry-Run.

### Public-API

```python
def generate_cost_report(
    api: InvenTreeAPI,
    assembly: Part,
    entries: list[BomEntry],
    tiers: list[int] = [1, 10, 100],
) -> str:
    """Generate Markdown cost report from BOM entries' price_breaks.

    Returns Markdown string. Writes it to $GITHUB_STEP_SUMMARY if env-var
    exists, and patches it onto assembly.notes via Part.save({"notes": ...}).
    """
```

### Daten-Source

Pro BomItem: `Quantity × Cheapest-Price-At-Tier`. Daten kommen aus den InvenTree-Objekten die wir gerade in `populate_bom` befüllt haben — keine zusätzlichen Supplier-Fetches.

Pro Sub-Part wird über alle SupplierParts iteriert, alle Price-Breaks gesammelt, das günstigste passende Price-Break pro Tier (Quantity ≤ benötigte Qty) gewählt.

### Tiers

Fix `[1, 10, 100]`. Deckt "Prototyp / Verein / Größeres Batch" ab.

### Output 1 — GitHub-Actions Step-Summary

```markdown
## BOM Cost Report — FMTransceiver v1.2 (Assembly pk=42)

| Qty | Total | per-Board | Sources                  |
|-----|-------|-----------|--------------------------|
| 1   | €58.20 | €58.20   | LCSC (45), Mouser (2)    |
| 10  | €38.50 | €3.85    | LCSC (47)                |
| 100 | €24.10 | €0.241   | LCSC (47)                |

**BOM items:** 47 total — 2 had no price data (`R_Custom`, `XTAL_Custom`).
```

Geschrieben nach `$GITHUB_STEP_SUMMARY` falls die Env-Var existiert (in GitHub Actions automatisch gesetzt).

### Output 2 — InvenTree Notes am Assembly-Part

Dieselbe Markdown-Tabelle via `Part.save({"notes": markdown})`. Re-Run derselben Revision → identische Tabelle, idempotent.

### Missing-Price-Handling

Parts ohne Preisdaten werden gezählt und unter der Tabelle aufgelistet (Reference + Name). Nicht-blocking. Cost-Report wird trotzdem generiert mit den Parts die Preise haben.

### Cheapest-Tier-Logik

Pro (Part, Qty) wählen wir das günstigste SupplierPart-Price-Break dessen Quantity ≤ benötigte Qty.

**Beispiel:** Ein Part hat:
- LCSC: €0.10 @ qty=10, €0.08 @ qty=100
- Mouser: €0.12 @ qty=1, €0.06 @ qty=500

Für Tier 1: LCSC ist nicht <10 verfügbar → Mouser €0.12 wins.
Für Tier 10: LCSC €0.10 wins (kein gültiger Mouser-Break ≤10).
Für Tier 100: LCSC €0.08 wins (Mouser hat erst @500).

"Sources"-Spalte zählt wieviele Items pro Supplier kommen pro Tier.

## Section 4: Files & Tests

### Files

| File | Action | Reason |
|---|---|---|
| `scripts/inventree_refresh.py` | Create | Standalone Refresh-Script |
| `scripts/inventree_sync/cost_report.py` | Create | `generate_cost_report(api, assembly, entries, tiers)` |
| `scripts/inventree_sync/dry_run.py` | Create | `DryRunReporter` Klasse + Print-Format |
| `scripts/bom_export.py` | Modify | `--dry-run` Flag; nach `populate_bom` Aufruf von `generate_cost_report` |
| `scripts/inventree_sync/part_manager.py` | Modify | Optional `reporter` Parameter; im Dry-Run `reporter.record()` statt API-Call |
| `scripts/e2e_revision_handling.py` | Modify | 3 neue Tests |
| `scripts/tests/test_cost_report.py` | Create | Pure-Python Unit-Tests |
| `scripts/tests/test_dry_run_reporter.py` | Create | Pure-Python Unit-Tests |
| `.github/workflows/scheduled-inventree-refresh.yaml` | Create | Nightly cron + workflow_dispatch |
| `.github/workflows/create-release-docs.yaml` | Modify | Cost-Report wird automatisch via Step-Summary sichtbar — keine separate Workflow-Änderung nötig wenn bom_export.py die $GITHUB_STEP_SUMMARY-Env selber schreibt |

### Testing-Strategie

1. **Pytest** (existing 73 + ca. 10 neue) → ~83 grün.
2. **`py_compile`** über alle Files.
3. **E2E** erweitert um 3 neue Tests:
   - `test_dry_run_no_side_effects`: bom_export.py mit `--dry-run` gegen kleinen Test-BOM → keine Parts angelegt; stdout enthält erwartete Patterns; Exit-Code korrekt.
   - `test_cost_report_generation`: Test-Assembly mit Test-Parts + Preisstaffeln, `generate_cost_report` → Markdown enthält die 3 Tier-Zeilen + korrekte Totals; Assembly-Notes wurden gesetzt (via API-Re-Fetch verifiziert).
   - `test_refresh_idempotent`: Part mit altem Parameter-Wert anlegen, `inventree_refresh.py` ausführen, Parameter geupdated. Zweiter Run → keine Änderung.

4. **Manueller E2E post-merge** bei nächstem realen Release + nightly Refresh-Run nach dem Merge.

### Out of Scope (für PR-4)

- Wert-Normalisierung der Parameter (Teil von #19)
- Aggregierte Fehlerausgabe (#16)
- Retry-with-Backoff (#17)
- Source-Commit Tracking (#18)
- Per-Category Filter im Refresh
- Diff-Reporting beim Refresh ("was hat sich geändert")
- ManufacturerPart-Linking auf SupplierParts (PR-3 Followup)
- Custom Tiers via CLI

### Erwarteter Diff-Umfang

- Code: ~+400/-30 Zeilen
- E2E + Pytest: ~+200 Zeilen
- YAML: ~+60 Zeilen

## Akzeptanzkriterien

- [ ] `scripts/inventree_refresh.py` existiert, läuft eigenständig gegen InvenTree, refreshed alle Parts mit LCSC/Mouser SupplierPart
- [ ] `.github/workflows/scheduled-inventree-refresh.yaml` läuft nightly + manuell triggerbar
- [ ] `bom_export.py --dry-run` produziert Pretty-Print stdout-Output, keine Side-Effects, korrekter Exit-Code
- [ ] `DryRunReporter` Klasse sammelt Decisions; `record(action, target, detail)` + `print_report()`
- [ ] `generate_cost_report` produziert Markdown mit Tiers [1,10,100] aus existing InvenTree-Daten
- [ ] Cost-Report landet in `$GITHUB_STEP_SUMMARY` (wenn Env-Var existiert) + Assembly.notes
- [ ] Cheapest-pro-Qty Logik korrekt (verifiziert per Pytest)
- [ ] 3 neue E2E-Tests + 2 neue Pytest-Files grün
- [ ] pytest-Suite weiter grün (~73→~83)
- [ ] py_compile clean
