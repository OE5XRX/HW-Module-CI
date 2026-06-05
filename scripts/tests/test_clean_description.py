"""Pure-Python unit tests for _clean_description (fetchers.py).

PR-8: both supplier fetchers route descriptions through a single helper
that decodes HTML entities and strips HTML tags. InvenTree rejects
description fields containing markup ("Remove HTML tags from this value").
"""

from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap sys.path so `inventree_sync` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.fetchers import _clean_description


def test_empty_string():
    assert _clean_description("") == ""


def test_none_input():
    """None must not raise — fetchers may pass through missing fields."""
    assert _clean_description(None) == None  # noqa: E711 — explicit identity


def test_plain_text_unchanged():
    assert _clean_description("100nF ceramic capacitor") == "100nF ceramic capacitor"


def test_entity_reg_decoded():
    """The bug that triggered this PR: LCSC's SIMPLE SWITCHER&reg;."""
    assert _clean_description("SIMPLE SWITCHER&reg; buck regulator") == (
        "SIMPLE SWITCHER® buck regulator"
    )


def test_entity_trade_decoded():
    assert _clean_description("Foo&trade;") == "Foo™"


def test_entity_amp_decoded():
    assert _clean_description("R&amp;D part") == "R&D part"


def test_entity_plusmn_decoded():
    """±-sign as entity (common in tolerance specs)."""
    assert _clean_description("&plusmn;1% tolerance") == "±1% tolerance"


def test_strip_b_tag():
    assert _clean_description("<b>bold</b> text") == "bold text"


def test_strip_sup_tag():
    """Used for exponents — leaves the raw digits, which is intentional
    (we don't try to convert to Unicode superscript)."""
    assert _clean_description("10<sup>3</sup>") == "103"


def test_encoded_tag_gets_decoded_then_stripped():
    """Order matters in the helper: unescape FIRST, then strip tags.

    If we stripped first the encoded brackets stay; the unescape after
    would re-introduce raw < and >.  By unescaping first we promote
    encoded tag-syntax to real tags, which then get stripped.
    """
    assert _clean_description("&lt;b&gt;real-bold&lt;/b&gt;") == "real-bold"


def test_combined_tags_and_entities():
    assert _clean_description("<b>SWITCHER&reg;</b>") == "SWITCHER®"


def test_idempotent():
    for x in ("", "plain", "SimpleSwitcher&reg;", "<b>&trade;</b>",
              "&lt;b&gt;encoded&lt;/b&gt;"):
        once = _clean_description(x)
        twice = _clean_description(once)
        assert once == twice, f"f({x!r})={once!r}, f(f({x!r}))={twice!r}"


def test_strips_trailing_whitespace():
    """Tag-stripping can leave dangling whitespace at the edges."""
    assert _clean_description("<p>foo</p>  ") == "foo"
    assert _clean_description("  <b>bar</b>") == "bar"


def test_multiline_tags():
    """Multiline descriptions (rare but seen): tags are stripped, plain
    text around them preserved as-is (no newline-collapsing — KISS)."""
    inp = "Line 1<br>Line 2"
    assert _clean_description(inp) == "Line 1Line 2"
