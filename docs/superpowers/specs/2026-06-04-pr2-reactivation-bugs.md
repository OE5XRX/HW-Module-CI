# PR-2: Re-Activation Bug-Fixes — Spec

**Branch:** `fix/reactivation-bugs`
**Backlog-Refs:** [`2026-06-03-inventree-sync-improvements-backlog.md`](./2026-06-03-inventree-sync-improvements-backlog.md) Bugs #2, #3, #4, #5
**Status:** Spec — bereit für Plan

## Scope

Vier zusammenhängende Bug-Fixes im InvenTree-Sync, die zusammen die Re-Activation-Pipeline robust und idempotent machen.

- **Bug #2:** PCB/Stencil/Assembly-Part-Revisionen werden korrekt versioniert + idempotent re-runnable
- **Bug #3:** `find_part_by_name` macht Substring-Match (falsch) statt exact-Filter
- **Bug #4:** Multi-SKU-Datenverlust — nur der erste SKU einer BOM-Alternativen-Liste wird angelegt
- **Bug #5:** `match_supplier_parts` lädt komplette SupplierPart-Tabelle (O(N) pro Release)

Bugs #3 + #5 werden zusammen gefixt (selber Code-Bereich, gemeinsame Lookup-Modernisierung).

## Section 1: Revision-Strategie (Bug #2)

### Aktuelles Verhalten

`bom_export.py:create_pcb_part`, `create_stencil_part`, `create_assembly_part` setzen `revision=args.version` (Full-Tag, z.B. `1.2`). Jeder Release-Run versucht eine neue Part-Anlage:
- Beim normalen Minor-Bump (v1.1 → v1.2): neue PCB-/Stencil-/Assembly-Parts werden angelegt — funktioniert beim ersten Mal pro Tag.
- Beim Re-Run desselben Tags (z.B. CI-Hiccup): InvenTree's unique-Constraint auf (name, revision) bricht alles ab.

### Neues Verhalten

**Alle drei Part-Typen (PCB, Stencil, Assembly):**
- Revision = `<MAJOR>.<MINOR>` (z.B. `1.2`) — eine InvenTree-Entität pro Release-Tag.
- Vor `Part.create`: Lookup via neuem Helper `find_part_by_name_and_revision(api, name, revision)`.
- Gefunden? → **silently reuse**, keine Modifikation der existierenden Felder.
- Nicht gefunden? → neu anlegen wie bisher (inkl. Image-Upload).

**BOM-Population idempotent (`bom_export.py:populate_bom`):**
- Vor jeder `BomItem.create`: prüfen ob `(sub_part_pk, reference)` für das Assembly schon existiert.
- Existing-Set einmal pro Run laden: `BomItem.list(api, part=assembly.pk)`.
- Skip duplicate, create missing.

### Beispiel-Sequenz

| Release-Run | PCB | Stencil | Assembly | BOM |
|---|---|---|---|---|
| v1.0 first time | create `1.0` | create `1.0` | create `1.0` | populate (full) |
| v1.0 re-run | reuse `1.0` | reuse `1.0` | reuse `1.0` | skip (all exist) |
| v1.1 first time | create `1.1` | create `1.1` | create `1.1` | populate (full) |
| v2.0 first time | create `2.0` | create `2.0` | create `2.0` | populate (full) |

Damit ist die `Auto-Release.yaml`-Workflow beliebig oft re-runnable ohne Duplikate oder Crashes zu produzieren.

### Trade-Off-Notiz

Die README-„gotcha" sagt PCB ist physisch byte-identisch über Minor-Bumps. Diese Spec wählt trotzdem MAJOR.MINOR-Revisioning auf der InvenTree-Seite, weil:
- Klare 1:1-Zuordnung Release-Tag ↔ InvenTree-Revision.
- Alte Releases bleiben mit ihrer historischen BOM erhalten — keine retroaktiven Änderungen.
- Geringe Katalog-Duplizierung (~3 Parts pro Minor-Bump) ist akzeptabel.
- Vermeidet komplexe BOM-Overwrite-Semantik die ein v<MAJOR>-only-Modell bräuchte.

## Section 2: Lookup-Fixes (Bugs #3 + #5)

### Aktuelles Verhalten

**`client.py:find_part_by_name`:**
```python
results = Part.list(api, search=name)  # Substring-Search
for part in results:
    if part.name == name:
        return part
```
Probleme:
- `search=` ist eine Volltext-Substring-Suche im InvenTree-API.
- Bei vielen Treffern + Pagination kann der exakte Match auf Seite 2+ landen und wird übersehen.

