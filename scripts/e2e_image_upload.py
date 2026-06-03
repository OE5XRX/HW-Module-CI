#!/usr/bin/env python3
"""
e2e_image_upload.py — End-to-end smoke test for upload_image_from_url().

Connects to a real InvenTree server, creates a single throwaway test
Part, downloads a known Mouser image (ESP32-D0WDRH2-V3) via the
production code path, and verifies the Part now has an image attached.

Cleans up the test Part at the end unless KEEP_TEST_PART=1.

Required env vars:
    INVENTREE_API_HOST   — InvenTree server URL
    INVENTREE_API_TOKEN  — API token (or USERNAME + PASSWORD)

Optional env vars:
    TEST_CATEGORY_PK     — InvenTree category PK for the test Part.
                           Defaults to the first category found.
    KEEP_TEST_PART=1     — don't delete the test Part after the run
                           (useful to inspect the image in the UI).

Usage:
    source ~/.inventree_test.env
    python3 scripts/e2e_image_upload.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Allow running from any cwd
sys.path.insert(0, str(Path(__file__).parent))

from inventree.api import InvenTreeAPI
from inventree.part import Part, PartCategory

from inventree_sync.client import upload_image_from_url


TEST_IMAGE_URL = (
    "https://www.mouser.at/images/espressifsystems/images/"
    "ESP32-D0WDRH2-V3_SPL.jpg"
)

# A test name that's clearly disposable. Includes a timestamp so repeated
# runs don't collide on the "name must be unique" InvenTree constraint.
TEST_PART_NAME = f"E2E-ImageUpload-Test {int(time.time())}"


def _pick_category(api: InvenTreeAPI) -> PartCategory | None:
    """Use TEST_CATEGORY_PK if set; else first category found; else None (root)."""
    pk_env = os.environ.get("TEST_CATEGORY_PK")
    if pk_env:
        return PartCategory(api, pk=int(pk_env))
    cats = PartCategory.list(api)
    return cats[0] if cats else None


def main() -> int:
    if not os.environ.get("INVENTREE_API_HOST"):
        print("ERROR: INVENTREE_API_HOST env var not set.", file=sys.stderr)
        return 2
    if not os.environ.get("INVENTREE_API_TOKEN") and not (
        os.environ.get("INVENTREE_API_USERNAME")
        and os.environ.get("INVENTREE_API_PASSWORD")
    ):
        print(
            "ERROR: set INVENTREE_API_TOKEN or "
            "(INVENTREE_API_USERNAME + INVENTREE_API_PASSWORD).",
            file=sys.stderr,
        )
        return 2

    print(f"→ Connecting to {os.environ['INVENTREE_API_HOST']} ...")
    api = InvenTreeAPI()
    print(f"  Connected. Server version: {getattr(api, 'server_version', '?')}")

    category = _pick_category(api)
    if category is None:
        print("→ No category available — creating Part at root level.")
    else:
        print(f"→ Using category: {category.name!r} (pk={category.pk})")

    payload = {
        "name": TEST_PART_NAME,
        "description": "Throwaway part for e2e image-upload smoke test.",
        "component": True,
        "active": True,
    }
    if category is not None:
        payload["category"] = category.pk

    print(f"→ Creating test Part {TEST_PART_NAME!r} ...")
    part = Part.create(api, payload)
    print(f"  Created (pk={part.pk}).")

    try:
        print(f"→ Downloading + attaching image from:\n  {TEST_IMAGE_URL}")
        upload_image_from_url(part, TEST_IMAGE_URL)

        # Re-fetch the Part to see what InvenTree stored.
        refreshed = Part(api, pk=part.pk)
        image_field = getattr(refreshed, "image", None) or refreshed._data.get("image")
        print(f"→ Part.image after upload: {image_field!r}")

        if not image_field:
            print(
                "FAIL: upload_image_from_url() returned without exception "
                "but the Part has no image. Check warning logs above.",
                file=sys.stderr,
            )
            return 1

        # InvenTree returns image as a relative URL like "/media/part_images/xx.webp".
        # If it looks like that, we're good. If it's an HTML body or empty, fail.
        if isinstance(image_field, str) and (
            image_field.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"))
            or "/media/" in image_field
        ):
            print(f"\nPASS — Part {part.pk} has image: {image_field}")
            return 0
        print(
            f"FAIL — Part.image field is suspicious: {image_field!r}",
            file=sys.stderr,
        )
        return 1
    finally:
        if os.environ.get("KEEP_TEST_PART") == "1":
            print(
                f"\nKEEP_TEST_PART=1 — leaving Part {part.pk} in place "
                f"for inspection."
            )
        else:
            print(f"\n→ Cleaning up: deleting test Part {part.pk} ...")
            try:
                # InvenTree refuses to delete *active* Parts. Deactivate first.
                part.save({"active": False})
                part.delete()
                print("  Deleted.")
            except Exception as exc:
                # Strip any "Token <hex>" from the exception message so that
                # rerunning the script never leaks the API token to stdout.
                import re
                safe = re.sub(r"Token\s+[A-Za-z0-9._-]+", "Token ***REDACTED***", str(exc))
                print(
                    f"  WARN: delete failed: {safe}. Clean up manually: pk={part.pk}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    sys.exit(main())
