# Dry-Run Findings — Marathon-Sync-Vorbereitung

**Datum:** 2026-06-05 (nach PR-5 merge, main @ 11ce28c).
**Zweck:** Vor dem realen Marathon-Sync alle 6 HW-Module per `bom_export --dry-run`
durchspielen, Beobachtungen sammeln, danach entscheiden welche Punkte ein
Pre-Sync-PR wert sind.

**Methode:** Release-Artifact (KiBot output) als `production`-zip aus dem
`Create Release Docs`-Workflow runterladen → `bom_export.py` mit allen
Args, `--dry-run`, `--output_dir` setzen → Output sichten.

**Module-Reihenfolge:**
1. PowerBoard v1.1 ✓
2. BusBoard v1.0
3. CM4Carrier v1.0
4. DeviceTester v1.0
5. FMTransceiver v1.0
6. (DebugBoard hat keine Releases)

---

## Modul 1 — PowerBoard v1.1

**Source:** `gh run download 25942436139 --repo OE5XRX/HW-Module-PowerBoard`
(3.75 MB `production` artifact, KiBot v1.1 release run).
**Stand:** ✅ läuft sauber durch, EXIT 0, 20 CREATE + 1 SKIP, 0 FAIL.

### Was gut funktioniert
- Alle 17 BOM-Zeilen klassifiziert, keine Crashes.
- Value-Normalisierung greift: `10u`, `10m`, `49R9`, `2k2`, `13k7` unverändert
  durchgereicht; Polarized-Cap-Slash sauber (`CP 100u/25V`).
- `J101 XT60PW-M` ohne SKU korrekt als SKIP, kein false-FAIL.
- LED, Fuse als generisches Symbol erhalten Single-Part-Aggregation.

### Beobachtungen
1. ~~PCB/Stencil-Revision = `1.1` statt `v1`~~ — **STORNIERT 2026-06-05.**
   User-Klarstellung: `1.1` ist die Vereins-Konvention. Kein Bug.
2. **Inductor ohne Einheit: `L 6.8u`** — KiCad-Value ist `6.8u`, Generator
   hängt nichts an. Lesbarer wäre `L 6.8uH`. Verbesserungs-Kandidat aber
   nicht eilig.
3. **Hässlicher Connector-Name: `Conn_02x10_Row_Letter_First`** — roher
   KiCad-Symbol-Name. Niedrige Priorität, aber unschön im Katalog.
4. **N+1 SKU-Lookup** (bekannt) — `SKU__in=[...]` ignoriert vom Server,
   16 Einzel-GETs Fallback. Nicht kritisch, nur langsam.

### CREATE-Liste (zur Referenz)
```
C 100n 0805        (×5)  LCSC C28233
C 10u 1206         (×3)  LCSC C13585
C 22u 1206         (×2)  LCSC C12891
CP 100u/25V        (×3)  LCSC C132558
LED                (×2)  LCSC C84256
Fuse               (×3)  Mouser 576-01530007Z
Conn_02x10_Row_Letter_First  Mouser 798-PCN10C20S254DS72
XT60PW-M           SKIP (kein SKU)
L 6.8u             Mouser 710-74439346068
R 10m 2512   (×2)  Mouser 652-CRF2512FZR010ELF
R 49R9 0805        Mouser 667-ERA-6AED49R9V
R 1k 0805          LCSC C17513
R 2k2 0805         LCSC C17520
R 13k7 0805        Mouser 652-CR0805FX-1372ELF
R 100k 0805        Mouser 603-RC0805JR-07100KL
INA226       (×2)  LCSC C49851
LMR51430 500kHz    Mouser 595-LMR51430XDDCR
```

---

## Modul 2 — BusBoard v1.0

**Source:** `gh run download 25954445013 --repo OE5XRX/HW-Module-BusBoard`.
**Stand:** ✅ läuft sauber durch, EXIT 0, 11 CREATE + 0 SKIP, 0 FAIL.