**`bom_export.py:match_supplier_parts`:**
```python
all_supplier_parts = SupplierPart.list(api)  # full table scan
sku_to_part = {sp.SKU: ... for sp in all_supplier_parts}
```
- O(N) über alle SupplierParts im Katalog — wächst linear mit Server-Größe.
- Bei einem ausgewachsenen Katalog: mehrere Sekunden pro Release, viel Memory.

### Neues Verhalten

**`find_part_by_name`:**
```python
results = Part.list(api, name=name)  # exact filter, InvenTree-API
return results[0] if results else None
```
Bei mehreren Treffern (verschiedene Kategorien, selber Name) → ersten nehmen, wie bisher.

**`match_supplier_parts`:**
```python
all_skus = {sku for entry in entries for sku in entry.lcsc + entry.mouser}
supplier_parts = SupplierPart.list(api, SKU__in=list(all_skus))
sku_to_part = {sp.SKU: Part(api, pk=sp.part) for sp in supplier_parts}
```
- Eine einzige API-Query, nur relevante SKUs angefragt.
- Skaliert mit BOM-Größe, nicht mit Katalog-Größe.

### Risiko & Fallback

InvenTree-API muss den `SKU__in=[...]`-Filter unterstützen (Django-Standard-Lookup, sollte gehen). Falls die spezifische InvenTree-Version diesen Filter nicht hat:

Fallback in `match_supplier_parts`:
```python
sku_to_part = {}
for sku in all_skus:
    sps = SupplierPart.list(api, SKU=sku)
    if sps:
        sku_to_part[sku] = Part(api, pk=sps[0].part)
```
Per-SKU-Query in einer Schleife — immer noch ein vielfaches besser als full-table-scan (N statt N*M).

Diese Fallback-Strategie wird beim Implementieren via Test-Run gegen den realen Server verifiziert. Wenn `SKU__in` geht: bulk. Wenn nicht: per-SKU.

## Section 3: Multi-SKU-Handling (Bug #4)

### Aktuelles Verhalten

`part_manager.py:ensure_parts_exist`:
```python
lcsc_sku = lcsc_skus[0] if lcsc_skus else ""
mouser_sku = mouser_skus[0] if mouser_skus else ""
```
Wenn ein BOM-Entry mehrere LCSC- oder Mouser-SKUs listet (z.B. `LCSC: C17414,C25804`), wird nur der erste verwendet. Die anderen verschwinden still.

### InvenTree-Modell-Recap

```
Part (das physikalische Bauteil)
├── ManufacturerPart (1+, einer pro (MPN, Manufacturer))
│   └── SupplierPart (1+, einer pro Distributor-SKU)
```

Mehrere Supplier-SKUs zur selben Hersteller-Part sind das Standard-Modell.

### Annahme

**Alle SKUs in einem BOM-Entry zeigen auf denselben MPN+Manufacturer.** Realistischer Fall: derselbe physische Bauteil bei verschiedenen Distributoren, oder verschiedene Reel-Größen mit gleicher MPN.

### Out of Scope (in dieser PR explizit ausgeschlossen)

