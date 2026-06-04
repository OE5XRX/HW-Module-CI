# PR-3: Rich Part Data — Spec

**Branch:** `feat/rich-part-data`
**Backlog-Refs:** [`2026-06-03-inventree-sync-improvements-backlog.md`](./2026-06-03-inventree-sync-improvements-backlog.md) Punkte #6, #7, #8, #12
**Status:** Spec — bereit für Plan

## Scope

Vier zusammenhängende Features die den InvenTree-Eintrag pro Part von einem nackten Skelett auf production-grade-Daten anreichern:

- **#6 — Parameter-Sync:** LCSC + Mouser Parameter (Resistance, Tolerance, Voltage, …) werden als `PartParameter` in InvenTree gespeichert → Suchbarkeit ("alle 100nF/0805/X7R/50V").
- **#7 — Supplier-Links:** Jeder `SupplierPart` bekommt das `link`-Feld populated → Click-through zur LCSC/Mouser-Produktseite direkt aus InvenTree.
- **#8 — KiBot-Outputs als Attachments:** Schematic-PDF, BOM-HTML/CSV, STEP, 3D-Renderings, Stencil-Files als InvenTree-Part-Attachments.
- **#12 — Stencil-Geometrie:** Subset von #8 — Stencil-SVG + JLCPCB-Stencil-ZIP am Stencil-Part.

Plus: Reaktivierung des bisher seit InvenTree-Server-Dekommissionierung (05/2026) auskommentierten `bom_export.py`-Steps im `create-release-docs.yaml`-Workflow.

## Section 1: Parameter-Sync (#6)

### Datenfluss

- LCSC-Fetcher füllt heute schon `PartData.parameters: dict[str, str]` via `paramVOList` — unverändert.
- Mouser-Fetcher wird erweitert: neuer interner `_parse_attributes`-Helper extrahiert Attribute aus dem `ProductAttributes`-Array der Mouser-API-v2-Response und mapped sie in dasselbe `parameters`-Dict.
- Merge in `_fetch_and_merge`: LCSC-Params haben Vorrang; Mouser füllt nur Keys die LCSC nicht hatte (Pattern wie bei den anderen Feldern schon etabliert).

### Upload-Helper

Neuer Helper in `inventree_sync/client.py`:

```python
def upload_parameters(api: InvenTreeAPI, part: Part, params: dict[str, str]) -> None:
    """Sync parameters dict to InvenTree Part.

    Overwrites existing values for keys present in *params* (supplier is
    source of truth — manual UI edits to these keys will be lost on the
    next sync). Keys NOT present in *params* are left untouched on the
    Part: this is a delta-sync, not a full replacement.

    For each (name, value):
      - Find-or-create PartParameterTemplate by exact name
      - Find existing PartParameter (part, template) — update or create
    """
```

### Aufruf-Punkte

- `create_part_in_inventree`: nach `Part.create` + ManufacturerPart + SupplierParts wird `upload_parameters(api, part, part_data.parameters)` aufgerufen.
- `ensure_supplier_parts`: zusätzlich am Ende `upload_parameters(api, part, part_data.parameters)` → existing Parts werden auf jeden Re-Sync auch refreshed.

### Re-Sync-Verhalten

Bewusst: **User-manuelle Edits an Parameter-Werten werden auf nächstem Sync überschrieben** wenn der Key vom Supplier zurückkommt. Trade-off explizit akzeptiert weil:
- Supplier-Daten sind das Quellsystem; manuelle Edits sind Korrekturen die typischerweise im Schaltplan / KiCad-Symbol gefixt werden sollen, nicht in InvenTree.
- Delta-Sync (nicht-erwähnte Keys bleiben unberührt) schützt Custom-Parameter die User selbst anlegen.

### Wert-Normalisierung