### CREATE-Liste
```
C 100nF 0805  (×6)  LCSC C28233        ← Schaltplan-Value = "100nF" (mit Einheit)
C 10uF 0805   (×2)  LCSC C13585        ← "10uF"
Conn_02x10_Row_Letter_First  (×6) Mouser 798-PCN1020P254DSA72
R 2k7 0805         LCSC C352230
FE1.1s             LCSC C6706491      (USB 2.0 Hub Controller)
USBLC6-2SC6  (×5)  LCSC C2827654      (ESD-Diode)
XTAL 12MHz         LCSC C2983432
```

### Neue Beobachtung
5. **Naming-Konsistenz zwischen Schaltplänen:**
   - PowerBoard schreibt `100n`, BusBoard schreibt `100nF` für **denselben**
     physischen Bauteil (LCSC C28233).
   - PowerBoard schreibt `10u`, BusBoard schreibt `10uF` (LCSC C13585).
   - Bei echtem Sync rettet uns **MPN+Mfr-Dedup (#13)**: der zuerst gesynchte
     Modul-Sync legt den Part an (z.B. `C 100n 0805`), der zweite findet ihn
     via MPN+Mfr und attached die SKU/qty. Im Dry-Run sehen wir das nicht,
     weil der Fetcher nicht läuft → falsch-optimistische CREATE-Doppel-Records.
   - **Kein Bug, aber:** Schaltplan-Konvention sollte langfristig vereinheitlicht
     werden (entweder immer ohne Einheit oder immer mit). Das ist eine
     KiCad-side-Aufräumarbeit, nicht hier.

---

## Modul 3 — CM4Carrier v1.0

**Source:** `gh run download 25954445233 --repo OE5XRX/HW-Module-CM4Carrier`.
**Stand:** ✅ läuft sauber durch, EXIT 0, 18 CREATE + 1 SKIP, 0 FAIL.

### CREATE-Liste
```
C 100n 0805    (×3)  LCSC C28233
CP 100u/25V    (×2)  LCSC C132558
LED            (×2)  LCSC C84256
Conn_01x03_Pin       SKIP (J102, kein SKU)
Conn_02x10_Row_Letter_First   Mouser 798-PCN10C20S254DS72
DF40C-100DS-0.4V_51_  (×2)  LCSC C597931   ← Hirose-Connector, trailing _51_
HR911130A           LCSC C54408     (RJ45 mit Magnetics)
TF-01A              LCSC C91145     (MicroSD-Slot, Not in stock!)
R 470 0805     (×2)  LCSC C17710    ← bare Ohm (kein Ω-Suffix)
R 1k 0805      (×2)  LCSC C17513
R 2k2 0805     (×4)  LCSC C17520
R 12k 0805          LCSC C17444
RT9742GGJ5          LCSC C250547   (RT9742AGJ5F LDO, Symbol-Name)
SN74LV1T34DBV       LCSC C100024   (Level-Shifter)
TPD4EUSB30     (×2)  LCSC C90627    (USB-3.0 ESD)
```

### Neue Beobachtungen
6. **Extra CSV-Spalten** (MF, Description_1, Package, SnapEDA_Link, …) werden
   von `csv.DictReader` problemlos ignoriert — `load_bom` zieht nur die
   gebrauchten Header. ✓
7. **Bare-Ohm-Resistor:** `R 470 0805` (KiCad-Value `470` ohne Suffix). Lesbar,
   kein Bug. Konsistent mit `49R9` (R-Notation für Komma-Werte).
8. **MicroSD `TF-01A` "Not in stock"** im SnapEDA-Status — irrelevant für Sync,
   nur Notiz für Beschaffung.

---

## Modul 4 — DeviceTester v1.0

**Source:** `gh run download 25954445974 --repo OE5XRX/HW-Module-DeviceTester`.
**Stand:** ✅ EXIT 0, aber **echter Sync-Bug entdeckt** (siehe #9).

### CREATE-Liste
```
CP 100u/25V    (×3)  LCSC C132558
LED            (×3)  LCSC C84256
Conn_01x02_Pin       SKIP (J303, kein SKU)
Conn_01x04           LCSC C2691448   (J202)
Conn_02x10_Row_Letter_First   Mouser 798-PCN1020P254DS72  (J203, Stiftleiste straight)
Conn_02x10_Row_Letter_First   Mouser 798-PCN10C20S254DS72 (J302, Buchsenleiste)   ⚠ SAME NAME
Screw_Terminal_01x02 LCSC C5349557   (J101)
USB_C_Receptacle_USB2.0_16P  (×2)  LCSC C2988369
R 1k 0805      (×2)  LCSC C17513
R 2k2 0805           LCSC C17520
R 5k1 0805     (×4)  LCSC C27834
```

### Neue Beobachtung — echter Bug
9. **🐛 Generic-Connector-Name-Kollision (NEU im Backlog!):**
   - J203 (Mouser `798-PCN1020P254DS72`, Stiftleiste straight) und J302
     (Mouser `798-PCN10C20S254DS72`, Buchsenleiste) sind **physisch
     verschiedene Bauteile** mit unterschiedlichen Mouser-SKUs.
   - Beide haben aber denselben KiCad-Symbol-Namen
     `Conn_02x10_Row_Letter_First` und der Footprint geht für Nicht-
     RCL-Parts **nicht** in den Namen ein → beide bekommen den generierten
     Namen `Conn_02x10_Row_Letter_First`.
   - **Was beim echten Sync passiert:** Erster CREATE legt Part an, zweiter
     fällt in `find_part_by_name` rein, findet den ersten Part, hängt
     seine Mouser-SKU per `ensure_supplier_parts` daran an → ein einziger
     InvenTree-Part hat zwei Mouser-SKUs für zwei physisch verschiedene
     Bauteile. Die MPN+Mfr-Dedup (#13) rettet uns nur wenn die Mouser-API
     verschiedene MPNs liefert; bei JAE-Connectoren ist das wahrscheinlich
     der Fall (`PCN10-20P-2.54DS` vs `PCN10C-20S-2.54DS`), dann landet's
     im 4. Lookup-Schritt (Name) → trotzdem Kollision.
   - **Fix-Optionen:**
     - **A)** Für `Conn_*`-Symbole Footprint in den Namen: z.B.
       `Conn_02x10_Row_Letter_First PCN10-20P-2.54DS`. Lang aber unique.
     - **B)** Generic-Connector-Symbol-Namen werden mit Footprint-Suffix
       gesalzen wie bei R/C/L: `Conn_02x10_Row_Letter_First PCN10`.
     - **C)** find_part_by_name zusätzlich auf Footprint-Match prüfen
       (entweder via Part-Attribut oder via einem der angehängten
       Parameter).
   - **Empfehlung:** Option A — minimal-invasiv, gleicher Stil wie
     `R 10k 0805` (mit Package-Suffix). Code-Änderung in
     `generate_part_name`: für `Conn_*`/`Screw_Terminal_*` Symbole den
     Footprint anhängen.

