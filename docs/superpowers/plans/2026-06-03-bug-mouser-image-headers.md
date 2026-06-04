# Bug #1: Mouser-Image-Download Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mouser-Bilder downloaden korrekt in InvenTree, statt PerimeterX-Bot-Block-HTML als „Bild" hochzuladen.

**Architecture:** Vier kleine Module-internen Helper in `scripts/inventree_sync/client.py` — ein Browser-Fingerprint-Header-Set (`_image_headers`), eine Mouser-HD-Variante-URL-Heuristik (`_try_mouser_hd_url`), und ein umgebautes `upload_image_from_url` das beide nutzt + Body-Validation macht. Ein neues `scripts/probe_supplier_images.py` testet die Headers gegen real Mouser+LCSC-CDNs (lokal, nicht in CI).

**Tech Stack:** Python 3.14, `requests` 2.34.0, stdlib `urllib.parse` + `tempfile`. Keine neuen Dependencies.

**Spec:** [`docs/superpowers/specs/2026-06-03-bug-mouser-image-headers.md`](../specs/2026-06-03-bug-mouser-image-headers.md)

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `scripts/inventree_sync/client.py` | Modify | `_DESKTOP_UA`, `_image_headers`, `_try_mouser_hd_url` Helpers; `upload_image_from_url` refactor |
| `scripts/probe_supplier_images.py` | Create | Diagnostik-Tool: testet Header-Set gegen real CDNs |

Keine Änderungen in `fetchers.py` — `_IOS_UA` bleibt dort und wird weiterhin für die LCSC-Such-API benutzt.

---

## Task 1: Probe-Script + `_image_headers` Helper (TDD cycle)

**Files:**
- Create: `scripts/probe_supplier_images.py`
- Modify: `scripts/inventree_sync/client.py` (top of file: imports + constants + helper + maintenance comment)

### Step 1.1: Probe-Script anlegen (failing test)

- [ ] Erstelle `scripts/probe_supplier_images.py` mit folgendem Inhalt:

