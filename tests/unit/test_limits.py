"""Tests unitarios para bot/risk/limits.py — RiskLimits."""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bot.core.event_bus import EventBus, EventType
from bot.risk.limits import RiskLimits, LimitCheckResult
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


def make_trade(symbol="EURUSD", direction="BUY", pnl=-100.0):
    t = MagicMock()
    t.symbol = symbol
    t.direction = direction
    t.pnl_currency = pnl
    t.closed_at = datetime.now(timezone.utc)
    return t


def make_limits(settings=None, open_trades=None, today_trades=None, consecutive_losses=0):
    settings = settings or make_settings()
    trade_repo = MagicMock()
    trade_repo.get_open.return_value = open_trades or []
    trade_repo.get_today.return_value = today_trades or []
    trade_repo.get_consecutive_losses.return_value = consecutive_losses
    drawdown_repo = MagicMock()
    event_bus = EventBus()
    return RiskLimits(
        settings=settings,
        trade_repo=trade_repo,
        drawdown_repo=drawdown_repo,
        event_bus=event_bus,
    )


class TestCheckOpenPositions:
    def test_check_open_positions_pass(self):
        limits = make_limits(open_trades=[make_trade(), make_trade()])
        result = limits.check_open_positions_limit()
        assert result.passed is True

    def test_check_open_positions_fail(self):
        limits = make_limits(
            settings=make_settings(max_open_positions=2),
            open_trades=[make_trade(), make_trade()],
        )
        result = limits.check_open_positions_limit()
        assert result.passed is False
        assert result.check_name == "open_positions_limit"
        assert result.severity == "WARNING"

    def test_check_open_positions_at_limit_fails(self):
        limits = make_limits(
            settings=make_settings(max_open_positions=3),
            open_trades=[make_trade(), make_trade(), make_trade()],
        )
        result = limits.check_open_positions_limit()
        assert result.passed is False


class TestCheckDailyTrades:
    def test_check_daily_trades_pass(self):
        limits = make_limits(today_trades=[make_trade()])
        result = limits.check_daily_trades_limit()
        assert result.passed is True

    def test_check_daily_trades_fail(self):
        limits = make_limits(
            settings=make_settings(max_daily_trades=2),
            today_trades=[make_trade(), make_trade()],
        )
        result = limits.check_daily_trades_limit()
        assert result.passed is False
        assert result.check_name == "daily_trades_limit"
        assert result.resume_at is not None

    def test_check_daily_trades_resume_at_midnight(self):
        limits = make_limits(
            settings=make_settings(max_daily_trades=1),
            today_trades=[make_trade()],
        )
        result = limits.check_daily_trades_limit()
        assert result.passed is False
        now = datetime.now(timezone.utc)
        # resume_at should be next midnight (tomorrow)
        assert result.resume_at is not None
        assert result.resume_at.hour == 0
        assert result.resume_at.minute == 0
        assert result.resume_at > now


class TestCheckConsecutiveLosses:
    def test_check_consecutive_losses_pass(self):
        limits = make_limits(consecutive_losses=2)
        result = limits.check_consecutive_losses()
        assert result.passed is True

    def test_check_consecutive_losses_fail(self):
        limits = make_limits(consecutive_losses=3)
        result = limits.check_consecutive_losses()
        assert result.passed is False
        assert result.check_name == "consecutive_losses"
        assert result.severity == "WARNING"
        assert result.resume_at is not None

    def test_check_consecutive_losses_resume_in_4h(self):
        limits = make_limits(consecutive_losses=5)
        before = datetime.now(timezone.utc)
        result = limits.check_consecutive_losses()
        after = datetime.now(timezone.utc)
        assert result.passed is False
        # resume_at should be ~4 hours from now
        expected_min = before + timedelta(hours=3, minutes=59)
        expected_max = after + timedelta(hours=4, minutes=1)
        assert expected_min <= result.resume_at <= expected_max


class TestCheckDailyDrawdown:
    def test_check_daily_drawdown_pass(self):
        limits = make_limits()
        result = limits.check_daily_drawdown(current_equity=9900.0, day_start_balance=10000.0)
        assert result.passed is True  # 1% loss < 3% limit

    def test_check_daily_drawdown_fail(self):
        limits = make_limits()
        result = limits.check_daily_drawdown(current_equity=9600.0, day_start_balance=10000.0)
        assert result.passed is False  # 4% loss > 3% limit
        assert result.check_name == "daily_drawdown"
        assert result.severity == "ERROR"
        assert result.resume_at is not None

    def test_check_daily_drawdown_zero_balance_passes(self):
        limits = make_limits()
        result = limits.check_daily_drawdown(current_equity=9000.0, day_start_balance=0.0)
        assert result.passed is True

    def test_check_daily_drawdown_emits_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.DRAWDOWN_LIMIT_HIT, lambda p: received.append(p))
        settings = make_settings(max_daily_drawdown_pct=3.0)
        trade_repo = MagicMock()
        trade_repo.get_open.return_value = []
        trade_repo.get_today.return_value = []
        trade_repo.get_consecutive_losses.return_value = 0
        limits = RiskLimits(settings=settings, trade_repo=trade_repo,
                            drawdown_repo=MagicMock(), event_bus=bus)
        limits.check_daily_drawdown(current_equity=9600.0, day_start_balance=10000.0)
        assert len(received) == 1
        assert received[0]["period"] == "daily"