---

## Modul 5 — FMTransceiver v1.0

**Source:** `gh run download 25954445690 --repo OE5XRX/HW-Module-FMTransceiver`.
**Stand:** ✅ EXIT 0, 39 CREATE + 1 SKIP. Größtes Modul (36 BOM-Zeilen).

### Highlights aus der CREATE-Liste
```
C 30pF 0805            Mouser 791-0805N300J500CT
C 10nF/NP0 0805        Mouser 81-GRM2165C2A103JA1D
C 100nF 0805     (×12) LCSC C28233           ← "100nF" diesmal
C 1uF 0805       (×2)  Mouser 791-0805B105K250CT
C 10uF 0805            Mouser 963-EMK212BJ106KG-T  ← "10uF" mit Einheit
C 10u 1206       (×2)  LCSC C13585           ← "10u" ohne (anders!)
C 22u 1206       (×2)  LCSC C12891
CP 100u/25V      (×2)  LCSC C132558
C 4.7u/10V/X5R 0805    Mouser 187-CL21A475KAQNNNF  ← compound /X5R
C 5.6nF/X7R/100V 0805  Mouser 791-0805B562K201CT   ← compound /X7R/100V
BAT43W-V               Mouser 78-BAT43W-G3-08
LFCN-180/490           Mouser 139-LFCN-180,139-LFCN-490   ← ⚠ 2 SKUs in field
Conn_02x10_Row_Letter_First   Mouser 798-PCN10C20S254DS72  (Buchse) ⚠ Name-collision-Kandidat
Conn_ARM_JTAG_SWD_10   SKIP (kein SKU)
Conn_Coaxial           Mouser 530-142-0701-801 (Amphenol SMA EdgeMount)  ⚠ Name-collision-Kandidat
L 6.8u                 Mouser 710-74439346068 (WE-XHMI 6060)  ← cross-board mit PowerBoard!
2N7002                 Mouser 637-2N7002
BC847BS                Mouser 241-BC847BS_R1_00001 (Dual NPN)
R 220 0805             Mouser 667-ERA-6AEB221V  ← bare Ohm 220R
R 3k9 0805             Mouser 603-RT0805DRD073K9L
R 4k7 0805             Mouser 603-RC0805FR-074K7L
R 47k 0805             Mouser 603-AC0805FR-1047KL
R 56k 0805             Mouser 603-RT0805DRE0756KL
CAT24C32               Mouser 698-CAT24C32WI-GT3   ← Schaltplan-Inkonsistenz: kicad_value=CAT24C32, Symbol/Description=CAT24C128
LMR51430 500kHz        Mouser 595-LMR51430XDDCR    ← cross-board mit PowerBoard
SA818V                 LCSC C3001504  (VHF transceiver)
STM32U575CITx          Mouser 511-STM32U575CIT6
TLV73333PQDBVRQ1       LCSC C2862550, Mouser 595-TLV73333PQDBVRQ1  ← Generator nimmt val, beide SKUs
USBLC6-2SC6            LCSC C2827654  ← cross-board mit BusBoard, CM4Carrier
XTAL 8MHz/20pF         Mouser 73-XT49M800-20  ← slash compound
```