```python
#!/usr/bin/env python3
"""
probe_supplier_images.py — Smoke-test for the image-download header set.

Hits real Mouser + LCSC image URLs with the headers produced by
``inventree_sync.client._image_headers()``.  Prints a result table and
exits non-zero if any expected-PASS case fails.

Not part of CI (GitHub Actions runners may be flagged by Mouser's
PerimeterX); intended as a local diagnostic when image-downloads start
failing in production, and as a fixture-precursor for the future
pytest suite (Backlog item 21).

Usage:
    python3 scripts/probe_supplier_images.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

# Allow `python3 scripts/probe_supplier_images.py` from any cwd
sys.path.insert(0, str(Path(__file__).parent))

from inventree_sync.client import _image_headers


SAMPLES = [
    # (label, url, min_bytes_expected)
    (
        "Mouser SPL  (Espressif ESP32)",
        "https://www.mouser.at/images/espressifsystems/images/ESP32-D0WDRH2-V3_SPL.jpg",
        1000,
    ),
    (
        "Mouser HD   (Espressif ESP32)",
        "https://www.mouser.at/images/espressifsystems/hd/ESP32-D0WDRH2-V3_SPL.jpg",
        10000,
    ),
    (
        "LCSC 900x900 (Uniroyal 10kΩ 0805)",
        "https://assets.lcsc.com/images/lcsc/900x900/20221228_UNI-ROYAL-Uniroyal-Elec-0805W8F1002T5E_C17414_front.jpg",
        10000,
    ),
]


def probe(url: str, min_bytes: int) -> tuple[bool, str]:
    """Fetch *url* with the production header set; return (ok, detail)."""
    try:
        resp = requests.get(url, timeout=20, headers=_image_headers(url))
        resp.raise_for_status()
    except Exception as exc:
        return False, f"request failed: {exc}"

    ct = resp.headers.get("Content-Type", "")
    body = resp.content
    if not ct.startswith("image/"):
        snippet = body[:80].decode("utf-8", errors="replace").strip()
        return False, f"non-image ct={ct!r}, first 80 B: {snippet!r}"
    if len(body) < min_bytes:
        return False, f"body too small ({len(body)} < {min_bytes} B)"
    return True, f"OK ct={ct} size={len(body)}B"


def main() -> int:
    fail_count = 0
    print(f"{'STATUS':<7} | {'CASE':<35} | DETAIL")
    print("-" * 100)
    for label, url, min_bytes in SAMPLES:
        ok, detail = probe(url, min_bytes)
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail_count += 1
        print(f"{status:<7} | {label:<35} | {detail}")
    print()
    if fail_count:
        print(f"FAIL: {fail_count}/{len(SAMPLES)} probes failed.", file=sys.stderr)
        return 1
    print("All probes passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] Mache das Script ausführbar:

```bash
chmod +x scripts/probe_supplier_images.py
```

### Step 1.2: Probe-Script laufen lassen — soll fehlen schlagen

- [ ] Run:

```bash
cd /home/pbuchegger/OE5XRX/HW-Module-CI
python3 scripts/probe_supplier_images.py
```

- [ ] Erwartet: **`ImportError: cannot import name '_image_headers' from 'inventree_sync.client'`** (Failure mode: das Helper-Symbol existiert noch nicht). Wenn ein anderer Fehler kommt, NICHT weitermachen — erst diagnostizieren.

### Step 1.3: `_image_headers` Helper + Konstanten in `client.py` einfügen

- [ ] In `scripts/inventree_sync/client.py` direkt nach den bestehenden Imports (vor `logger = logging.getLogger(__name__)`) **diesen Block einfügen**:

```python
import urllib.parse
```

(Falls die Zeile nicht schon vorhanden ist; aktuell ist sie nicht im File.)

- [ ] Anschließend, **vor `def get_or_create_supplier(...)`** (also direkt nach der `logger = ...` Zeile), folgenden Block einfügen:

```python
# ---------------------------------------------------------------------------
# Image-download headers
# ---------------------------------------------------------------------------
# PerimeterX (Mouser) + LCSC-CDN-defeating header set, verified 2026-06-03
# against real CDNs.  Five of the six mandatory headers are presence-only
# from PerimeterX's perspective today — we set realistic browser values
# anyway so a future PerimeterX tightening doesn't silently break the
# Auto-Release workflow.  See docs/superpowers/specs/2026-06-03-bug-
# mouser-image-headers.md for the full analysis.
#
# Falls Image-Downloads später wieder 4–5 kB HTML statt Bild zurückgeben:
#   1. Chrome-Version unten gegen current-stable refreshen
#      (https://www.useragentstring.com/pages/Chrome/).
#   2. `python3 scripts/probe_supplier_images.py` laufen lassen — zeigt
#      sofort, ob das Problem an UA, Sec-Fetch-*, oder etwas Neuem liegt.
#   3. Spec-Doc mit dem aktualisierten Befund updaten.

_DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _image_headers(image_url: str) -> dict[str, str]:
    """Browser-fingerprint headers for PerimeterX-protected supplier CDNs.

    The same set works for both Mouser (PerimeterX) and LCSC (asset CDN),
    so callers don't need to switch on host.
    """
    parts = urllib.parse.urlsplit(image_url)
    site_root = f"{parts.scheme}://{parts.netloc}/"
    return {
        "User-Agent":         _DESKTOP_UA,
        "Accept":             "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language":    "en-US,en;q=0.9",
        "Referer":            site_root,
        "Sec-Fetch-Dest":     "image",
        "Sec-Fetch-Mode":     "no-cors",
        "Sec-Fetch-Site":     "same-origin",
        "Sec-Ch-Ua":          '"Chromium";v="130", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile":   "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
    }
```

### Step 1.4: Syntax-Check

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/client.py
```

- [ ] Erwartet: keine Ausgabe (Exit-Code 0).

### Step 1.5: Probe-Script laufen lassen — soll jetzt grün sein

