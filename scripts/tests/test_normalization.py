"""Pure-Python unit tests for value normalization in part-name generation."""

from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap sys.path so `inventree_sync` resolves when pytest runs from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.categories import _normalize_value


def test_normalize_strip_omega():
    """Unicode Ohm signs (Ω and Ohm-Sign) are stripped."""
    assert _normalize_value("10kΩ") == "10k"      # U+03A9 GREEK CAPITAL LETTER OMEGA
    assert _normalize_value("4.7Ω") == "4.7"      # bare omega
    assert _normalize_value("1MΩ") == "1M"        # mega preserved (see lowercase test)


def test_normalize_uppercase_k_to_lowercase():
    """Capital K (kilo) is normalized to lowercase k."""
    assert _normalize_value("10K") == "10k"
    assert _normalize_value("4.7K") == "4.7k"


def test_normalize_lowercase_m_stays_milli():
    """Lowercase m (milli) is NOT changed — ambiguity with M (mega) is preserved."""
    assert _normalize_value("10m") == "10m"
    assert _normalize_value("470mA") == "470mA"


def test_normalize_uppercase_M_stays_mega():
    """Capital M (mega) is NOT changed."""
    assert _normalize_value("1M") == "1M"
    assert _normalize_value("16MHz") == "16MHz"


def test_normalize_micro_to_u():
    """µ (U+00B5) is converted to ASCII u for InvenTree-search-friendliness."""
    assert _normalize_value("4.7µF") == "4.7uF"
    assert _normalize_value("100µH") == "100uH"


def test_normalize_strip_whitespace_between_number_and_unit():
    """Single space between digits and a SI-prefix-or-unit token is removed."""
    assert _normalize_value("10 k") == "10k"
    assert _normalize_value("100 nF") == "100nF"
    assert _normalize_value("4.7 µF") == "4.7uF"


def test_normalize_uppercase_K_with_leading_space():
    """Digit + space + capital K is normalized (KiCad mixed-case shape)."""
    assert _normalize_value("10 K") == "10k"
    assert _normalize_value("4.7 K") == "4.7k"


def test_normalize_no_trailing_whitespace_after_omega_strip():
    """Stripping Ω must not leave trailing whitespace that breaks dedup."""
    assert _normalize_value("10K Ω") == "10k"
    assert _normalize_value("10 kΩ ") == "10k"
    assert _normalize_value("1MΩ ") == "1M"


def test_normalize_idempotent():
    """Running the normalizer twice produces the same output as running it once."""
    for inp in ("10K", "10 kΩ", "4.7µF", "100 nF", "1MΩ", "10 K", "10K Ω"):
        once = _normalize_value(inp)
        twice = _normalize_value(once)
        assert once == twice, f"f({inp!r})={once!r}, f(f({inp!r}))={twice!r}"


def test_normalize_passthrough_for_non_RCL_strings():
    """Already-canonical or non-RCL strings pass through unchanged."""
    assert _normalize_value("8MHz") == "8MHz"
    assert _normalize_value("STM32U575CITx") == "STM32U575CITx"
    assert _normalize_value("100nF") == "100nF"


def test_normalize_empty_string():
    """Empty input → empty output (no exception)."""
    assert _normalize_value("") == ""


def test_normalize_compound_value_with_slash():
    """Slash-separated compound values (e.g. crystal load) keep their structure."""
    # Crystal value like "8MHz/20pF" — slash already collapsed by caller, but
    # _normalize_value should not split it further.
    assert _normalize_value("8MHz/20pF") == "8MHz/20pF"