### Neue Beobachtungen
10. **Multi-SKU im selben CSV-Feld** (`139-LFCN-180,139-LFCN-490`) wird von
    `_split_sku_field` korrekt zerlegt → zwei Mouser-SupplierParts würden
    angelegt. ✓
11. **Cross-modul-Wiederverwendung sichtbar:** `L 6.8u` (Wurth WE-XHMI 6060),
    `LMR51430 500kHz`, `USBLC6-2SC6`, `Conn_02x10_Row_Letter_First` (Buchse)
    tauchen in mehreren Boards mit identischer Mouser/LCSC-SKU auf.
    MPN+Mfr-Dedup wird bei Real-Sync das richtig auflösen.
12. **Schaltplan-Inkonsistenz `CAT24C128 vs CAT24C32`** (U202): Description und
    Symbol-Name sagen CAT24C128, kicad_value sagt CAT24C32, Mouser-SKU ist für
    CAT24C32. Generator nimmt `kicad_value` → CAT24C32 → matched die SKU.
    Sync ist robust gegen sowas, der Bauteil-Identitäts-Disambiguator ist die
    SKU. **Hinweis an User:** Schaltplan-Cleanup empfehlenswert, kein
    Sync-Bug.
13. **TLV73333PQDBVRQ1** — automotive Q-Variante: kicad_value, beide Suppliers
    haben die richtige SKU. Generator → korrekter MPN.
14. **`Conn_Coaxial`** — generisches Symbol, hat zwar eindeutige Mouser-SKU
    (Amphenol SMA), aber falls ein zukünftiges Board einen anderen Koax-
    Connector mit selbem Symbol verwendet (z.B. BNC), trifft uns derselbe
    Bug wie #9. Same fix needed.

---

## Modul 6 — PowerBoard v1.0 (zusätzlich)

**Source:** `gh run download 25942310961 --repo OE5XRX/HW-Module-PowerBoard`.
**Stand:** ✅ EXIT 0, Summary identisch zu v1.1: 20 CREATE + 1 SKIP.

