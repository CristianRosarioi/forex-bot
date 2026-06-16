"""Tests unitarios para bot/risk/sizing.py — PositionSizer."""

from __future__ import annotations
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from bot.risk.sizing import PositionSizer, _get_correlated_group, _pip_digits
from config.settings import RiskSettings


def make_settings(**kwargs) -> RiskSettings:
    defaults = dict(
        risk_pct_per_trade=0.5,
        max_daily_drawdown_pct=3.0,
        max_weekly_drawdown_pct=6.0,
        max_monthly_drawdown_pct=10.0,
        max_open_positions=3,
        max_daily_trades=10,
        max_consecutive_losses=3,
        kill_switch_balance_pct=80.0,
    )
    defaults.update(kwargs)
    return RiskSettings(**defaults)


def make_symbol_info(**kwargs) -> dict:
    defaults = dict(
        contract_size=100000,
        point=0.00001,
        tick_value=1.0,
        tick_size=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    defaults.update(kwargs)
    return defaults


def make_sizer(settings=None, open_trades=None) -> PositionSizer:
    settings = settings or make_settings()
    trade_repo = MagicMock()
    trade_repo.get_open.return_value = open_trades or []
    return PositionSizer(settings=settings, trade_repo=trade_repo)


class TestBasicCalculation:
    """test_basic_eurusd_calculation:
    balance=10000, risk_pct=0.5%, entry=1.1000, sl=1.0980 (20 pips)
    risk_amount = 50 USD
    pip_value_per_lot = 0.0001 * 100000 = 10 USD/lot
    lots = 50 / (20 * 10) = 0.25 lots
    """
    def test_basic_eurusd_calculation(self):
        sizer = make_sizer()
        info = make_symbol_info()
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        # 50 / (20 * 10) = 0.25
        assert lots == Decimal("0.25")

    def test_larger_balance_scales_proportionally(self):
        sizer = make_sizer()
        info = make_symbol_info()
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=20000.0,
            symbol_info=info,
        )
        # 100 / (20 * 10) = 0.50
        assert lots == Decimal("0.50")


class TestZeroSlDistance:
    def test_zero_sl_distance_returns_zero(self):
        sizer = make_sizer()
        info = make_symbol_info()
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.1000,  # same as entry → 0 pips
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0")


class TestCorrelation:
    def test_correlation_reduces_sizing_by_half(self):
        """If there's an open correlated trade in same direction, lots should be halved."""
        settings = make_settings()
        trade_repo = MagicMock()
        open_trade = MagicMock()
        open_trade.symbol = "GBPUSD"
        open_trade.direction = "BUY"
        trade_repo.get_open.return_value = [open_trade]
        sizer = PositionSizer(settings=settings, trade_repo=trade_repo)

        info = make_symbol_info()
        info["direction"] = "BUY"
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        # Without correlation: 0.25, with 50% factor: 0.12 (rounded down)
        assert lots == Decimal("0.12")

    def test_no_correlation_keeps_full_size(self):
        """Opposite direction open trade should not trigger correlation reduction."""
        settings = make_settings()
        trade_repo = MagicMock()
        open_trade = MagicMock()
        open_trade.symbol = "GBPUSD"
        open_trade.direction = "SELL"  # opposite direction
        trade_repo.get_open.return_value = [open_trade]
        sizer = PositionSizer(settings=settings, trade_repo=trade_repo)

        info = make_symbol_info()
        info["direction"] = "BUY"
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0.25")

    def test_non_correlated_symbol_keeps_full_size(self):
        """Trade in different group should not affect sizing."""
        settings = make_settings()
        trade_repo = MagicMock()
        open_trade = MagicMock()
        open_trade.symbol = "USDJPY"  # different group
        open_trade.direction = "BUY"
        trade_repo.get_open.return_value = [open_trade]
        sizer = PositionSizer(settings=settings, trade_repo=trade_repo)

        info = make_symbol_info()
        info["direction"] = "BUY"
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0.25")


class TestLotLimits:
    def test_lot_min_returns_zero_when_below(self):
        """When calculated lots < lot_min, return Decimal('0')."""
        sizer = make_sizer()
        info = make_symbol_info(
            volume_min=10.0,  # very high minimum
            volume_max=100.0,
            volume_step=0.01,
        )
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0")

    def test_lot_max_caps_size(self):
        """When calculated lots > lot_max, cap at lot_max."""
        sizer = make_sizer(settings=make_settings(risk_pct_per_trade=5.0))
        info = make_symbol_info(
            volume_min=0.01,
            volume_max=0.10,  # very low maximum
            volume_step=0.01,
        )
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0.10")


