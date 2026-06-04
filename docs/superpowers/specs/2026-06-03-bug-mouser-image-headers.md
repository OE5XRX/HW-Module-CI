# Bug-Fix: Mouser-Image-Download via PerimeterX-Block

**Branch:** `fix/mouser-image-headers`
**Backlog-Ref:** [`2026-06-03-inventree-sync-improvements-backlog.md` Punkt 1](./2026-06-03-inventree-sync-improvements-backlog.md)
**Status:** Spec — bereit für Plan

## Problem

`scripts/inventree_sync/client.py:upload_image_from_url` lädt für jeden
InvenTree-Part das Produktbild vom Supplier-CDN. Bei Mouser-Image-URLs
(z.B. `https://www.mouser.com/images/<manufacturer>/lrg/<sku>_SPL.jpg`)
gibt der Server systematisch eine 4592-Byte „Access denied"-HTML-Seite
mit HTTP 200 zurück — Mouser betreibt PerimeterX-Bot-Protection vor
seinem Image-CDN.

Der Code erkennt das nicht:
- Es wird `resp.raise_for_status()` aufgerufen → 200 OK, keine Exception.
- Die HTML-Antwort wird in eine temporäre Datei mit `.jpg`-Endung
  geschrieben.
- `part.uploadImage(tmp_path)` wird mit dieser Datei aufgerufen.
- Je nach InvenTree-Server-Verhalten: stiller Reject oder ein Part mit
  „Bild" das in Wirklichkeit HTML-Müll ist.

Symptom für den User: Mouser-Parts in InvenTree haben kein Bild.

## Verifikation

Lokal am 2026-06-03 gegen real Mouser+LCSC reproduziert und gefixt.
Probes mit `curl` und `wget` gegen
`https://www.mouser.at/images/espressifsystems/images/ESP32-D0WDRH2-V3_SPL.jpg`:

- Aktuelle Code-Header (iOS-UA + `Referer: lcsc.com`) → 4592 B HTML.
- Browser-shaped UA + Accept-Language + Referer + Sec-Fetch-Trio → 3282 B
  JPEG (oder 1860 B WebP wenn `Accept: image/webp` mitgeschickt wird).
- HD-Variante via URL-Transform `/images/` → `/hd/`: 36 kB JPEG / 19 kB
  WebP, 1038×1032 statt 150×149 (Mousers Default-Thumbnail).
- Derselbe Header-Set funktioniert auch auf LCSC (53 kB JPEG, identische
  Bytes wie mit dem alten iOS-UA-Set).

## Lösung

### Code-Änderungen in `scripts/inventree_sync/client.py`

1. **Neuer Helper `_image_headers(image_url)`** der einen vollständigen
   browser-shaped Header-Set zurückgibt — derselbe für Mouser und LCSC,
   weil verifiziert wurde dass beide damit liefern.

2. **`upload_image_from_url` umbauen:**
   - `_image_headers(url)` statt der zwei aktuell hartcodierten Header.
   - Nach `raise_for_status()`: prüfen dass `Content-Type` mit `image/`
     beginnt **und** Body-Größe ≥ 200 Bytes. Sonst war es der PerimeterX-
     Block (oder ähnliches CDN-WAF-Verhalten) → nicht hochladen, WARN-log
     mit den ersten 80 Bytes des Bodys (sonst diagnostiziert man künftige
     Header-Updates blind).
   - Mouser-URL-Auto-Upgrade auf HD: Wenn die URL `/images/` enthält
     (Mouser-Pattern), versuche zunächst `/hd/`-Variante; bei Misserfolg
     (404 oder Validation-Fail) auf Original fallback. Optional, kann
     auch erstmal nur via Konstante feature-gated werden.

### Defensive Werte für die Header

Auch wenn isoliert wurde dass PerimeterX nur die *Präsenz* prüft (nicht
die Werte), setzen wir trotzdem plausible Browser-Werte ein. Falls
PerimeterX in einigen Wochen anfängt Werte zu validieren, würde die
Auto-Release-Workflow sonst still brechen.

| Header | Wert | Quelle |
|---|---|---|
| `User-Agent` | Chrome 130 stable Linux x64 | Mozilla-UA, quarterly refresh |
| `Accept` | `image/avif,image/webp,image/apng,image/*,*/*;q=0.8` | Chrome-Standard |
| `Accept-Language` | `en-US,en;q=0.9` | CI-Default, unverdächtig |
| `Referer` | Site-Root des Image-Hosts | konsistent mit `Sec-Fetch-Site: same-origin` |
| `Sec-Fetch-Dest` | `image` | echter Wert |
| `Sec-Fetch-Mode` | `no-cors` | echter Wert für Image-Loads |
| `Sec-Fetch-Site` | `same-origin` | passend zum Referer |
| `Sec-Ch-Ua` | `"Chromium";v="130", "Not.A/Brand";v="24"` | Client-Hint |
| `Sec-Ch-Ua-Mobile` | `?0` | Client-Hint |
| `Sec-Ch-Ua-Platform` | `"Linux"` | Client-Hint |