BOM byte-identisch zu v1.1 — Schaltplan-/Bauteilliste hat sich zwischen
v1.0 und v1.1 nicht verändert (vermutlich nur PCB-Layout-Updates oder
Doku-Bugfixes). Beim Real-Sync wird PR-5 #13 (MPN+Mfr-Dedup) das alles
korrekt zu denselben Parts wiederverwenden.

---

## Module 7-11 — alle v0.2-Releases (historisch)

**Strategy:** Workflow-Artifacts für v0.2 waren nach 90 Tagen retentiert.
GitHub's `gh run rerun`-Limit ist 1 Monat. Lösung: `gh workflow run "Create
Release Docs" --ref v0.2` — der Trigger erlaubt `workflow_dispatch`, also
neue Workflow-Runs auf v0.2-Tag.

**Stencil-Image:** v0.2-Releases hatten **keine** Stencil-Generierung in
der CI. `bom_export.py` wurde ohne `--stencil_image` gerufen — der
Stencil-Part wird trotzdem als "<Module> SMT Stencil rev 0.2" angelegt
(image=None ist legal). Bei Real-Sync entstehen damit Fake-Stencil-Parts
ohne tatsächlichen Stencil. **Diskussionspunkt:** möchten wir die für
v0.2 wirklich anlegen? Workaround: `--name`-Filter im Marathon-Sync-
Skript, oder manuelles Aufräumen nach Sync.

### v0.2-Stand pro Modul

| Modul | BOM | CREATE | SKIP | Δ zu v1.x |
|-------|-----|--------|------|-----------|
| **PowerBoard v0.2** | 17 | 20 | 1 | identisch zu v1.0/1.1 |
| **BusBoard v0.2** | 7 | 11 | 0 | identisch zu v1.0 |
| **CM4Carrier v0.2** | 14 | 18 | 1 | identisch zu v1.0 |
| **DeviceTester v0.2** | 10 | 14 | 1 | identisch zu v1.0 |
| **FMTransceiver v0.2** | 33 | 36 | 1 | **deutlich anders** (siehe unten) |

### FMTransceiver v0.2 vs v1.0 — Hardware-Migration

v0.2 → v1.0 ist eine **Plattform-Migration** STM32F302 (Cortex-M4) →
STM32U575 (Cortex-M33). Konkret im BOM sichtbar:

```
v0.2 only:                                  v1.0 only:
  U201 STM32F302CBTx                          U201 STM32U575CITx
  Q601, Q701 zusätzliche 2N7002 (×3)          Q701 BC847BS (Dual NPN)
  C501, C601, C701 mehr 10nF Caps             C216 extra 4.7u/X5R Cap
  R601, R701 mehr 10k Pull-ups                R705/R706 mehr 100k Pull-ups
                                              R703, R704 47k Pulls
                                              R301-R304: feinere Pull-Netze
```

Beim Real-Sync via MPN+Mfr-Dedup landen die als **zwei separate STM32-Parts**
(unterschiedliche MPNs) — korrekt, weil es echt verschiedene MCUs sind.
Die gemeinsamen R/C/L-Bauteile teilen sich Parts cross-version.

### Wieder: keine neuen Bug-Befunde

- 0 Crashes
- 0 FAILs
- alle EXIT 0
- DeviceTester v0.2 hat denselben Conn_02x10_Row_Letter_First Stift/Buchse-Konflikt
  wie v1.0 — wird durch PR-6 (MPN-basierter Name) korrekt aufgelöst beim Real-Sync.

---

## Querschnittliche Findings