**Keine.** LCSC liefert `"10kΩ"`, Mouser liefert `"10 kOhms"` — wir nehmen die Werte 1:1 wie sie vom Supplier kommen. Normalisierung wäre ein eigenes Feature (Backlog #19) und kommt wenn Bedarf entsteht.

## Section 2: Supplier-Links (#7)

### URL-Konstruktion (pattern-basiert, nicht API)

- **LCSC:** `https://www.lcsc.com/product-detail/{lcsc_sku}.html`
- **Mouser:** `https://www.mouser.com/ProductDetail/{mouser_sku}`

Pattern-basiert ist robuster gegen Supplier-API-Schema-Änderungen.

### Helper

```python
def _supplier_url(supplier_name: str, sku: str) -> str:
    name = supplier_name.lower()
    if "lcsc" in name:
        return f"https://www.lcsc.com/product-detail/{sku}.html"
    if "mouser" in name:
        return f"https://www.mouser.com/ProductDetail/{sku}"
    return ""  # unknown supplier — leave blank
```

### Aufruf-Punkte

In `client.py:create_part_in_inventree` + `ensure_supplier_parts`: bei jedem `SupplierPart.create`-Payload ein `"link": _supplier_url(supplier.name, sku)` mitgeben.

### Multi-SKU

Ein konstruierter Link pro SKU — jeder SupplierPart bekommt seine eigene URL zur eigenen Produktseite, kein geteilter Link.

### Re-Sync

SupplierPart.link wird beim ersten Anlegen gesetzt. Wenn ein SupplierPart schon existiert (Re-Run trifft ihn), bleibt der Link unverändert. Trade-off OK weil URL-Pattern stabil.

### `PartData.supplier_link`-Feld

Bleibt in der Dataclass aber unbenutzt (kein Refactor in dieser PR). Kann in einer späteren Cleanup-PR raus.

## Section 3: KiBot-Outputs als Attachments (#8 + #12)

### Neuer Modul

`scripts/inventree_sync/attachments.py` — eigenes File weil die Logik isoliert ist (kein Bezug zu Supplier/Part-Creation, nur Filesystem + Part.uploadAttachment).

Public-API:
```python
def attach_kibot_outputs(
    api: InvenTreeAPI,
    pcb: Part,
    assembly: Part,
    stencil: Part,
    output_dir: str | Path,
) -> None:
```

### Auto-Discovery-Mapping

Glob-Patterns relativ zu `output_dir`:

| Pattern | Target-Part | Comment |
|---|---|---|
| `*.step` | PCB | `"3D STEP model"` |
| `*-3D_top.png` | PCB | `"3D render (top, no components)"` |
| `*-3D_bottom.png` | PCB | `"3D render (bottom)"` |
| `*-stencil_top.svg` | Stencil | `"Stencil paste layer (SVG)"` |
| `Fabrication/*.zip` | Stencil | `"JLCPCB stencil spec"` |
| `*-schematic.pdf` | Assembly | `"Schematic"` |
| `*-bom.html` | Assembly | `"BOM (static HTML)"` |
| `*-bom.csv` | Assembly | `"BOM (CSV)"` |
| `*-ibom.html` | Assembly | `"Interactive BOM"` |

### Skipped (Anti-Doppel-Attach)

- `*-3D_top-with.png` — schon als `Assembly.image` gesetzt (via `--assembly_image`)
- `*-3D_top-without.png` — schon als `PCB.image` (via `--pcb_image`)
- `*-stencil_top.png` — schon als `Stencil.image` (via `--stencil_image`)

### Idempotency (Re-Run-safe)

```python
existing_filenames = {a.filename for a in part.getAttachments()}
for path in matched_files:
    basename = Path(path).name
    if basename in existing_filenames:
        log.info("Attachment %r already on pk=%s, skipping", basename, part.pk)
        continue
    part.uploadAttachment(path, comment=comment)
    log.info("Uploaded attachment %r to pk=%s", basename, part.pk)
```

### Diagnostics

Am Ende jedes `attach_kibot_outputs`-Aufrufs:

```
INFO: Attachments: N uploaded, M skipped (already present), K patterns had no match.
```

Wenn `K > 0` → KiBot-Output-Convention hat sich geändert und Patterns müssen ggf. angepasst werden.

### CLI-Integration

`bom_export.py` bekommt neuen optionalen Arg:
```
--output_dir <path>   KiBot output directory for attachment discovery.
                      When given, attaches all matched files to the
                      respective Parts after BOM population. When omitted,
                      no attachments are created.
```

Aufruf am Ende von `bom_export.py:main`, nach `populate_bom`:

```python
if args.output_dir:
    attach_kibot_outputs(api, pcb, assembly, stencil, args.output_dir)
```

### Workflow-Integration

`.github/workflows/create-release-docs.yaml`:
- Reaktiviere den bisher auskommentierten `bom_export.py`-Step.
- Aktualisiere die `INVENTREE_API_HOST` / `INVENTREE_API_TOKEN` Secret-Bezüge — diese müssen vor dem Merge im Org-Settings rotiert/eingestellt werden.
- Füge `--output_dir output/` zu den Args hinzu.

### JLCPCB-Stencil-ZIP — Empirische Verifikation

Der `import: JLCPCB_stencil` Plugin im `production.kibot.yaml` legt Files in `Fabrication/` ab (`resources_dir: Fabrication` global). Das genaue ZIP-Filename-Pattern ist nicht offline bekannt. Plan:
- Initialer Pattern: `Fabrication/*.zip`
- Beim ersten echten CI-Run gegen den InvenTree-Server verifizieren ob ein ZIP-File gematched wird.
- Falls nicht: Pattern justieren (z.B. `Fabrication/JLCPCB/*.zip` oder spezifischer Filename).

## Section 4: Files & Tests

### Files

| File | Action | Reason |
|---|---|---|
| `scripts/inventree_sync/client.py` | Modify | Neuer `upload_parameters` Helper; neuer `_supplier_url` Helper; SupplierPart-Create-Payloads bekommen `link`-Feld; Aufruf von `upload_parameters` in `create_part_in_inventree` + `ensure_supplier_parts` |
| `scripts/inventree_sync/fetchers.py` | Modify | Mouser-Fetcher um `_parse_attributes` erweitern — befüllt `parameters` aus `ProductAttributes` |
| `scripts/inventree_sync/attachments.py` | Create | Neuer Modul: `attach_kibot_outputs(api, pcb, assembly, stencil, output_dir)` |
| `scripts/bom_export.py` | Modify | Neuer `--output_dir` CLI-Arg; nach `populate_bom` Aufruf von `attach_kibot_outputs` |
| `scripts/e2e_revision_handling.py` | Modify | 3 neue Tests: `test_parameter_sync_delta`, `test_supplier_link_populated`, `test_attachment_idempotent` |
| `.github/workflows/create-release-docs.yaml` | Modify | Reaktiviere bom_export-Step + neuer `--output_dir output/` Arg |

### Testing-Strategie

1. **Pytest-Suite** (`scripts/tests/`): 64 grün — keine Regression.
2. **`py_compile`** über alle modifizierten + neue Files.
3. **E2E** (`scripts/e2e_revision_handling.py`) erweitert um 3 Tests:
   - **`test_parameter_sync_delta`**: Part erstellen, `upload_parameters({"A":"1","B":"2"})` → 2 PartParameters. Nochmal `upload_parameters({"A":"99","C":"3"})` → A=99 (overwrite), B=2 (unverändert weil nicht im Dict), C=3 (neu). Total 3 PartParameters.
   - **`test_supplier_link_populated`**: Part anlegen mit LCSC + Mouser SupplierParts via `create_part_in_inventree` → beide SupplierParts haben `link` populated mit konstruierten URLs.
   - **`test_attachment_idempotent`**: tempfile mit known content erstellen, `attach_kibot_outputs` mit synthetischer Dir-Struktur, prüfen dass Attachment präsent. Zweiter Aufruf → keine Duplikate.

4. **Manueller post-merge E2E** bei realem Release (z.B. nächste FMTransceiver-Minor):
   - InvenTree-UI öffnen, Assembly-Part inspizieren — Parameter Tab, Supplier-Links anklickbar, Attachment-Liste vollständig.

### Out of Scope (für PR-3, klar dokumentiert)

- Cost-Report (#11) — eigene PR
- Refresh-Mode (#9) — eigene PR
- Dry-Run (#10) — eigene PR
- Wert-Normalisierung der Parameter (Teil von #19) — eigene PR
- Update existierender SupplierPart-Links auf Re-Sync
- Entfernung von `PartData.supplier_link` als unused Field — Cleanup

### Erwarteter Diff-Umfang

- Code: ~+250/-30 Zeilen
- E2E-Test: ~+100 Zeilen
- YAML: ~+10 Zeilen

## Akzeptanzkriterien

- [ ] `upload_parameters` Helper in `client.py` existiert mit delta-sync-Semantik
- [ ] Mouser-Fetcher parsed `ProductAttributes` → `parameters` dict
- [ ] LCSC + Mouser Parameter werden bei `create_part_in_inventree` und `ensure_supplier_parts` auf InvenTree synced
- [ ] `_supplier_url` Helper konstruiert LCSC + Mouser URLs aus SKU
- [ ] Alle SupplierPart-Create-Payloads enthalten populiertes `link`-Feld
- [ ] `scripts/inventree_sync/attachments.py` mit `attach_kibot_outputs(api, pcb, assembly, stencil, output_dir)` existiert
- [ ] Auto-Discovery Mapping wie spezifiziert
- [ ] Idempotency via existing-filenames Check
- [ ] `bom_export.py` hat neuen `--output_dir` CLI-Arg
- [ ] `create-release-docs.yaml` reaktiviert mit `--output_dir output/`
- [ ] 3 neue E2E-Tests grün
- [ ] pytest-Suite 64 grün
- [ ] py_compile clean