class TestJpyPair:
    def test_jpy_pair_uses_correct_pip_size(self):
        """USDJPY uses 0.01 pip size (2 decimal pips).

        For USDJPY: pip_size = 0.01, pair starts with USD so:
          pip_value_per_lot = pip_size * contract_size / entry
          = 0.01 * 100000 / 150.0 = 6.6667 USD/lot
        With 20 pips SL, risk=50 USD:
          lots_base = 50 / (20 * 6.6667) ≈ 0.375 → rounded to 0.37
        Note: we must pass tick_value=None to avoid using the tick_value branch.
        """
        sizer = make_sizer()
        # Explicitly omit tick_value/tick_size so USD-base logic is used
        info = dict(
            contract_size=100000,
            point=0.001,
            tick_value=None,
            tick_size=None,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        lots = sizer.calculate_lots(
            symbol="USDJPY",
            entry_price=150.00,
            sl_price=149.80,
            account_balance=10000.0,
            symbol_info=info,
        )
        # Verify pip size used: price_diff = 0.20, pip_size=0.01, pips_at_risk = 20
        # pip_value = 0.01 * 100000 / 150 ≈ 6.6667
        # lots = 50 / (20 * 6.6667) ≈ 0.375 → rounded to 0.37
        assert lots > Decimal("0")
        assert lots == Decimal("0.37")

    def test_pip_digits_jpy(self):
        assert _pip_digits("USDJPY") == 2
        assert _pip_digits("GBPJPY") == 2

    def test_pip_digits_normal(self):
        assert _pip_digits("EURUSD") == 4
        assert _pip_digits("GBPUSD") == 4


class TestRounding:
    def test_rounding_to_lot_step(self):
        """Lots must be rounded DOWN to the lot_step."""
        sizer = make_sizer()
        # Use odd lot_step to test rounding
        info = make_symbol_info(volume_step=0.05, volume_min=0.05)
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        # 0.25 / 0.05 = 5.0 exactly → 0.25
        assert lots == Decimal("0.25")
        # Verify it's a multiple of 0.05
        assert lots % Decimal("0.05") == 0

    def test_rounding_truncates_not_rounds_up(self):
        """Verify ROUND_DOWN not ROUND_HALF_UP."""
        sizer = make_sizer()
        # entry=1.1000, sl=1.0985 → 15 pips
        # risk=50, pip_value=10, lots_base=50/(15*10)=0.3333...
        # With step 0.01 → should be 0.33, not 0.34
        info = make_symbol_info(volume_step=0.01, volume_min=0.01)
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0985,
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0.33")


class TestCorrelatedGroups:
    def test_get_correlated_group_eurusd(self):
        group = _get_correlated_group("EURUSD")
        assert group is not None
        assert "EURUSD" in group
        assert "GBPUSD" in group

    def test_get_correlated_group_unknown(self):
        group = _get_correlated_group("EURCHF")  # no pertenece a ningún grupo
        assert group is None

    def test_gold_belongs_to_correlation_group(self):
        """XAUUSD ahora participa en correlación (Regla #9): cesta anti-dólar."""
        group = _get_correlated_group("XAUUSD")
        assert group is not None
        assert "XAUUSD" in group
        # Correlaciona positivamente con los longs anti-dólar
        assert "EURUSD" in group


# ──────────────────────────────────────────────────────────────────────────────
# NEW TESTS: Coverage for risk auditor fixes
# ──────────────────────────────────────────────────────────────────────────────

class TestCorrelationScaling:
    def test_correlation_factor_two_trades_returns_quarter(self):
        """M-01: 2 correlated trades → 0.25 factor."""
        settings = make_settings()
        trade_repo = MagicMock()
        trade1 = MagicMock()
        trade1.symbol = "GBPUSD"
        trade1.direction = "BUY"
        trade2 = MagicMock()
        trade2.symbol = "AUDUSD"
        trade2.direction = "BUY"
        trade_repo.get_open.return_value = [trade1, trade2]
        sizer = PositionSizer(settings=settings, trade_repo=trade_repo)
        info = make_symbol_info()
        info["direction"] = "BUY"
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        # base=0.25, factor=0.25 → 0.0625 → rounded down to 0.06
        assert lots == Decimal("0.06")

    def test_correlation_factor_one_trade_returns_half(self):
        """M-01: 1 correlated trade → 0.5 factor."""
        settings = make_settings()
        trade_repo = MagicMock()
        trade1 = MagicMock()
        trade1.symbol = "GBPUSD"
        trade1.direction = "BUY"
        trade_repo.get_open.return_value = [trade1]
        sizer = PositionSizer(settings=settings, trade_repo=trade_repo)
        info = make_symbol_info()
        info["direction"] = "BUY"
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        # base=0.25, factor=0.5 → 0.125 → rounded down to 0.12
        assert lots == Decimal("0.12")


class TestCrossPairNoMT5:
    def test_cross_pair_without_tick_value_returns_zero(self):
        """M-02: cross pairs without MT5 tick_value return 0 lots."""
        sizer = make_sizer()
        info = dict(
            contract_size=100000,
            point=0.00001,
            tick_value=None,  # no MT5 data
            tick_size=None,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        lots = sizer.calculate_lots(
            symbol="EURGBP",  # cross pair: not starts/ends with USD
            entry_price=0.8600,
            sl_price=0.8580,
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0")

    def test_eurusd_without_tick_value_still_calculates(self):
        """M-02: EURUSD ends with USD so it uses direct pip_value formula."""
        sizer = make_sizer()
        info = dict(
            contract_size=100000,
            point=0.00001,
            tick_value=None,
            tick_size=None,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        lots = sizer.calculate_lots(
            symbol="EURUSD",
            entry_price=1.1000,
            sl_price=1.0980,
            account_balance=10000.0,
            symbol_info=info,
        )
        assert lots == Decimal("0.25")
