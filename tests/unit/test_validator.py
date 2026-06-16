"""Tests unitarios para bot/risk/validator.py — OrderValidator."""

from __future__ import annotations
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bot.core.event_bus import EventBus, EventType
from bot.risk.limits import LimitCheckResult
from bot.risk.validator import OrderRequest, OrderValidator, ValidationResult
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


def make_request(**kwargs) -> OrderRequest:
    defaults = dict(
        symbol="EURUSD",
        direction="BUY",
        entry_price=1.1000,
        sl_price=1.0980,
        tp_price=1.1050,
        signal_id=None,
        strategy_name="test_strategy",
        bot_mode="SHADOW",
    )
    defaults.update(kwargs)
    return OrderRequest(**defaults)


def make_account_state(**kwargs) -> dict:
    defaults = dict(
        balance=10000.0,
        equity=10000.0,
        initial_balance=10000.0,
        day_start_balance=10000.0,
        week_start_balance=10000.0,
        month_start_balance=10000.0,
    )
    defaults.update(kwargs)
    return defaults


def make_validator(
    limits_pass=True,
    lots=Decimal("0.25"),
    is_paused=False,
    active_pause=None,
    bot_mode=None,  # None = SHADOW equivalent (no enforcement)
    **kwargs
) -> OrderValidator:
    settings = make_settings()
    trade_repo = MagicMock()
    drawdown_repo = MagicMock()
    signal_repo = MagicMock()

    position_sizer = MagicMock()
    position_sizer.calculate_lots.return_value = lots

    risk_limits = MagicMock()
    risk_limits.check_all.return_value = [] if limits_pass else [
        LimitCheckResult(passed=False, check_name="test_fail",
                         reason="Test failure", severity="WARNING")
    ]
    risk_limits.is_paused.return_value = is_paused
    risk_limits.get_active_pause.return_value = active_pause

    event_bus = EventBus()

    return OrderValidator(
        settings=settings,
        trade_repo=trade_repo,
        drawdown_repo=drawdown_repo,
        signal_repo=signal_repo,
        position_sizer=position_sizer,
        risk_limits=risk_limits,
        event_bus=event_bus,
        bot_mode=bot_mode,
        **kwargs
    )


def _fake_symbol_info():
    """Fake symbol_info de MT5 (simula MT5 vivo) para que el sizing fail-closed
    no rechace en tests que esperan aprobación. Specs forex genéricas."""
    info = MagicMock()
    info.trade_contract_size = 100000
    info.point = 0.00001
    info.trade_tick_value = 1.0
    info.trade_tick_size = 0.00001
    info.volume_min = 0.01
    info.volume_max = 100.0
    info.volume_step = 0.01
    info.spread = 10
    return info


@pytest.fixture(autouse=True)
def _mt5_live_symbol_info():
    """Por defecto el validador 've' MT5 vivo con symbol_info real.

    El validador es fail-closed: sin symbol_info rechaza la orden. Antes había
    un fallback forex que ahora se eliminó, así que los tests deben simular MT5
    vivo. Los tests del caso 'sin symbol_info' lo sobrescriben con su propio patch.
    """
    with patch("MetaTrader5.symbol_info", return_value=_fake_symbol_info()):
        yield


class TestSlValidation:
    def test_missing_sl_rejected_critical(self):
        """SL=None must be rejected with CRITICAL severity."""
        validator = make_validator()
        request = make_request(sl_price=None)
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"
        assert "MANDATORY" in result.rejection_reason

    def test_sl_equals_entry_rejected(self):
        """SL == entry price must be rejected."""
        validator = make_validator()
        request = make_request(entry_price=1.1000, sl_price=1.1000)
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"

    def test_sl_wrong_side_buy_rejected(self):
        """BUY order with SL above entry must be rejected."""
        validator = make_validator()
        request = make_request(direction="BUY", entry_price=1.1000, sl_price=1.1050)
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"
        assert "BUY" in result.rejection_reason

    def test_sl_wrong_side_sell_rejected(self):
        """SELL order with SL below entry must be rejected."""
        validator = make_validator()
        request = make_request(direction="SELL", entry_price=1.1000, sl_price=1.0980)
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"
        assert "SELL" in result.rejection_reason

    def test_valid_buy_sl_passes(self):
        """BUY with SL below entry is valid SL-wise."""
        validator = make_validator()
        request = make_request(direction="BUY", entry_price=1.1000, sl_price=1.0980)
        result = validator.validate(request, make_account_state())
        assert result.approved is True

    def test_valid_sell_sl_passes(self):
        """SELL with SL above entry is valid SL-wise."""
        validator = make_validator()
        request = make_request(direction="SELL", entry_price=1.1000, sl_price=1.1050)
        result = validator.validate(request, make_account_state())
        assert result.approved is True