- [ ] Run:

```bash
python3 scripts/probe_supplier_images.py
```

- [ ] Erwartet (Reihenfolge der Zeilen, Bytes können ±5 % schwanken):

```
STATUS  | CASE                                | DETAIL
----------------------------------------------------------------------------------------------------
PASS    | Mouser SPL  (Espressif ESP32)       | OK ct=image/webp size=1860B
PASS    | Mouser HD   (Espressif ESP32)       | OK ct=image/webp size=19068B
PASS    | LCSC 900x900 (Uniroyal 10kΩ 0805)   | OK ct=image/jpeg size=53946B

All probes passed.
```

Mouser kann statt WebP auch JPEG liefern (Content-Negotiation) — wenn alle drei Zeilen `PASS` zeigen und Bytes ≥ den `min_bytes_expected` aus dem Script, ist es korrekt. Exit-Code muss `0` sein.

### Step 1.6: Commit

- [ ] Run:

```bash
git add scripts/probe_supplier_images.py scripts/inventree_sync/client.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): _image_headers helper + probe script

Browser-shaped header set für Image-Downloads von Mouser- und LCSC-
CDNs. PerimeterX (Mouser) prüft heute nur Header-Präsenz, wir setzen
trotzdem plausible Chrome-130-Werte als Defense-in-Depth.

scripts/probe_supplier_images.py: lokales Smoke-Test-Tool für die
Header. Nicht Teil von CI (GH-Runner-IPs werden teilweise von Mouser
gefiltert), wird auch als Fixture-Vorlage für die spätere Pytest-Suite
verwendet (Backlog Punkt 21).

Verifiziert gegen Mouser SPL+HD (Espressif ESP32) und LCSC 900x900
(Uniroyal 10kΩ 0805).

Refs: docs/superpowers/specs/2026-06-03-bug-mouser-image-headers.md
EOF
)"
```

---

## Task 2: `upload_image_from_url` refactorn — Helper nutzen + Body-Validation

**Files:**
- Modify: `scripts/inventree_sync/client.py` (Funktion `upload_image_from_url` + Import-Bereinigung)

### Step 2.1: Function ersetzen

- [ ] In `scripts/inventree_sync/client.py` die existierende `upload_image_from_url`-Funktion **komplett ersetzen** durch:

```python
def upload_image_from_url(part: Part, url: str) -> None:
    """Download an image from *url* and attach it to *part*.

    Validates that the response is a plausible image (Content-Type +
    minimum size) before uploading.  Supplier CDNs — notably Mouser
    behind PerimeterX — return HTTP 200 with a ~4 kB HTML bot-block
    page when their browser-fingerprint check rejects the request; we
    catch that here and log the first 80 bytes of the body so future
    header-update needs are immediately visible.
    """
    if not url:
        return
    try:
        resp = requests.get(url, timeout=20, headers=_image_headers(url))
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Image download failed (%s): %s", url, exc)
        return

    content_type = resp.headers.get("Content-Type", "")
    body = resp.content
    if not content_type.startswith("image/") or len(body) < 200:
        snippet = body[:80].decode("utf-8", errors="replace").strip()
        logger.warning(
            "Image rejected for %s (ct=%r size=%d). First 80 B: %r",
            url, content_type, len(body), snippet,
        )
        return

    # Determine extension from Content-Type — the URL extension may lie
    # when the CDN does content-negotiation (e.g. Mouser delivers WebP
    # from a `.jpg` URL when Accept includes image/webp).
    if "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    elif "png" in content_type:
        suffix = ".png"
    elif "webp" in content_type:
        suffix = ".webp"
    elif "avif" in content_type:
        suffix = ".avif"
    elif "gif" in content_type:
        suffix = ".gif"
    else:
        suffix = ".jpg"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(body)
            tmp_path = tmp.name
        part.uploadImage(tmp_path)
        logger.info("Uploaded image to part %s", part.pk)
    except Exception as exc:
        logger.warning("Image upload failed for part %s: %s", part.pk, exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
```

