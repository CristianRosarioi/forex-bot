"""Tests unitarios para bot/risk/limits.py — RiskLimits."""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bot.core.event_bus import EventBus, EventType
from bot.risk.limits import RiskLimits, LimitCheckResult, _next_monday_utc
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


class TestConsecutiveLossesDeadlockBreak:
    """Verifica que la racha se cuenta por modo y con corte temporal tras la pausa."""

    def _limits_with_cutoff(self, cutoff, *, consecutive_losses, bot_mode="DEMO",
                            prior_pauses=0):
        """Construye RiskLimits con get_session/RiskPauseRepository mockeados para
        que el cutoff de la última pausa expirada sea `cutoff` (o None) y el conteo
        de pausas recientes (para la escalada) sea `prior_pauses`."""
        settings = make_settings(max_consecutive_losses=3)
        trade_repo = MagicMock()
        trade_repo.get_consecutive_losses.return_value = consecutive_losses
        drawdown_repo = MagicMock()
        limits = RiskLimits(
            settings=settings,
            trade_repo=trade_repo,
            drawdown_repo=drawdown_repo,
            event_bus=EventBus(),
            bot_mode=bot_mode,
        )
        mock_repo = MagicMock()
        if cutoff is None:
            mock_repo.get_last_expired_pause_by_prefix.return_value = None
        else:
            pause = MagicMock()
            pause.paused_at = cutoff
            mock_repo.get_last_expired_pause_by_prefix.return_value = pause
        mock_repo.count_pauses_by_prefix_since.return_value = prior_pauses
        ctx = patch("bot.risk.limits.get_session")
        repo_ctx = patch("bot.risk.limits.RiskPauseRepository", return_value=mock_repo)
        return limits, trade_repo, ctx, repo_ctx

    def test_passes_bot_mode_and_cutoff_to_repo(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=5)
        limits, trade_repo, ctx, repo_ctx = self._limits_with_cutoff(
            cutoff, consecutive_losses=0, bot_mode="DEMO")
        with ctx as mock_gs, repo_ctx:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            result = limits.check_consecutive_losses()
        trade_repo.get_consecutive_losses.assert_called_once_with(bot_mode="DEMO", since=cutoff)
        assert result.passed is True

    def test_deadlock_broken_streak_resets_after_pause(self):
        """Aunque la historia tenga una racha al límite, si el corte deja la racha
        efectiva por debajo del límite, el check vuelve a pasar."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        # Tras el cutoff, la racha efectiva es 0 → pasa.
        limits, trade_repo, ctx, repo_ctx = self._limits_with_cutoff(
            cutoff, consecutive_losses=0)
        with ctx as mock_gs, repo_ctx:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            result = limits.check_consecutive_losses()
        assert result.passed is True

    def test_no_pause_history_uses_none_cutoff(self):
        limits, trade_repo, ctx, repo_ctx = self._limits_with_cutoff(
            None, consecutive_losses=1, bot_mode="DEMO")
        with ctx as mock_gs, repo_ctx:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            result = limits.check_consecutive_losses()
        trade_repo.get_consecutive_losses.assert_called_once_with(bot_mode="DEMO", since=None)
        assert result.passed is True

    def test_cutoff_db_error_fails_open_to_none(self):
        """Si la consulta del cutoff falla, se usa since=None (cuenta toda la racha)."""
        settings = make_settings(max_consecutive_losses=3)
        trade_repo = MagicMock()
        trade_repo.get_consecutive_losses.return_value = 4
        limits = RiskLimits(settings, trade_repo, MagicMock(), EventBus(), bot_mode="DEMO")
        with patch("bot.risk.limits.get_session", side_effect=Exception("DB down")):
            result = limits.check_consecutive_losses()
        trade_repo.get_consecutive_losses.assert_called_once_with(bot_mode="DEMO", since=None)
        assert result.passed is False  # racha completa → bloquea (conservador)


class TestConsecutiveLossesEscalation:
    """M-1: el breaker escala a bloqueo diario si se dispara demasiado en 24h."""

    def _build(self, *, consecutive_losses, prior_pauses):
        helper = TestConsecutiveLossesDeadlockBreak()
        return helper._limits_with_cutoff(
            None, consecutive_losses=consecutive_losses, prior_pauses=prior_pauses)

    def test_no_escalation_below_threshold(self):
        """1 pausa previa (esta sería la 2ª en 24h) → pausa normal de 4h."""
        limits, trade_repo, ctx, repo_ctx = self._build(consecutive_losses=3, prior_pauses=1)
        before = datetime.now(timezone.utc)
        with ctx as mock_gs, repo_ctx:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            result = limits.check_consecutive_losses()
        after = datetime.now(timezone.utc)
        assert result.passed is False
        assert before + timedelta(hours=3, minutes=59) <= result.resume_at <= after + timedelta(hours=4, minutes=1)
        assert "escalated" not in result.reason

    def test_escalates_to_midnight_when_recurrent(self):
        """2 pausas previas (esta sería la 3ª en 24h) → bloqueo hasta medianoche."""
        limits, trade_repo, ctx, repo_ctx = self._build(consecutive_losses=3, prior_pauses=2)
        with ctx as mock_gs, repo_ctx:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            result = limits.check_consecutive_losses()
        assert result.passed is False
        # Escalada = bloqueo hasta medianoche UTC, pero nunca menos que la base 4h (clamp).
        from bot.risk.limits import _next_midnight_utc
        now = datetime.now(timezone.utc)
        expected = max(_next_midnight_utc(), now + timedelta(hours=4))
        assert abs((result.resume_at - expected).total_seconds()) < 2
        assert result.resume_at >= now + timedelta(hours=4) - timedelta(seconds=2)
        assert "escalated" in result.reason


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
        # M-03: parameter is now current_equity
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            with patch("bot.risk.limits.RiskPauseRepository"):
                result = limits.check_kill_switch(current_equity=8500.0, initial_balance=10000.0)
        assert result.passed is True  # 85% > 80% threshold

    def test_check_kill_switch_fail(self):
        limits = make_limits()
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            with patch("bot.risk.limits.RiskPauseRepository"):
                result = limits.check_kill_switch(current_equity=7500.0, initial_balance=10000.0)
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
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            with patch("bot.risk.limits.RiskPauseRepository"):
                limits.check_kill_switch(current_equity=7000.0, initial_balance=10000.0)
        assert len(received) == 1
        assert received[0]["balance"] == 7000.0

    def test_check_kill_switch_zero_initial_passes(self):
        limits = make_limits()
        result = limits.check_kill_switch(current_equity=0.0, initial_balance=0.0)
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
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            with patch("bot.risk.limits.RiskPauseRepository"):
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

    def test_is_paused_no_engine_returns_true(self):
        """C-02: When DB engine is not initialized, is_paused() must return True (fail-closed)."""
        limits = make_limits()
        # Patch get_session to raise RuntimeError (engine not initialized)
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.side_effect = RuntimeError("Engine not initialized")
            result = limits.is_paused()
        assert result is True  # fail-closed

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


# ──────────────────────────────────────────────────────────────────────────────
# NEW TESTS: Coverage for risk auditor fixes
# ──────────────────────────────────────────────────────────────────────────────

class TestKillSwitchPersistsPause:
    def test_kill_switch_creates_critical_pause(self):
        """C-01: kill switch must create a CRITICAL pause in DB."""
        limits = make_limits()
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__.return_value = mock_session
            mock_gs.return_value.__exit__.return_value = False
            mock_repo = MagicMock()
            with patch("bot.risk.limits.RiskPauseRepository") as MockRepo:
                MockRepo.return_value = mock_repo
                limits.check_kill_switch(current_equity=7000.0, initial_balance=10000.0)
                mock_repo.create_pause.assert_called_once()
                call_kwargs = mock_repo.create_pause.call_args[1]
                assert call_kwargs["severity"] == "CRITICAL"
                assert call_kwargs["resume_at"] is None  # permanent pause


class TestIsPausedFailClosed:
    def test_is_paused_returns_true_on_db_error(self):
        """C-02: DB failure must return True (fail-closed), not False."""
        limits = make_limits()
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.side_effect = Exception("DB connection lost")
            result = limits.is_paused()
        assert result is True  # fail-closed

    def test_is_paused_permanent_pause_resume_at_none(self):
        """C-01b: resume_at=None means permanent pause."""
        limits = make_limits()
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__.return_value = mock_session
            mock_gs.return_value.__exit__.return_value = False
            mock_repo = MagicMock()
            pause = MagicMock()
            pause.resume_at = None  # permanent
            mock_repo.get_active.return_value = pause
            with patch("bot.risk.limits.RiskPauseRepository") as MockRepo:
                MockRepo.return_value = mock_repo
                result = limits.is_paused()
        assert result is True  # permanent pause is active


class TestNextMondayUtc:
    def test_next_monday_from_monday_is_7_days(self):
        """H-02: from Monday, next Monday is 7 days away, not 0."""
        from bot.risk.limits import _next_monday_utc
        # Monday 2024-01-08 10:00 UTC
        monday = datetime(2024, 1, 8, 10, 0, tzinfo=timezone.utc)

        # Patch datetime.now inside limits module
        with patch("bot.risk.limits.datetime") as mock_dt:
            mock_dt.now.return_value = monday
            # Allow datetime constructor to work normally
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = _next_monday_utc()

        # Should be 2024-01-15 (7 days later), not 2024-01-08 (same day)
        assert result.weekday() == 0  # Monday
        assert result > monday
        assert (result - monday).days >= 6

    def test_next_monday_from_wednesday(self):
        """H-02: from Wednesday, next Monday is 5 days ahead."""
        from bot.risk.limits import _next_monday_utc
        wednesday = datetime(2024, 1, 10, 10, 0, tzinfo=timezone.utc)  # Wednesday

        with patch("bot.risk.limits.datetime") as mock_dt:
            mock_dt.now.return_value = wednesday
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = _next_monday_utc()

        assert result.weekday() == 0  # Monday
        assert result > wednesday


class TestKillSwitchUsesEquity:
    def test_kill_switch_uses_equity_not_balance(self):
        """M-03: kill switch must fire when equity drops, even if balance unchanged."""
        limits = make_limits()
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__.return_value = mock_session
            mock_gs.return_value.__exit__.return_value = False
            with patch("bot.risk.limits.RiskPauseRepository"):
                # Equity drops below threshold, balance unchanged
                result = limits.check_kill_switch(current_equity=7500.0, initial_balance=10000.0)
        assert result.passed is False  # 75% < 80% threshold

    def test_kill_switch_passes_when_equity_above_threshold(self):
        limits = make_limits()
        with patch("bot.risk.limits.get_session") as mock_gs:
            mock_gs.return_value.__enter__.return_value = MagicMock()
            mock_gs.return_value.__exit__.return_value = False
            with patch("bot.risk.limits.RiskPauseRepository"):
                result = limits.check_kill_switch(current_equity=8500.0, initial_balance=10000.0)
        assert result.passed is True