class TestKillSwitch:
    def test_kill_switch_active_blocks(self):
        """CRITICAL active pause should block orders."""
        pause = MagicMock()
        pause.severity = "CRITICAL"
        pause.reason = "Kill switch active"
        validator = make_validator(active_pause=pause)
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"


class TestPauseActive:
    def test_pause_active_blocks(self):
        """Active pause should block new orders."""
        pause = MagicMock()
        pause.severity = "ERROR"
        pause.reason = "Daily drawdown limit hit"
        pause.resume_at = datetime.now(timezone.utc) + timedelta(hours=4)
        validator = make_validator(is_paused=True, active_pause=pause)
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert "drawdown" in result.rejection_reason.lower() or result.rejection_reason is not None

    def test_no_pause_does_not_block(self):
        """No active pause should not block."""
        validator = make_validator(is_paused=False, active_pause=None)
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is True


class TestLimitsFail:
    def test_limits_fail_blocks(self):
        """When risk limits check_all returns failures, order is rejected."""
        validator = make_validator(limits_pass=False)
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert len(result.failed_checks) >= 1

    def test_limits_pass_continues(self):
        """When no limit failures, proceed to sizing."""
        validator = make_validator(limits_pass=True, lots=Decimal("0.25"))
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is True


class TestSizingZero:
    def test_sizing_zero_blocks(self):
        """When PositionSizer returns 0 lots, order is rejected."""
        validator = make_validator(lots=Decimal("0"))
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert "lots = 0" in result.rejection_reason.lower() or "0" in result.rejection_reason


class TestFullApprovalPath:
    def test_full_approval_happy_path(self):
        """Happy path: valid SL, no spread issue, no pauses, limits pass, sizing OK."""
        validator = make_validator(
            limits_pass=True,
            lots=Decimal("0.25"),
            is_paused=False,
            active_pause=None,
        )
        request = make_request(
            direction="BUY",
            entry_price=1.1000,
            sl_price=1.0980,
            tp_price=1.1050,
        )
        result = validator.validate(request, make_account_state())
        assert result.approved is True
        assert result.calculated_lots == Decimal("0.25")
        assert result.rejection_reason is None

    def test_full_approval_does_not_publish_signal_event(self):
        """L-05: On approval, SIGNAL_GENERATED must NOT be published by validator."""
        bus = EventBus()
        received = []
        bus.subscribe(EventType.SIGNAL_GENERATED, lambda p: received.append(p))

        settings = make_settings()
        trade_repo = MagicMock()
        drawdown_repo = MagicMock()
        signal_repo = MagicMock()
        position_sizer = MagicMock()
        position_sizer.calculate_lots.return_value = Decimal("0.25")
        risk_limits = MagicMock()
        risk_limits.check_all.return_value = []
        risk_limits.is_paused.return_value = False
        risk_limits.get_active_pause.return_value = None

        validator = OrderValidator(
            settings=settings,
            trade_repo=trade_repo,
            drawdown_repo=drawdown_repo,
            signal_repo=signal_repo,
            position_sizer=position_sizer,
            risk_limits=risk_limits,
            event_bus=bus,
        )

        request = make_request(direction="BUY", entry_price=1.1000, sl_price=1.0980)
        validator.validate(request, make_account_state())

        # L-05: validator must NOT publish SIGNAL_GENERATED (that's the strategy's job)
        assert len(received) == 0

    def test_sell_order_full_approval(self):
        """SELL order with valid SL above entry should be approved."""
        validator = make_validator(
            limits_pass=True,
            lots=Decimal("0.10"),
            is_paused=False,
            active_pause=None,
        )
        request = make_request(
            direction="SELL",
            entry_price=1.1000,
            sl_price=1.1050,
            tp_price=1.0950,
        )
        result = validator.validate(request, make_account_state())
        assert result.approved is True
        assert result.calculated_lots == Decimal("0.10")


