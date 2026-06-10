"""Unit tests for get_receive_location."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventree_sync.order_import import get_receive_location  # noqa: E402


def _stock_location(pk: int, name: str, parent: int | None = None):
    loc = MagicMock()
    loc.pk = pk
    loc.name = name
    loc.parent = parent
    return loc


def test_returns_named_location_when_present():
    api = MagicMock()
    target = _stock_location(7, "Lager")
    with patch("inventree_sync.order_import.StockLocation") as SL:
        SL.list.return_value = [target]
        result = get_receive_location(api, "Lager")
    assert result is target


def test_falls_back_to_first_top_level_when_named_missing():
    api = MagicMock()
    fallback = _stock_location(3, "Default", parent=None)
    with patch("inventree_sync.order_import.StockLocation") as SL:
        SL.list.side_effect = [
            [],                      # name= lookup returns empty
            [fallback,               # full list — first top-level
             _stock_location(4, "Sub", parent=3)],
        ]
        result = get_receive_location(api, "DoesNotExist")
    assert result is fallback


def test_raises_when_no_locations_exist_at_all():
    api = MagicMock()
    with patch("inventree_sync.order_import.StockLocation") as SL:
        SL.list.side_effect = [[], []]  # name= empty, full empty
        with pytest.raises(RuntimeError) as exc:
            get_receive_location(api, "Lager")
    assert "StockLocation" in str(exc.value)