class TestCheckWeeklyDrawdown:
    def test_check_weekly_drawdown_pass(self):
        limits = make_limits()
        result = limits.check_weekly_drawdown(current_equity=9500.0, week_start_balance=10000.0)
        assert result.passed is True  # 5% < 6%

    def test_check_weekly_drawdown_fail(self):
        limits = make_limits()
        result = limits.check_weekly_drawdown(current_equity=9300.0, week_start_balance=10000.0)
        assert result.passed is False  # 7% > 6%
        assert result.check_name == "weekly_drawdown"
        assert result.severity == "ERROR"


class TestCheckMonthlyDrawdown:
    def test_check_monthly_drawdown_pass(self):
        limits = make_limits()
        result = limits.check_monthly_drawdown(current_equity=9100.0, month_start_balance=10000.0)
        assert result.passed is True  # 9% < 10%

    def test_check_monthly_drawdown_fail(self):
        limits = make_limits()
        result = limits.check_monthly_drawdown(current_equity=8900.0, month_start_balance=10000.0)
        assert result.passed is False  # 11% > 10%
        assert result.check_name == "monthly_drawdown"


class TestCheckKillSwitch:
    def test_check_kill_switch_pass(self):
        limits = make_limits()
        result = limits.check_kill_switch(current_balance=8500.0, initial_balance=10000.0)
        assert result.passed is True  # 85% > 80% threshold

    def test_check_kill_switch_fail(self):
        limits = make_limits()
        result = limits.check_kill_switch(current_balance=7500.0, initial_balance=10000.0)
        assert result.passed is False  # 75% < 80% threshold
        assert result.severity == "CRITICAL"
        assert result.check_name == "kill_switch"

    def test_check_kill_switch_emits_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.KILL_SWITCH_TRIGGERED, lambda p: received.append(p))
        settings = make_settings(kill_switch_balance_pct=80.0)
        trade_repo = MagicMock()
        trade_repo.get_open.return_value = []
        trade_repo.get_today.return_value = []
        trade_repo.get_consecutive_losses.return_value = 0
        limits = RiskLimits(settings=settings, trade_repo=trade_repo,
                            drawdown_repo=MagicMock(), event_bus=bus)
        limits.check_kill_switch(current_balance=7000.0, initial_balance=10000.0)
        assert len(received) == 1
        assert received[0]["balance"] == 7000.0

    def test_check_kill_switch_zero_initial_passes(self):
        limits = make_limits()
        result = limits.check_kill_switch(current_balance=0.0, initial_balance=0.0)
        assert result.passed is True


class TestCheckAll:
    def test_check_all_returns_failed_only(self):
        """check_all should return only failed checks."""
        limits = make_limits(
            settings=make_settings(max_open_positions=1),
            open_trades=[make_trade()],  # 1 open, limit is 1 → fail
        )
        failed = limits.check_all(
            symbol="EURUSD",
            direction="BUY",
            current_equity=10000.0,
            current_balance=10000.0,
            initial_balance=10000.0,
            day_start_balance=10000.0,
            week_start_balance=10000.0,
            month_start_balance=10000.0,
        )
        assert len(failed) >= 1
        check_names = [r.check_name for r in failed]
        assert "open_positions_limit" in check_names

    def test_check_all_passes_when_no_limits_hit(self):
        limits = make_limits()
        failed = limits.check_all(
            symbol="EURUSD",
            direction="BUY",
            current_equity=10000.0,
            current_balance=10000.0,
            initial_balance=10000.0,
            day_start_balance=10000.0,
            week_start_balance=10000.0,
            month_start_balance=10000.0,
        )
        assert failed == []

    def test_check_all_multiple_failures(self):
        limits = make_limits(
            settings=make_settings(max_open_positions=1, max_daily_trades=1),
            open_trades=[make_trade()],
            today_trades=[make_trade()],
        )
        failed = limits.check_all(
            symbol="EURUSD",
            direction="BUY",
            current_equity=10000.0,
            current_balance=10000.0,
            initial_balance=10000.0,
            day_start_balance=10000.0,
            week_start_balance=10000.0,
            month_start_balance=10000.0,
        )
        assert len(failed) >= 2


class TestIsPausedWithDb:
    """Integration test using real DB."""

    def test_is_paused_no_engine_returns_false(self):
        """When DB engine is not initialized, is_paused() should return False safely."""
        limits = make_limits()
        # Patch get_session to raise RuntimeError (engine not initialized)
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.side_effect = RuntimeError("Engine not initialized")
            result = limits.is_paused()
        assert result is False

    def test_is_paused_with_real_db(self):
        """Real DB integration test — requires initialized engine."""
        from bot.db.session import init_engine, get_session
        from bot.db.repository import RiskPauseRepository
        try:
            init_engine()
        except Exception:
            pytest.skip("DB not available")

        # Deactivate any existing pauses first
        with get_session() as session:
            repo = RiskPauseRepository(session)
            repo.deactivate_all()

        limits = make_limits()
        assert limits.is_paused() is False

        # Create a pause
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        limits.pause_until(future, "test pause", "WARNING")
        assert limits.is_paused() is True

        # Resume
        limits.resume()
        assert limits.is_paused() is False
