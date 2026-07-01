"""Tests para soporte de oro (XAUUSD) y configuración de símbolos habilitados."""

from __future__ import annotations

import pytest

from bot.core.instruments import pip_size, pip_digits, GOLD_SYMBOLS, JPY_PAIRS
from bot.execution.order_manager import _pip_size as om_pip_size
from bot.execution.position_tracker import _pip_size as pt_pip_size
from bot.core.feed import _load_enabled_symbols


class TestInstrumentsModule:
    """pip_size centralizado en bot/core/instruments (fuente única de verdad)."""

    def test_pip_size_gold_jpy_forex(self):
        assert pip_size("XAUUSD") == 0.1
        assert pip_size("USDJPY") == 0.01
        assert pip_size("EURUSD") == 0.0001

    def test_pip_size_is_case_insensitive(self):
        assert pip_size("xauusd") == 0.1
        assert pip_size("eurusd") == 0.0001

    def test_pip_digits(self):
        assert pip_digits("XAUUSD") == 1
        assert pip_digits("USDJPY") == 2
        assert pip_digits("EURUSD") == 4

    def test_execution_modules_use_shared_function(self):
        """order_manager y position_tracker re-exportan la misma función compartida."""
        assert om_pip_size is pip_size
        assert pt_pip_size is pip_size

    def test_gold_in_set(self):
        assert "XAUUSD" in GOLD_SYMBOLS
        assert "USDJPY" in JPY_PAIRS


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
    def test_reactivated_pairs_loaded(self):
        """NZDUSD y AUDUSD reactivados → vuelven a cargarse (suman volumen)."""
        enabled = _load_enabled_symbols()
        assert "NZDUSD" in enabled
        assert "AUDUSD" in enabled

    def test_kept_pairs_still_enabled(self):
        enabled = _load_enabled_symbols()
        for sym in ("EURUSD", "GBPUSD", "USDJPY", "USDCAD", "GBPJPY", "EURGBP"):
            assert sym in enabled, f"{sym} debería seguir habilitado"

    def test_indices_still_disabled(self):
        """US500 y NAS100 siguen deshabilitados hasta estar probados."""
        enabled = _load_enabled_symbols()
        assert "US500" not in enabled
        assert "NAS100" not in enabled

    def test_gold_is_enabled(self):
        assert "XAUUSD" in _load_enabled_symbols()
