"""Pytest unit tests for MouserFetcher._parse_attributes — no network access."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.fetchers import MouserFetcher  # noqa: E402


def test_parse_attributes_basic():
    """Mouser ProductAttributes → params dict."""
    product = {
        "ProductAttributes": [
            {"AttributeName": "Resistance", "AttributeValue": "10 kOhms"},
            {"AttributeName": "Tolerance", "AttributeValue": "1 %"},
            {"AttributeName": "Voltage Rating DC", "AttributeValue": "50 V"},
        ],
    }
    result = MouserFetcher._parse_attributes(product)
    assert result == {
        "Resistance": "10 kOhms",
        "Tolerance": "1 %",
        "Voltage Rating DC": "50 V",
    }


def test_parse_attributes_missing_field():
    """Product without ProductAttributes returns empty dict."""
    assert MouserFetcher._parse_attributes({}) == {}
    assert MouserFetcher._parse_attributes({"ProductAttributes": None}) == {}
    assert MouserFetcher._parse_attributes({"ProductAttributes": []}) == {}


def test_parse_attributes_empty_or_whitespace():
    """Skip rows with empty/whitespace name or value, and strip both."""
    product = {
        "ProductAttributes": [
            {"AttributeName": "  Resistance  ", "AttributeValue": "  10kΩ  "},
            {"AttributeName": "", "AttributeValue": "ignored"},
            {"AttributeName": "ignored2", "AttributeValue": ""},
            {"AttributeName": "Tolerance", "AttributeValue": None},
            {"AttributeName": None, "AttributeValue": "x"},
        ],
    }
    result = MouserFetcher._parse_attributes(product)
    assert result == {"Resistance": "10kΩ"}


def test_parse_attributes_duplicate_name_last_wins():
    """If Mouser returns the same name twice, last value wins."""
    product = {
        "ProductAttributes": [
            {"AttributeName": "Resistance", "AttributeValue": "10 kOhms"},
            {"AttributeName": "Resistance", "AttributeValue": "10.1 kOhms"},
        ],
    }
    assert MouserFetcher._parse_attributes(product) == {"Resistance": "10.1 kOhms"}


def test_parse_attributes_non_string_coerced():
    """Mouser sometimes returns numeric AttributeValue for numeric-only specs."""
    product = {
        "ProductAttributes": [
            {"AttributeName": "Operating Temperature (Min)", "AttributeValue": -40},
            {"AttributeName": "Operating Temperature (Max)", "AttributeValue": 85},
            {"AttributeName": "Voltage", "AttributeValue": 3.3},
        ],
    }
    assert MouserFetcher._parse_attributes(product) == {
        "Operating Temperature (Min)": "-40",
        "Operating Temperature (Max)": "85",
        "Voltage": "3.3",
    }
