"""Tests unitarios para bot/risk/validator.py — OrderValidator."""

from __future__ import annotations
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bot.core.event_bus import EventBus
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
        **kwargs
    )


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

    def test_full_approval_publishes_signal_event(self):
        """On approval, SIGNAL_GENERATED event must be published."""
        bus = EventBus()
        received = []
        from bot.core.event_bus import EventType
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

        assert len(received) == 1
        assert received[0]["symbol"] == "EURUSD"
        assert received[0]["direction"] == "BUY"

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