**Echt unterschiedliche MPNs als BOM-Alternates** (z.B. „Yageo 10k OR KOA 10k") würden mehrere ManufacturerParts unter einer Part brauchen. Realistisch selten in unseren Hardware-Modulen. Wird eigene PR wenn das Bedürfnis aufkommt.

### Neues Verhalten

1. **Supplier-Daten-Fetch:** Erster LCSC-SKU bevorzugt, sonst erster Mouser-SKU. Holt MPN+Manufacturer+Description+Image+Parameters einmal. Unverändert.

2. **`find_existing_part(api, lcsc_skus: list, mouser_skus: list)`:** Iteriert ALLE SKUs, gibt ersten Part-Match zurück.

3. **`create_part_in_inventree`:** Akzeptiert SKU-Listen statt einzelne Strings. Pro Supplier eine Schleife über die SKU-Liste, ein SupplierPart pro SKU, alle unter der einen ManufacturerPart.

4. **`ensure_supplier_parts` (für existing Parts):** Akzeptiert ebenfalls SKU-Listen. Filtert existing-SKUs raus, legt nur fehlende SupplierParts an. Idempotent.

5. **Preise:** Die Preisstaffeln vom ersten gefetchten SKU werden auf alle SupplierParts desselben Suppliers übertragen. Akzeptable Vereinfachung — selbe MPN, ähnliche Preise. Genauer per-SKU-Fetch wäre ein Feature für später (Backlog #6+#7 deckt das tangential ab).

### Beispiel

BOM-Entry: `LCSC: C17414,C25804`, `MOUSER: 603-RC0805FR-0710KL`.

Resultierende InvenTree-Struktur:
```
Part: R 10k 0805
├── ManufacturerPart (Yageo, RC0805FR-0710KL)
│   ├── SupplierPart (LCSC, C17414, mit Preisstaffel)
│   ├── SupplierPart (LCSC, C25804, mit Preisstaffel)
│   └── SupplierPart (Mouser, 603-RC0805FR-0710KL, mit Preisstaffel)
```

## Section 4: Testing & Files

### Testing-Strategie

1. **Bestehende Pytests:** `pytest scripts/tests/` (64 tests) muss grün bleiben. Keine Regressionen.
2. **`py_compile`** über alle modifizierten Files. Self-CI-Workflow `ci.yaml` hat das eingebaut.
3. **Neuer E2E-Smoke-Test** `scripts/e2e_revision_handling.py` — analog zu `e2e_image_upload.py`:
   - Erstellt Throwaway-Parts mit Revisionen, exerziert `find_part_by_name_and_revision`-Helper.
   - Verifiziert silently-reuse-Verhalten für PCB/Stencil/Assembly (zwei Aufrufe → ein Part).
   - Verifiziert BOM-idempotenz (zwei `populate_bom`-Aufrufe → keine Duplikate).
   - Verifiziert Multi-SKU-Anlage (zwei dummy-SKUs → zwei SupplierParts).
   - Cleanup am Ende (deactivate + delete für alle erzeugten Test-Parts).
4. **Manuelle UI-Inspektion** post-merge bei realem Release einer kleinen HW-Modul-Release (z.B. nächste FMTransceiver-Minor-Version). Bestätigt das Verhalten gegen Production-Daten.

### Out of Scope (für PR-2)

- Pytest-mocked Unit-Tests für `inventree_sync` (Backlog #21).
- Edge-Case Multi-MPN-Alternates (eigene PR).
- Bug #1 PR-Erweiterungen / Image-Pipeline-Änderungen.
- Backlog #6-#21 (kommen in eigenen PRs).

### Files

| File | Action | Reason |
|---|---|---|
| `scripts/bom_export.py` | Modify | `create_pcb_part`, `create_stencil_part`, `create_assembly_part` (silently-reuse-Helper); `match_supplier_parts` (batch-SKU-Query); `populate_bom` (idempotent skip-existing-Items). |
| `scripts/inventree_sync/client.py` | Modify | `find_part_by_name` (exact-Filter); `find_existing_part` (SKU-Listen); `ensure_supplier_parts` (SKU-Listen); `create_part_in_inventree` (SKU-Listen); neuer Helper `find_part_by_name_and_revision`. |
| `scripts/inventree_sync/part_manager.py` | Modify | SKU-Listen statt `[0]` an client.py-Funktionen durchreichen. |
| `scripts/e2e_revision_handling.py` | Create | E2E-Smoke-Test analog `e2e_image_upload.py`. |
| `scripts/probe_supplier_images.py` | Unchanged | Bezieht sich nur auf Image-Headers (PR-1 Scope). |
| `scripts/e2e_image_upload.py` | Unchanged | Bezieht sich nur auf Image-Upload (PR-1 Scope). |

**Erwarteter Diff-Umfang:** ~+250/-80 Zeilen Code + ~+150 Zeilen E2E-Test.

## Akzeptanzkriterien

- [ ] `find_part_by_name_and_revision` Helper existiert in `client.py`.
- [ ] PCB/Stencil/Assembly verwenden MAJOR.MINOR-Revision und re-usen existierende Parts beim Re-Run.
- [ ] `populate_bom` ist idempotent: Re-Run produziert keine duplicate BomItems.
- [ ] `find_part_by_name` nutzt `name=`-exact-Filter statt `search=`.
- [ ] `match_supplier_parts` nutzt batch `SKU__in=[...]`-Query (mit per-SKU-Fallback falls API unsupported).
- [ ] Multi-SKU: ALLE SKUs aus `entry.lcsc` und `entry.mouser` werden als separate SupplierParts angelegt.
- [ ] `find_existing_part` iteriert alle SKUs für Lookup.
- [ ] `scripts/e2e_revision_handling.py` existiert und exit-Code 0 gegen real InvenTree.
- [ ] `pytest scripts/tests/` weiterhin 64 grün.
- [ ] `py_compile` clean auf allen Files.

## Verifikations-Plan

Sobald Implementation done und E2E-Test grün, wird gegen den `parts.oe5xrx.org`-Server **manuell mit kleinen realen BOM** verifiziert (z.B. ein Test-Modul oder die Header-Stage des nächsten echten FMTransceiver-Release). Das stellt sicher dass:
- Multi-SKU-Parts aus echten Schaltplänen korrekt landen
- Existing Parts (aus PR-1 E2E-Tests) korrekt von `find_existing_part` und `find_part_by_name` gefunden werden
- Performance des batch-Lookups OK ist