### Step 2.2: Obsoleten `_IOS_UA`-Import entfernen

`_IOS_UA` wird in `client.py` nach dem Refactor nicht mehr benutzt — die `_image_headers`-Logik nutzt den eigenen `_DESKTOP_UA`. In `fetchers.py` bleibt `_IOS_UA` aber weiterhin in Verwendung für die LCSC-API-Calls, deshalb dort nicht anfassen.

- [ ] In `scripts/inventree_sync/client.py` die Zeile **entfernen**:

```python
from .fetchers import _IOS_UA
```

### Step 2.3: Syntax-Check

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/client.py
```

- [ ] Erwartet: keine Ausgabe.

- [ ] Run außerdem für Sicherheit (prüft dass kein anderer Modul den Import-Pfad gebrochen hat):

```bash
python3 -m py_compile scripts/inventree_sync/*.py
```

- [ ] Erwartet: keine Ausgabe.

### Step 2.4: Probe nochmal laufen lassen (Sanity-Check)

`upload_image_from_url` wird vom Probe-Script nicht aufgerufen — es testet nur den Header-Helper. Aber wir prüfen dass der Import-Refactor nichts gebrochen hat.

- [ ] Run:

```bash
python3 scripts/probe_supplier_images.py
```

- [ ] Erwartet: weiterhin alle 3 `PASS` (Exit-Code 0).

### Step 2.5: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/client.py
git commit -m "$(cat <<'EOF'
fix(inventree-sync): validate image-download Content-Type + body size

upload_image_from_url() lud bisher den HTML-Body einer PerimeterX-Block-
Antwort blind in eine .jpg-Tempdatei und schickte das an InvenTrees
uploadImage(). Resultat: Mouser-Parts ohne Bild (bestenfalls) oder
korrupte „Bilder" (schlimmstenfalls).

Neue Logik:
- Headers kommen aus _image_headers(url) statt zweier hartkodierter
  Zeilen mit cross-origin Referer.
- Content-Type muss mit image/ beginnen UND Body >=200 B sein
  (PerimeterX-HTML ist ~4.6 kB; valide Mouser-Thumbnails sind >=1 kB).
- Bei Mismatch werden die ersten 80 Bytes des Bodys geloggt — sonst
  diagnostiziert man künftige CDN-Header-Updates blind.
- Suffix-Erkennung um WebP/AVIF erweitert (Content-Negotiation kann
  ein JPG-URL als image/webp ausliefern).

`_IOS_UA`-Import in client.py war nach dem Refactor unbenutzt und wurde
entfernt; in fetchers.py bleibt er für die LCSC-Such-API.
EOF
)"
```

---

## Task 3: HD-URL-Transform für Mouser

**Files:**
- Modify: `scripts/inventree_sync/client.py` (`_try_mouser_hd_url` Helper hinzu, `upload_image_from_url` erweitern)
- Modify: `scripts/probe_supplier_images.py` (Test-Case für HD-URL-Transform)

### Step 3.1: `_try_mouser_hd_url` Helper hinzufügen

- [ ] In `scripts/inventree_sync/client.py`, **direkt nach** der `_image_headers`-Funktion (vor `def get_or_create_supplier`), einfügen:

```python
def _try_mouser_hd_url(url: str) -> str:
    """Upgrade a Mouser thumbnail URL to its HD variant when possible.

    The Mouser API typically returns paths like
        https://www.mouser.com/images/<mfr>/lrg/<sku>_SPL.jpg
        https://www.mouser.com/images/<mfr>/images/<sku>_SPL.jpg
    The same path with ``/hd/`` in place of ``/lrg/`` or ``/images/``
    returns a ~1000-px version (10–20× larger).  Callers are expected
    to fall back to *url* on 404/validation-fail.

    Examples
    --------
    >>> _try_mouser_hd_url("https://www.mouser.com/images/ti/lrg/X_t.jpg")
    'https://www.mouser.com/images/ti/hd/X_t.jpg'
    >>> _try_mouser_hd_url("https://www.lcsc.com/foo.jpg")
    'https://www.lcsc.com/foo.jpg'
    """
    if "mouser." not in url:
        return url
    # Order matters: try /lrg/ → /hd/ first (more specific), then /images/ → /hd/
    for needle in ("/lrg/", "/images/"):
        if needle in url:
            return url.replace(needle, "/hd/", 1)
    return url
```

### Step 3.2: `upload_image_from_url` für HD-Try-Fallback erweitern

- [ ] In `scripts/inventree_sync/client.py` die in Task 2 angelegte `upload_image_from_url` **komplett ersetzen** durch:

```python
def upload_image_from_url(part: Part, url: str) -> None:
    """Download an image from *url* and attach it to *part*.

    For Mouser URLs, tries the HD variant first and falls back to the
    original on failure.  Validates Content-Type + body size before
    upload to catch CDN bot-block pages that come back as HTTP 200 +
    HTML (see ``_image_headers`` docstring).
    """
    if not url:
        return

    # Build the list of URL candidates: HD first (if applicable), then original.
    candidates: list[str] = []
    hd = _try_mouser_hd_url(url)
    if hd != url:
        candidates.append(hd)
    candidates.append(url)

    for candidate in candidates:
        try:
            resp = requests.get(candidate, timeout=20, headers=_image_headers(candidate))
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Image download failed (%s): %s", candidate, exc)
            continue

        content_type = resp.headers.get("Content-Type", "")
        body = resp.content
        if not content_type.startswith("image/") or len(body) < 200:
            snippet = body[:80].decode("utf-8", errors="replace").strip()
            logger.warning(
                "Image rejected for %s (ct=%r size=%d). First 80 B: %r",
                candidate, content_type, len(body), snippet,
            )
            continue

        if "jpeg" in content_type or "jpg" in content_type:
            suffix = ".jpg"
        elif "png" in content_type:
            suffix = ".png"
        elif "webp" in content_type:
            suffix = ".webp"
        elif "avif" in content_type:
            suffix = ".avif"
        elif "gif" in content_type:
            suffix = ".gif"
        else:
            suffix = ".jpg"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(body)
                tmp_path = tmp.name
            part.uploadImage(tmp_path)
            logger.info("Uploaded image to part %s (from %s)", part.pk, candidate)
            return  # Erfolg — keine weiteren Fallbacks
        except Exception as exc:
            logger.warning("Image upload failed for part %s: %s", part.pk, exc)
            return  # Upload-Fehler: kein Fallback, sonst evtl. doppelter Upload
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    logger.warning("No usable image obtained from any variant of %s", url)
```

### Step 3.3: Probe-Script um HD-Transform-Verifikation erweitern

Bisher testet das Probe-Script bereits eine direkte HD-URL. Jetzt prüfen wir zusätzlich dass `_try_mouser_hd_url` aus einer SPL-URL korrekt die HD-URL macht und dass diese auch funktioniert.

- [ ] In `scripts/probe_supplier_images.py` den Import-Block erweitern:

```python
from inventree_sync.client import _image_headers, _try_mouser_hd_url
```

(Statt nur `_image_headers`.)

- [ ] Im selben File, **vor** `def main()` einen neuen Test einbauen:

```python
def check_hd_transform() -> tuple[bool, str]:
    """Verify that _try_mouser_hd_url upgrades known URL patterns."""
    cases = [
        (
            "https://www.mouser.com/images/ti/lrg/X_t.jpg",
            "https://www.mouser.com/images/ti/hd/X_t.jpg",
        ),
        (
            "https://www.mouser.at/images/espressifsystems/images/ESP32-D0WDRH2-V3_SPL.jpg",
            "https://www.mouser.at/images/espressifsystems/hd/ESP32-D0WDRH2-V3_SPL.jpg",
        ),
        (
            "https://www.lcsc.com/foo.jpg",
            "https://www.lcsc.com/foo.jpg",  # nicht-Mouser bleibt unverändert
        ),
    ]
    for src, expected in cases:
        got = _try_mouser_hd_url(src)
        if got != expected:
            return False, f"_try_mouser_hd_url({src!r}) returned {got!r}, expected {expected!r}"
    return True, f"{len(cases)} URL-transform cases OK"
```

- [ ] Ersetze die komplette `main()`-Funktion in `scripts/probe_supplier_images.py` durch:

```python
def main() -> int:
    fail_count = 0
    print(f"{'STATUS':<7} | {'CASE':<35} | DETAIL")
    print("-" * 100)

    # Offline check: HD-URL transform
    ok, detail = check_hd_transform()
    status = "PASS" if ok else "FAIL"
    if not ok:
        fail_count += 1
    print(f"{status:<7} | {'HD-URL transform (offline)':<35} | {detail}")

    # Online checks: real CDN downloads
    for label, url, min_bytes in SAMPLES:
        ok, detail = probe(url, min_bytes)
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail_count += 1
        print(f"{status:<7} | {label:<35} | {detail}")
    print()
    if fail_count:
        print(f"FAIL: {fail_count}/{len(SAMPLES) + 1} probes failed.", file=sys.stderr)
        return 1
    print("All probes passed.")
    return 0
```

(Der einzige funktionale Unterschied zur alten `main()`: ein zusätzlicher Offline-Check ganz oben, und `len(SAMPLES) + 1` im Fail-Reporter weil jetzt ein Check mehr läuft.)

### Step 3.4: Doctest-Anmerkung

Die `_try_mouser_hd_url`-Docstring enthält Doctest-Beispiele zur Dokumentation. Wir führen `python3 -m doctest` aber **nicht** aus, weil `client.py` als Teil des `inventree_sync`-Pakets package-relative Imports nutzt (`from .models import PartData`) und doctest die Datei isoliert importieren würde. Stattdessen testet `check_hd_transform()` im Probe-Script genau dieselben Cases.

### Step 3.5: Syntax-Check

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/client.py scripts/probe_supplier_images.py
```

- [ ] Erwartet: keine Ausgabe.

### Step 3.6: Probe-Script laufen lassen — jetzt 4 Tests

- [ ] Run:

```bash
python3 scripts/probe_supplier_images.py
```

- [ ] Erwartet:

```
STATUS  | CASE                                | DETAIL
----------------------------------------------------------------------------------------------------
PASS    | HD-URL transform (offline)          | 3 URL-transform cases OK
PASS    | Mouser SPL  (Espressif ESP32)       | OK ct=image/webp size=1860B
PASS    | Mouser HD   (Espressif ESP32)       | OK ct=image/webp size=19068B
PASS    | LCSC 900x900 (Uniroyal 10kΩ 0805)   | OK ct=image/jpeg size=53946B

All probes passed.
```

Exit-Code 0.

### Step 3.7: Commit

- [ ] Run:

```bash
git add scripts/inventree_sync/client.py scripts/probe_supplier_images.py
git commit -m "$(cat <<'EOF'
feat(inventree-sync): prefer Mouser HD image variant when available

Mouser-API liefert ImagePath meist als _SPL.jpg unter /images/ oder
/lrg/ — das ist die 150x150-Thumbnail-Variante. Replace mit /hd/ holt
die ~1000x1000-Variante (10–20× größer). Lokal verifiziert mit
ESP32-D0WDRH2-V3: 1.8 kB SPL → 19 kB HD.

upload_image_from_url() versucht jetzt zuerst die HD-Variante; bei
404/Validation-Fail Fallback auf die Original-URL. Nicht-Mouser-URLs
gehen unverändert direkt.

probe_supplier_images.py um einen offline-Test für die URL-Transform
ergänzt (3 Cases, keine Netzwerk-Abhängigkeit) plus eine Doctest in
_try_mouser_hd_url für IDE-/python-doctest-Verifikation.
EOF
)"
```

---

## Task 4: Final Verification

**Files:** keine Änderungen — nur Verifikation und Branch-Übersicht.

### Step 4.1: py_compile über alle veränderten Files

- [ ] Run:

```bash
python3 -m py_compile scripts/inventree_sync/*.py scripts/probe_supplier_images.py
```

- [ ] Erwartet: keine Ausgabe.

### Step 4.2: Probe-Script final

- [ ] Run:

```bash
python3 scripts/probe_supplier_images.py
```

- [ ] Erwartet: 4 PASS, Exit-Code 0 (wie in Step 3.6).

### Step 4.3: Self-CI checks lokal nachstellen

Der `.github/workflows/ci.yaml` macht u.a. `py_compile` über alle scripts und einen pip-install-dry-run. Wir prüfen lokal die wichtigsten Punkte:

- [ ] Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/kibot-check.yaml')); yaml.safe_load(open('.github/workflows/create-release-docs.yaml'))"
```

- [ ] Erwartet: keine Ausgabe (YAML-Files laden ohne Fehler).

- [ ] Run (pip-Resolve-Test):

```bash
pip install --dry-run -r scripts/requirements.txt 2>&1 | tail -3
```

- [ ] Erwartet: `Would install …` oder `Requirement already satisfied …`, kein `ERROR`.

### Step 4.4: Git-Log und Diff-Stat reviewen

- [ ] Run:

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

- [ ] Erwartet (Reihenfolge bottom-up):
  - `42924a1 spec: Bug #1 — Mouser-Image-Download Fix` (existiert schon)
  - `<sha> feat(inventree-sync): _image_headers helper + probe script` (Task 1)
  - `<sha> fix(inventree-sync): validate image-download Content-Type + body size` (Task 2)
  - `<sha> feat(inventree-sync): prefer Mouser HD image variant when available` (Task 3)

  Diff-Stat sollte zeigen:
  - `docs/superpowers/specs/2026-06-03-bug-mouser-image-headers.md` — neu, ~155 Zeilen
  - `docs/superpowers/plans/2026-06-03-bug-mouser-image-headers.md` — neu, ~XXX Zeilen (durch diesen Plan-Commit, falls noch nicht passiert)
  - `scripts/inventree_sync/client.py` — modifiziert, ~+90/-15 Zeilen
  - `scripts/probe_supplier_images.py` — neu, ~100 Zeilen

### Step 4.5: Plan-Datei selbst committen (falls noch nicht passiert)

Der Plan-Commit wird typischerweise vor der Implementierung gemacht. Falls er noch fehlt:

- [ ] Run:

```bash
git status --short docs/superpowers/plans/
```

- [ ] Falls Output etwas zeigt: 

```bash
git add docs/superpowers/plans/2026-06-03-bug-mouser-image-headers.md
git commit -m "plan: Bug #1 — Mouser-Image-Download Fix Implementation Plan"
```

### Step 4.6: Done — bereit für PR

Branch ist bereit. Die PR wird in einem separaten Schritt nach diesem Plan erstellt (siehe Akzeptanzkriterien im Spec).

---

## Akzeptanzkriterien (aus dem Spec)

- [x] `_image_headers(url)` Helper existiert in `client.py` (Task 1)
- [x] `upload_image_from_url` nutzt den Helper und validiert Content-Type + Body-Größe vor dem Upload (Task 2)
- [x] Bei Validation-Fail wird eine WARN-Zeile mit den ersten 80 Bytes des Bodys geloggt, der Part wird ohne Bild fortgeschrieben (kein `raise`) (Task 2)
- [x] `scripts/probe_supplier_images.py` exists, läuft mit Exit-Code 0 gegen real Mouser- und LCSC-Sample-URLs, gibt Tabelle aus (Task 1+3)
- [x] Maintenance-Kommentar mit Refresh-Anweisung im Code (Task 1)
- [x] Self-CI grün (Task 4)
- [x] **Bonus:** HD-URL-Transform für Mouser (Task 3)
