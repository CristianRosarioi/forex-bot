"""Tests para soporte de oro (XAUUSD) y configuración de símbolos habilitados."""

from __future__ import annotations

import pytest

from bot.execution.order_manager import _pip_size as om_pip_size
from bot.execution.position_tracker import _pip_size as pt_pip_size
from bot.core.feed import _load_enabled_symbols


class TestGoldPipSize:
    @pytest.mark.parametrize("pip_size_fn", [om_pip_size, pt_pip_size])
    def test_gold_pip_size_is_point_one(self, pip_size_fn):
        """XAUUSD usa pip size de 0.1 (no se calcula como forex)."""
        assert pip_size_fn("XAUUSD") == 0.1

    @pytest.mark.parametrize("pip_size_fn", [om_pip_size, pt_pip_size])
    def test_jpy_pip_size_unchanged(self, pip_size_fn):
        assert pip_size_fn("USDJPY") == 0.01
        assert pip_size_fn("GBPJPY") == 0.01

    @pytest.mark.parametrize("pip_size_fn", [om_pip_size, pt_pip_size])
    def test_regular_forex_pip_size_unchanged(self, pip_size_fn):
        assert pip_size_fn("EURUSD") == 0.0001
        assert pip_size_fn("USDCAD") == 0.0001

    def test_both_modules_agree_on_gold(self):
        assert om_pip_size("XAUUSD") == pt_pip_size("XAUUSD") == 0.1


class TestEnabledSymbols:
    def test_disabled_pairs_not_loaded(self):
        """NZDUSD y AUDUSD están deshabilitados → no se cargan (no generan señales)."""
        enabled = _load_enabled_symbols()
        assert "NZDUSD" not in enabled
        assert "AUDUSD" not in enabled

    def test_kept_pairs_still_enabled(self):
        enabled = _load_enabled_symbols()
        for sym in ("EURUSD", "GBPUSD", "USDJPY", "USDCAD", "GBPJPY", "EURGBP"):
            assert sym in enabled, f"{sym} debería seguir habilitado"

    def test_gold_is_enabled(self):
        assert "XAUUSD" in _load_enabled_symbols()