class TestSessionCalendarIntegration:
    def test_session_check_weekend_blocks(self):
        """When market_calendar says it's weekend, order is rejected."""
        market_calendar = MagicMock()
        market_calendar.is_weekend.return_value = True
        market_calendar.symbol_allowed_in_current_session.return_value = False

        validator = make_validator(market_calendar=market_calendar)
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert "weekend" in result.rejection_reason.lower()

    def test_economic_calendar_blocked_rejects(self):
        """When economic_calendar says blocked, order is rejected."""
        eco_calendar = MagicMock()
        eco_calendar.is_blocked_for_symbol.return_value = True

        validator = make_validator(economic_calendar=eco_calendar)
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert "economic event" in result.rejection_reason.lower()


# ──────────────────────────────────────────────────────────────────────────────
# NEW TESTS: Coverage for risk auditor fixes
# ──────────────────────────────────────────────────────────────────────────────

class TestAccountStateMissingFields:
    def test_missing_balance_raises_value_error(self):
        """L-06: missing required account fields must raise ValueError."""
        validator = make_validator()
        request = make_request()
        incomplete = {"balance": 10000.0}  # missing equity, initial_balance, etc.
        with pytest.raises(ValueError, match="missing required fields"):
            validator.validate(request, incomplete)

    def test_all_fields_present_does_not_raise(self):
        """L-06: when all required fields are present, no ValueError raised."""
        validator = make_validator()
        request = make_request()
        result = validator.validate(request, make_account_state())
        # Should not raise, should succeed
        assert result.approved is True

    def test_missing_multiple_fields_raises_value_error(self):
        """L-06: missing multiple fields reports them all."""
        validator = make_validator()
        request = make_request()
        incomplete = {}  # all fields missing
        with pytest.raises(ValueError, match="missing required fields"):
            validator.validate(request, incomplete)


class TestValidatorBotMode:
    def test_demo_mode_requires_market_calendar(self):
        """C-04: DEMO mode requires market_calendar."""
        with pytest.raises(ValueError, match="market_calendar"):
            make_validator(bot_mode="DEMO")  # no market_calendar

    def test_live_mode_requires_economic_calendar(self):
        """C-04: LIVE mode requires economic_calendar."""
        with pytest.raises(ValueError, match="economic_calendar"):
            market_cal = MagicMock()
            make_validator(bot_mode="LIVE", market_calendar=market_cal)  # no eco_calendar

    def test_shadow_mode_does_not_require_calendars(self):
        """C-04: SHADOW mode works without calendars."""
        validator = make_validator(bot_mode="SHADOW")  # should not raise
        assert validator is not None

    def test_none_mode_does_not_require_calendars(self):
        """C-04: bot_mode=None (default) does not require calendars."""
        validator = make_validator(bot_mode=None)  # should not raise
        assert validator is not None

    def test_demo_mode_with_both_calendars_succeeds(self):
        """C-04: DEMO mode with both calendars should not raise."""
        market_cal = MagicMock()
        eco_cal = MagicMock()
        eco_cal.is_blocked_for_symbol.return_value = False
        market_cal.is_weekend.return_value = False
        market_cal.symbol_allowed_in_current_session.return_value = True
        validator = make_validator(
            bot_mode="DEMO",
            market_calendar=market_cal,
            economic_calendar=eco_cal,
        )
        assert validator is not None


