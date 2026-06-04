"""Pytest unit tests for inventree_sync.part_manager._fetch_and_merge."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.models import PartData  # noqa: E402
from inventree_sync.part_manager import _fetch_and_merge  # noqa: E402


class _StubFetcher:
    """Minimal fetcher stub: returns canned PartData regardless of input."""

    def __init__(self, by_sku: dict[str, PartData], by_mpn: Optional[dict[str, PartData]] = None):
        self._by_sku = by_sku
        self._by_mpn = by_mpn or {}

    def fetch_by_sku(self, sku: str) -> Optional[PartData]:  # LCSC API
        return self._by_sku.get(sku)

    def fetch_by_mpn(self, mpn: str) -> Optional[PartData]:  # LCSC API
        return self._by_mpn.get(mpn)

    def fetch(self, sku: str) -> Optional[PartData]:  # Mouser API
        return self._by_sku.get(sku)


def test_merge_parameters_lcsc_priority_mouser_fills_gaps():
    """LCSC params win on conflict, Mouser fills keys LCSC didn't have."""
    lcsc = _StubFetcher({
        "C17414": PartData(
            mpn="RC0805-10K",
            manufacturer="Yageo",
            description="LCSC desc",
            lcsc_sku="C17414",
            parameters={"Resistance": "10kΩ", "Tolerance": "±1%"},
        ),
    })
    mouser = _StubFetcher({
        "603-RC0805-10K": PartData(
            mpn="RC0805-10K",
            manufacturer="Yageo",
            description="Mouser desc",
            mouser_sku="603-RC0805-10K",
            parameters={
                "Resistance": "10 kOhms",        # conflict → LCSC wins
                "Tolerance": "1 %",              # conflict → LCSC wins
                "Operating Temperature": "-55 to 155 °C",  # new → Mouser fills
            },
        ),
    })

    merged = _fetch_and_merge(lcsc, mouser, "C17414", "603-RC0805-10K")
    assert merged is not None

    # LCSC priority preserved for conflicting keys
    assert merged.parameters["Resistance"] == "10kΩ"
    assert merged.parameters["Tolerance"] == "±1%"
    # Mouser-only key landed
    assert merged.parameters["Operating Temperature"] == "-55 to 155 °C"
    # Three keys total
    assert len(merged.parameters) == 3


def test_merge_parameters_lcsc_only():
    """LCSC params survive when Mouser has none."""
    lcsc = _StubFetcher({
        "C17414": PartData(
            lcsc_sku="C17414",
            parameters={"Resistance": "10kΩ"},
        ),
    })
    mouser = _StubFetcher({})  # Mouser knows nothing

    merged = _fetch_and_merge(lcsc, mouser, "C17414", "")
    assert merged is not None
    assert merged.parameters == {"Resistance": "10kΩ"}


def test_merge_parameters_mouser_only():
    """Mouser params survive when LCSC has none (Mouser-only Part)."""
    lcsc = _StubFetcher({})  # LCSC knows nothing
    mouser = _StubFetcher({
        "603-NEW": PartData(
            mouser_sku="603-NEW",
            parameters={"Voltage": "50 V"},
        ),
    })

    merged = _fetch_and_merge(lcsc, mouser, "", "603-NEW")
    assert merged is not None
    assert merged.parameters == {"Voltage": "50 V"}


def test_merge_parameters_both_empty():
    """No params anywhere → empty dict, not None."""
    lcsc = _StubFetcher({
        "C0": PartData(lcsc_sku="C0"),
    })
    mouser = _StubFetcher({
        "M0": PartData(mouser_sku="M0"),
    })

    merged = _fetch_and_merge(lcsc, mouser, "C0", "M0")
    assert merged is not None
    assert merged.parameters == {}
