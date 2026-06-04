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

from inventree_sync.client import _image_headers, _try_mouser_hd_url


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

    ct = resp.headers.get("Content-Type", "").lower()
    body = resp.content
    if not ct.startswith("image/"):
        snippet = body[:80].decode("utf-8", errors="replace").strip()
        return False, f"non-image ct={ct!r}, first 80 B: {snippet!r}"
    # JPEG/PNG only — WebP/AVIF support varies by InvenTree-instance Pillow
    # build flags and front-end version (see _image_headers docstring).  The
    # production Accept header excludes them; if a supplier CDN now delivers
    # them anyway, fail loud rather than ship a possibly-unrenderable image.
    if "webp" in ct or "avif" in ct:
        return False, f"non-jpeg/png ct={ct!r} (refresh _image_headers Accept?)"
    if len(body) < min_bytes:
        return False, f"body too small ({len(body)} < {min_bytes} B)"
    return True, f"OK ct={ct} size={len(body)}B"


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


if __name__ == "__main__":
    sys.exit(main())