class TestKillSwitchDbError:
    def test_db_error_fetching_pause_blocks_order(self):
        """H-01/C-03: DB error when fetching pause state must block orders (fail-closed)."""
        settings = make_settings()
        trade_repo = MagicMock()
        drawdown_repo = MagicMock()
        signal_repo = MagicMock()
        position_sizer = MagicMock()
        position_sizer.calculate_lots.return_value = Decimal("0.25")
        risk_limits = MagicMock()
        risk_limits.check_all.return_value = []
        risk_limits.is_paused.return_value = False
        risk_limits.get_active_pause.side_effect = Exception("DB down")
        bus = EventBus()
        validator = OrderValidator(
            settings=settings,
            trade_repo=trade_repo,
            drawdown_repo=drawdown_repo,
            signal_repo=signal_repo,
            position_sizer=position_sizer,
            risk_limits=risk_limits,
            event_bus=bus,
        )
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"


class TestPauseActiveUsesPreloadedObject:
    def test_pause_active_does_not_call_is_paused(self):
        """H-01/NEW-02: _check_pause_active must use active_pause directly, no extra DB call."""
        settings = make_settings()
        trade_repo = MagicMock()
        drawdown_repo = MagicMock()
        signal_repo = MagicMock()
        position_sizer = MagicMock()
        position_sizer.calculate_lots.return_value = Decimal("0.25")
        risk_limits = MagicMock()
        risk_limits.check_all.return_value = []
        # is_paused should NEVER be called — _check_pause_active uses active_pause directly
        risk_limits.is_paused.side_effect = AssertionError("is_paused() must not be called")
        # Return an active pause via get_active_pause
        pause = MagicMock()
        pause.severity = "ERROR"
        pause.reason = "Daily drawdown limit hit"
        pause.resume_at = None  # permanent pause
        risk_limits.get_active_pause.return_value = pause
        bus = EventBus()
        validator = OrderValidator(
            settings=settings,
            trade_repo=trade_repo,
            drawdown_repo=drawdown_repo,
            signal_repo=signal_repo,
            position_sizer=position_sizer,
            risk_limits=risk_limits,
            event_bus=bus,
        )
        request = make_request()
        result = validator.validate(request, make_account_state())
        # Must be rejected due to the active pause
        assert result.approved is False
        assert result.check_name if hasattr(result, "check_name") else True
        # is_paused() must never have been called
        risk_limits.is_paused.assert_not_called()

    def test_expired_pause_does_not_block(self):
        """H-01: an expired pause (resume_at in the past) should not block new orders."""
        from datetime import timedelta
        pause = MagicMock()
        pause.severity = "ERROR"
        pause.reason = "Old pause"
        pause.resume_at = datetime.now(timezone.utc) - timedelta(hours=1)  # expired
        validator = make_validator(active_pause=pause)
        request = make_request()
        result = validator.validate(request, make_account_state())
        assert result.approved is True  # expired pause must not block


class TestNoSignalEventOnApproval:
    def test_approval_does_not_publish_signal_generated(self):
        """L-05: validator must NOT publish SIGNAL_GENERATED on approval."""
        bus = EventBus()
        received = []
        bus.subscribe(EventType.SIGNAL_GENERATED, lambda p: received.append(p))
        settings = make_settings()
        trade_repo = MagicMock()
        drawdown_repo = MagicMock()
        signal_repo = MagicMock()
        position_sizer = MagicMock()
        position_sizer.calculate_lots.return_value = Decimal("0.25")
        risk_limits = MagicMock()
        risk_limits.check_all.return_value = []
        risk_limits.is_paused.return_value = False
        risk_limits.get_active_pause.return_value = None
        validator = OrderValidator(
            settings=settings,
            trade_repo=trade_repo,
            drawdown_repo=drawdown_repo,
            signal_repo=signal_repo,
            position_sizer=position_sizer,
            risk_limits=risk_limits,
            event_bus=bus,
        )
        request = make_request()
        validator.validate(request, make_account_state())
        assert len(received) == 0  # no SIGNAL_GENERATED


class TestSpreadCalculation:
    def test_spread_blocks_wide_spread(self):
        """H-04: spread check must block when spread > spread_max."""
        validator = make_validator(symbol_config={"EURUSD": {"spread_max": 2.0}})
        request = make_request(symbol="EURUSD")
        # Mock _get_current_spread to return a wide spread
        with patch.object(validator, "_get_current_spread", return_value=3.5):
            result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert "spread" in result.rejection_reason.lower()

    def test_spread_allows_normal_spread(self):
        """H-04: normal spread within limit should not block."""
        validator = make_validator(symbol_config={"EURUSD": {"spread_max": 2.0}})
        request = make_request(symbol="EURUSD")
        with patch.object(validator, "_get_current_spread", return_value=1.2):
            result = validator.validate(request, make_account_state())
        assert result.approved is True