Der alte `_IOS_UA` in `fetchers.py` bleibt unverändert — der wird nur
für die LCSC-API-Calls (`wmsc.lcsc.com/ftps/wm/...`) benutzt, und der
Code-Pfad ist verifiziert produktiv.

### Probe-Script

Neues `scripts/probe_supplier_images.py` mit folgenden Aufgaben:

1. **Smoke-Test:** Bekannte Mouser- und LCSC-Bild-URLs herunterladen,
   Bytes/Content-Type/Dimensionen ausgeben. Wird gegen real CDNs
   ausgeführt — keine Mocks.
2. **Diagnostik-Tool für die Zukunft:** Wenn jemand in 6 Monaten den
   Verdacht hat dass Mouser die Header-Regeln nachgeschärft hat, ruft
   man das Script auf und sieht sofort welcher Header-Set noch geht.
3. **Vorbereitung für Pytest** (Punkt 21 im Backlog): Die Probe-Logik
   wird später in `scripts/tests/test_supplier_images.py` mit
   `requests-mock` portiert.

Ausführung: `python3 scripts/probe_supplier_images.py` — keine
Argumente, schreibt Tabelle auf stdout, exit-Code 0 nur bei vollem
Erfolg. Nicht Teil der CI (würde von GH-Runner-IPs auch
unzuverlässig sein), aber als Dev-Tool checked-in.

### Maintenance-Kommentar im Code

Über dem `_DESKTOP_UA` und dem `_image_headers`-Helper:

```python
# PerimeterX-defeating header set verifiziert 2026-06-03 gegen
# Mouser- und LCSC-CDN. Falls Image-Downloads später wieder
# 4-5 kB HTML-Bodies zurückgeben statt Bild-Bytes:
#   1. UA gegen current-stable-Chrome refreshen
#      (https://www.useragentstring.com/pages/Chrome/)
#   2. scripts/probe_supplier_images.py laufen lassen — zeigt sofort
#      ob das Problem an UA, Sec-Fetch-* oder etwas Neuem liegt.
#   3. Backlog-Doc 2026-06-03-inventree-sync-improvements-backlog.md
#      mit dem aktualisierten Befund updaten.
```

## Out of Scope (für eine spätere PR)

- **Mouser-API-Image-Path-Korrektur** auf `/hd/`: Optional, könnte hier
  schon mit rein wenn der Diff klein bleibt; sonst eigene Mini-PR.
- **Alle anderen Bugs/Features aus dem Backlog (Punkte 2–21)**: Brauchen
  einen laufenden InvenTree-Server, kommen in eigene PRs.
- **Pytest-Suite für `inventree_sync`**: `probe_supplier_images.py` ist
  die Vorstufe, echte Pytest-Migration kommt mit Backlog-Punkt 21.

## Testing

Da der InvenTree-Server offline ist (siehe README, „InvenTree sync runs
LAST and non-blocking"-Section):

- **Vor der PR:** `python3 scripts/probe_supplier_images.py` muss grün
  sein. Beide Suppliers liefern echte Bilder ≥1 kB mit `image/*`-
  Content-Type.
- **Static checks:** `python3 -m py_compile scripts/inventree_sync/client.py`
  und `python3 -m py_compile scripts/probe_supplier_images.py`.
- **Self-CI:** Der `.github/workflows/ci.yaml`-Workflow in HW-Module-CI
  selber muss grün laufen (py_compile + actionlint).
- **End-to-End-Test gegen InvenTree:** Nicht möglich solange Server down.
  Wird dokumentiert in der PR-Description; sobald InvenTree zurück ist,
  zur Verifikation einmalig die Auto-Release-Workflow gegen einen Test-
  Release-Tag laufen lassen.

## Akzeptanzkriterien

- [ ] `_image_headers(url)` Helper existiert in
      `scripts/inventree_sync/client.py`.
- [ ] `upload_image_from_url` nutzt den Helper und validiert
      Content-Type + Body-Größe vor dem Upload.
- [ ] Bei Validation-Fail wird eine WARN-Zeile mit den ersten 80 Bytes
      des Bodys geloggt, der Part wird ohne Bild fortgeschrieben (kein
      `raise`).
- [ ] `scripts/probe_supplier_images.py` exists, läuft mit Exit-Code 0
      gegen real Mouser- und LCSC-Sample-URLs, gibt Tabelle aus.
- [ ] Maintenance-Kommentar mit Refresh-Anweisung im Code.
- [ ] Self-CI grün.