**Summary über 11 Dry-Runs / 192 BOM-Zeilen:**
- 0 Crashes, 0 echte FAILs, alle EXIT 0.
- 1 echter Sync-Bug entdeckt (DeviceTester #9).
- Mehrere Naming-Konsistenz-Beobachtungen.
- Mehrere Cross-Modul-Wiederverwendungen sichtbar → MPN+Mfr-Dedup wird's
  beim Real-Sync auflösen.

### Pre-Sync-PR-Kandidaten (Priorität)

**🔴 Hoch (sollte vor Marathon-Sync rein):**

1. **Generic-Connector/Coaxial Name-Kollision (Beobachtung #9)**
   - **Wo:** `categories.generate_part_name` else-Branch.
   - **Fix:** Für `Conn_*`/`Screw_Terminal_*`-Symbole Footprint anhängen
     (analog R/C mit Package-Suffix). Konkret: erweitere die
     RCL-Whitelist um eine Connector-Whitelist und produziere
     `f"{val} {extract_package(footprint)}"`.
   - **Aufwand:** ~10 LOC + 4-5 pytest cases + Re-Verify gegen die 5
     Module. Mini-PR.
   - **Konsequenz wenn nicht gefixt:** ein Real-Sync legt einen einzigen
     `Conn_02x10_Row_Letter_First`-Part an, die zweite SKU wird via
     `ensure_supplier_parts` dran-gehängt → Katalog hat ein "Hydra"-Part
     mit zwei physisch verschiedenen SKUs. Bei InvenTree-Browse vom
     Vereinsmitglied verwirrend; Reverse-Reparatur braucht manuelle
     Aufräumung.

**🟡 Mittel (nice-to-have, nicht blockierend):**

2. ~~**PCB/Stencil Major-Revision**~~ — **STORNIERT 2026-06-05.** User hat
   bestätigt: `1.1` als PCB-Revision ist die Vereins-Konvention, nicht
   Backlog-#2-Halbimplementierung. Beobachtung #1 oben ist damit hinfällig.

3. **Inductor-Einheit anhängen (Beobachtung #2):**
   - Heute: `L 6.8u` (KiCad-Value `6.8u`).
   - Fix: Wenn `kicad_part in {L, L_Iron}` und val endet auf SI-Prefix
     ohne H, hänge `H` an → `L 6.8uH`. Analog C (F) und Crystal bleibt
     wie es ist (val hat oft schon Einheit).
   - **Konsequenz wenn nicht gefixt:** Katalog liest sich uneinheitlich;
     keine funktionalen Konflikte.

**🟢 Niedrig (KiCad-side, kein Code-Fix):**

4. **Schaltplan-Konsistenz** für Cap-Werte: `100n` vs `100nF`, `10u` vs `10uF`
   in verschiedenen Modulen (Beobachtung #5 + FMT-internal Mismatches
   in #12). Sync ist via MPN+Mfr-Dedup robust, aber Vereins-Konvention
   "immer mit Einheit" oder "immer ohne" wäre langfristig sauber.

5. **CAT24C128 vs CAT24C32 Inkonsistenz** im FMTransceiver-Schaltplan
   (Beobachtung #12) — KiCad-side cleanup.

### Bekannte, akzeptierte Limitierungen
- **N+1 SKU-Lookup** (Server-Limit, dokumentierter Fallback): bei
  Marathon-Sync über 5 Module mit ~92 BOM-Zeilen ergibt das ~92 GETs
  (Batch failed, per-SKU fallback). Dauer mehrere Sekunden, akzeptabel.
- **Generic-Symbol-Names** (`LED`, `Fuse`) erhalten denselben Part — funktionell
  korrekt, da LCSC-SKU eindeutig den Bauteil identifiziert. Wenn ein
  Schaltplan zwei verschiedene LEDs (z.B. rot + grün) mit demselben Symbol
  `LED` und verschiedenen SKUs hat, würden die zu zwei Parts; bei selber SKU
  zu einem Part — beides korrekt.
- **`Conn_02x10_Row_Letter_First`-Buchse** taucht in CM4Carrier J101,
  DeviceTester J302 und FMTransceiver J202 mit derselben Mouser-SKU
  `798-PCN10C20S254DS72` auf. Bei Real-Sync via SKU-Match (find_existing_part)
  → einzelner Part wird angelegt und cross-board genutzt. ✓
- **Dry-Run optimistisches CREATE**: weil Fetcher off, sieht man im Dry-Run
  alle Parts als CREATE. Bei Real-Sync werden viele zu REUSE via
  MPN+Mfr-Dedup (PR-5 #13). Erwartetes Verhalten, kein Bug.

---

## Empfehlung für nächsten Schritt

**Vor dem Marathon-Sync:** Mini-PR mit Fix #1 (Connector-Name-Disambiguator).
Das verhindert den einen echten Bug, alles andere ist nice-to-have.

**Optional gemeinsam in derselben PR** (wenn schon dabei): Fix #2
(PCB/Stencil Major-Revision) — winziger Code-Change, exakt der gleiche Spot
in `bom_export.py`, und behebt die nächste vorhersehbare Duplikat-Quelle.

**Reihenfolge dann:**
1. Mini-PR mit #1 (+ optional #2).
2. Marathon-Sync **gegen den Test-Server** zuerst (alle 5 Module).
3. Bestandsaufnahme der angelegten Parts, ggf. manuelle Cleanups.
4. Marathon-Sync gegen Produktion (falls separater Server existiert, sonst
   ist das schon der Produktions-Sync — `parts.oe5xrx.org` ist offenbar die
   echte Instanz).

---

## CI-Integrations-Audit (PR-7)

**Timeline:**
- `c853bac` 2026-05: bom_export-Stage **disabled** im `create-release-docs.yaml`-
  Workflow, weil der InvenTree-Server dekommissioniert wurde.
- `60eed50` 2026-06-04 17:49: **Re-Enabled** + `--output_dir` für Attachment-
  Auto-Discovery. Seitdem läuft die Stage wieder, ist aber bisher nur in
  unseren Test-Re-Runs (mit `continue-on-error: true`) angeschlagen.
- PR-3, PR-4, PR-5, PR-6 fügten Args dazu (alle optional/backwards-compat),
  CI-Aufruf wurde nie aktiv re-validiert.

**Befunde im CI-Aufruf (`.github/workflows/create-release-docs.yaml:153-160`):**

1. `--name "${{ github.event.repository.name }}"` → liefert
   `HW-Module-PowerBoard` (Repo-Name) statt `PowerBoard` (KiCad-Project-Name).
   Damit würden InvenTree-Parts wie `HW-Module-PowerBoard PCB` entstehen, was
   nicht zu den lokalen Marathon-Sync- und E2E-Test-Konventionen passt.
2. `--version "${{ github.ref_name }}"` → liefert `v1.1` (Tag mit v-prefix)
   statt `1.1`. Inkonsistent zur Title-Block-Convention im selben Workflow
   (der `Inject release version`-Step strippt v schon).
3. Beim Auditieren noch dazu: **Command-Injection-Hotspots** — drei
   `${{ ... }}`-Inline-Templating-Stellen in `run:`-Blöcken (1× bom_export,
   2× stencil-PNG-Step). Defensive Korrektur: env-var-Mapping (`env: PROJECT,
   REF_NAME`), dann `"${PROJECT}"` / `"${REF_NAME#v}"`.

**Fix in PR-7:** alle drei adressiert.

**Followups (nicht in PR-7):**
- `create-debug-docs.yaml` hatte dasselbe Inline-Templating im stencil-PNG-
  Step (2 Stellen) — **mit-gefixt**, weil identisches Pattern.
- v0.2-Tags rufen den shared workflow nicht auf (legacy inline create-release-
  docs.yaml). Ein v0.2-Release-Re-Trigger würde **nicht** bom_export laufen
  lassen — wer v0.2 syncen will, muss `bom_export.py` lokal mit den
  Downloaded-Artifacts aufrufen.

**Was bedeutet das für den Marathon-Sync?**
- **CI-Variante:** ein einzelner `gh workflow run "Create Release Docs"
  --ref v1.0` auf einem v1.x-Tag würde mit PR-7 korrekt syncen.
- **Manuelle Variante:** wir tun's ohnehin per Hand — die haben wir in den
  Dry-Runs bewährt.