class TestSymbolInfoFailClosed:
    """Sin symbol_info real de MT5, el validador NO dimensiona como forex: rechaza."""

    def test_no_symbol_info_rejects_fail_closed(self):
        """Sin symbol_info, la orden se rechaza (CRITICAL) en vez de abrirse."""
        validator = make_validator()
        request = make_request(symbol="XAUUSD")
        with patch("MetaTrader5.symbol_info", return_value=None):
            result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"
        assert "symbol_info" in result.rejection_reason.lower()

    def test_no_symbol_info_does_not_size_as_forex(self):
        """Sin symbol_info NO se llama al sizer (no se adivina con specs forex)."""
        validator = make_validator()
        request = make_request(symbol="XAUUSD")
        with patch("MetaTrader5.symbol_info", return_value=None):
            validator.validate(request, make_account_state())
        validator._sizer.calculate_lots.assert_not_called()

    def test_mt5_exception_also_fails_closed(self):
        """Si symbol_info lanza excepción, también se rechaza (no fallback)."""
        validator = make_validator()
        request = make_request(symbol="EURUSD")
        with patch("MetaTrader5.symbol_info", side_effect=RuntimeError("MT5 down")):
            result = validator.validate(request, make_account_state())
        assert result.approved is False
        assert result.rejection_severity == "CRITICAL"

    def test_forex_with_symbol_info_still_approved(self):
        """Con MT5 vivo (symbol_info real), un forex normal sigue aprobándose igual."""
        validator = make_validator(lots=Decimal("0.25"))
        request = make_request(symbol="EURUSD")
        with patch("MetaTrader5.symbol_info", return_value=_fake_symbol_info()):
            result = validator.validate(request, make_account_state())
        assert result.approved is True
        assert result.calculated_lots == Decimal("0.25")


class TestGoldSpread:
    """El oro usa pip_size=0.1 y compara spread_max en PUNTOS crudos de MT5."""

    def test_gold_spread_forex_path_uses_pip_size_point_one(self):
        """El cálculo en pips del oro usa pip_size=0.1, no 0.0001 (evita inflar 1000x)."""
        validator = make_validator()
        fake_info = MagicMock(spread=30, point=0.01)
        with patch("MetaTrader5.symbol_info", return_value=fake_info):
            pips = validator._get_current_spread("XAUUSD", in_points=False)
        # (30 * 0.01) / 0.1 == 3.0  (con el bug viejo 0.0001 daría 3000)
        assert pips == pytest.approx(3.0)

    def test_gold_spread_in_points_is_raw_mt5_spread(self):
        validator = make_validator()
        fake_info = MagicMock(spread=30, point=0.01)
        with patch("MetaTrader5.symbol_info", return_value=fake_info):
            pts = validator._get_current_spread("XAUUSD", in_points=True)
        assert pts == 30.0

    def test_gold_reasonable_spread_not_rejected(self):
        """Un spread de oro de 30 puntos NO debe rechazarse con spread_max=50."""
        validator = make_validator(
            symbol_config={"XAUUSD": {"type": "metal", "spread_max": 50}}
        )
        request = make_request(symbol="XAUUSD")
        fake_info = MagicMock(spread=30, point=0.01)
        with patch("MetaTrader5.symbol_info", return_value=fake_info):
            result = validator._check_spread(request)
        assert result is None  # no rechazado

    def test_gold_wide_spread_rejected(self):
        """Un spread de oro de 60 puntos SÍ se rechaza con spread_max=50."""
        validator = make_validator(
            symbol_config={"XAUUSD": {"type": "metal", "spread_max": 50}}
        )
        request = make_request(symbol="XAUUSD")
        fake_info = MagicMock(spread=60, point=0.01)
        with patch("MetaTrader5.symbol_info", return_value=fake_info):
            result = validator._check_spread(request)
        assert result is not None
        assert result.passed is False
        assert "spread" in result.reason.lower()
