"""Tests for bot/execution/position_tracker.py"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from bot.execution.position_tracker import (
    PositionTracker,
    BREAKEVEN_TRIGGER_R,
    BREAKEVEN_BUFFER_PIPS,
    BREAKEVEN_MIN_TIME_SECONDS,
    MAX_TRADE_DURATION_HOURS,
    _pip_size,
)
from bot.core.event_bus import EventBus, EventType


def _make_tracker():
    connector = MagicMock()
    bus = EventBus()
    trade_repo = MagicMock()
    drawdown_repo = MagicMock()
    settings = MagicMock()
    tracker = PositionTracker(
        connector=connector,
        event_bus=bus,
        trade_repo=trade_repo,
        drawdown_repo=drawdown_repo,
        settings=settings,
    )
    return tracker, bus


def _make_trade(
    direction="BUY",
    entry=1.1000,
    sl=1.0950,
    tp=1.1100,
    opened_seconds_ago=7200,  # 2 hours old by default
    symbol="EURUSD",
    bot_mode="PAPER",
    trade_id=1,
):
    trade = MagicMock()
    trade.id = trade_id
    trade.symbol = symbol
    trade.direction = direction
    trade.entry_price = entry
    trade.sl_price = sl
    trade.tp_price = tp
    trade.volume = 0.01
    trade.bot_mode = bot_mode
    trade.strategy_name = "retest"
    opened_at = datetime.now(timezone.utc) - timedelta(seconds=opened_seconds_ago)
    trade.opened_at = opened_at
    return trade


class TestCloseConditions:
    def test_sl_hit_buy(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="BUY", entry=1.1000, sl=1.0950)
        reason = tracker._check_close_conditions(trade, current_price=1.0940)
        assert reason == "SL"

    def test_sl_hit_sell(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="SELL", entry=1.1000, sl=1.1050)
        reason = tracker._check_close_conditions(trade, current_price=1.1060)
        assert reason == "SL"

    def test_tp_hit_buy(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="BUY", entry=1.1000, sl=1.0950, tp=1.1100)
        reason = tracker._check_close_conditions(trade, current_price=1.1105)
        assert reason == "TP"

    def test_tp_hit_sell(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="SELL", entry=1.1000, sl=1.1050, tp=1.0900)
        reason = tracker._check_close_conditions(trade, current_price=1.0895)
        assert reason == "TP"

    def test_time_expiry(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(
            direction="BUY", entry=1.1000, sl=1.0950, tp=1.1200,
            opened_seconds_ago=MAX_TRADE_DURATION_HOURS * 3600 + 60,
        )
        reason = tracker._check_close_conditions(trade, current_price=1.1050)
        assert reason == "time"

    def test_no_close_in_normal_conditions(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="BUY", entry=1.1000, sl=1.0950, tp=1.1100)
        with patch.object(tracker, "_is_session_ended", return_value=False):
            reason = tracker._check_close_conditions(trade, current_price=1.1050)
        assert reason is None

    def test_session_end_triggers_close_after_30min(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(
            direction="BUY", entry=1.1000, sl=1.0950, tp=1.1200,
            opened_seconds_ago=2000,  # >30 min
        )
        with patch.object(tracker, "_is_session_ended", return_value=True):
            reason = tracker._check_close_conditions(trade, current_price=1.1050)
        assert reason == "session_end"

    def test_session_end_ignored_if_trade_too_new(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(
            direction="BUY", entry=1.1000, sl=1.0950, tp=1.1200,
            opened_seconds_ago=100,  # < 30 min
        )
        with patch.object(tracker, "_is_session_ended", return_value=True):
            reason = tracker._check_close_conditions(trade, current_price=1.1050)
        assert reason is None


class TestBreakevenLogic:
    def test_breakeven_moves_sl_buy_at_1r(self):
        tracker, _ = _make_tracker()
        # entry=1.1000, sl=1.0950, risk=50 pips
        # 1R = 1.1050, current=1.1055 → R ≈ 1.1
        trade = _make_trade(
            direction="BUY", entry=1.1000, sl=1.0950,
            opened_seconds_ago=BREAKEVEN_MIN_TIME_SECONDS + 60,
        )
        new_sl = tracker._calc_breakeven_sl(trade, current_price=1.1055)
        expected = 1.1000 + BREAKEVEN_BUFFER_PIPS * _pip_size("EURUSD")
        assert new_sl == pytest.approx(expected)

    def test_breakeven_moves_sl_sell_at_1r(self):
        tracker, _ = _make_tracker()
        # entry=1.1000, sl=1.1050, risk=50 pips
        # 1R = 1.0950, current=1.0945 → R ≈ 1.1
        trade = _make_trade(
            direction="SELL", entry=1.1000, sl=1.1050, tp=1.0900,
            opened_seconds_ago=BREAKEVEN_MIN_TIME_SECONDS + 60,
        )
        new_sl = tracker._calc_breakeven_sl(trade, current_price=1.0945)
        expected = 1.1000 - BREAKEVEN_BUFFER_PIPS * _pip_size("EURUSD")
        assert new_sl == pytest.approx(expected)

    def test_breakeven_no_move_too_early(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(
            direction="BUY", entry=1.1000, sl=1.0950,
            opened_seconds_ago=BREAKEVEN_MIN_TIME_SECONDS - 60,  # not enough time
        )
        new_sl = tracker._calc_breakeven_sl(trade, current_price=1.1055)
        assert new_sl is None

    def test_breakeven_no_move_insufficient_r(self):
        tracker, _ = _make_tracker()
        # current price only at 0.5R
        trade = _make_trade(
            direction="BUY", entry=1.1000, sl=1.0950,
            opened_seconds_ago=BREAKEVEN_MIN_TIME_SECONDS + 60,
        )
        new_sl = tracker._calc_breakeven_sl(trade, current_price=1.1025)  # only 0.5R
        assert new_sl is None

    def test_breakeven_no_regression_buy(self):
        tracker, _ = _make_tracker()
        # current SL is already above the new breakeven level
        trade = _make_trade(
            direction="BUY", entry=1.1000,
            sl=1.1010,  # SL already above entry+buffer
            opened_seconds_ago=BREAKEVEN_MIN_TIME_SECONDS + 60,
        )
        new_sl = tracker._calc_breakeven_sl(trade, current_price=1.1055)
        assert new_sl is None  # new_sl (1.1003) < current sl (1.1010), so no improvement

    def test_breakeven_no_regression_sell(self):
        tracker, _ = _make_tracker()
        # current SL is already below the new breakeven level (already protected)
        trade = _make_trade(
            direction="SELL", entry=1.1000,
            sl=1.0990,  # SL already below entry-buffer
            tp=1.0900,
            opened_seconds_ago=BREAKEVEN_MIN_TIME_SECONDS + 60,
        )
        new_sl = tracker._calc_breakeven_sl(trade, current_price=1.0945)
        assert new_sl is None  # new_sl (1.0997) > current sl (1.0990), no improvement


class TestPnlCalculation:
    def test_pnl_pips_buy_profit(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="BUY", entry=1.1000)
        pnl_pips, pnl_currency = tracker._calc_pnl(trade, exit_price=1.1050, pip_value_per_lot=10.0)
        assert pnl_pips == pytest.approx(50.0)
        assert pnl_currency > 0

    def test_pnl_pips_buy_loss(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="BUY", entry=1.1000)
        pnl_pips, pnl_currency = tracker._calc_pnl(trade, exit_price=1.0950, pip_value_per_lot=10.0)
        assert pnl_pips == pytest.approx(-50.0)
        assert pnl_currency < 0

    def test_pnl_pips_sell_profit(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="SELL", entry=1.1000, sl=1.1050, tp=1.0900)
        # SELL: price dropped 50 pips → profit
        pnl_pips, pnl_currency = tracker._calc_pnl(trade, exit_price=1.0950, pip_value_per_lot=10.0)
        assert pnl_pips == pytest.approx(50.0)
        assert pnl_currency > 0

    def test_pnl_pips_sell_loss(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="SELL", entry=1.1000, sl=1.1050, tp=1.0900)
        # SELL: price rose 30 pips → loss
        pnl_pips, pnl_currency = tracker._calc_pnl(trade, exit_price=1.1030, pip_value_per_lot=10.0)
        assert pnl_pips == pytest.approx(-30.0)
        assert pnl_currency < 0

    def test_pnl_jpy_pair(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="BUY", entry=150.00, sl=149.50, symbol="USDJPY")
        # 50 pips profit on JPY pair
        pnl_pips, _ = tracker._calc_pnl(trade, exit_price=150.50, pip_value_per_lot=8.0)
        assert pnl_pips == pytest.approx(50.0)

    def test_pnl_currency_uses_volume(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(direction="BUY", entry=1.1000)
        trade.volume = 0.02  # double size
        pnl_pips, pnl_currency = tracker._calc_pnl(trade, exit_price=1.1010, pip_value_per_lot=10.0)
        assert pnl_pips == pytest.approx(10.0)
        # 10 pips × $10/pip × 0.02 lots = $2.00
        assert pnl_currency == pytest.approx(2.0)


class TestMonitorLoop:
    def test_check_positions_closes_on_sl(self):
        tracker, bus = _make_tracker()
        trade = _make_trade(direction="BUY", entry=1.1000, sl=1.0950, tp=1.1100)

        closed_events = []
        bus.subscribe(EventType.POSITION_CLOSED, lambda p: closed_events.append(p))

        with patch("bot.execution.position_tracker.get_session") as mock_ctx, \
             patch.object(tracker, "_get_current_price", return_value=1.0940), \
             patch.object(tracker, "_take_snapshot"):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            tr_repo = MagicMock()
            tr_repo.get_open_paper.return_value = [trade]
            dd_repo = MagicMock()

            with patch("bot.execution.position_tracker.TradeRepository", return_value=tr_repo), \
                 patch("bot.execution.position_tracker.DrawdownRepository", return_value=dd_repo):
                tracker._check_positions()

        tr_repo.close.assert_called_once()
        close_kwargs = tr_repo.close.call_args
        assert close_kwargs.kwargs.get("close_reason") == "SL" or close_kwargs[1].get("close_reason") == "SL" or \
               (len(close_kwargs[0]) > 3 and close_kwargs[0][3] == "SL")

        assert len(closed_events) == 1
        assert closed_events[0]["symbol"] == "EURUSD"

    def test_check_positions_moves_breakeven(self):
        tracker, _ = _make_tracker()
        trade = _make_trade(
            direction="BUY", entry=1.1000, sl=1.0950,
            opened_seconds_ago=BREAKEVEN_MIN_TIME_SECONDS + 60,
        )

        # R > 1.0: price at 1.1055 (1.1R)
        with patch("bot.execution.position_tracker.get_session") as mock_ctx, \
             patch.object(tracker, "_get_current_price", return_value=1.1055), \
             patch.object(tracker, "_is_session_ended", return_value=False):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            tr_repo = MagicMock()
            tr_repo.get_open_paper.return_value = [trade]
            dd_repo = MagicMock()

            with patch("bot.execution.position_tracker.TradeRepository", return_value=tr_repo), \
                 patch("bot.execution.position_tracker.DrawdownRepository", return_value=dd_repo):
                tracker._check_positions()

        tr_repo.update_sl.assert_called_once()
        new_sl = tr_repo.update_sl.call_args[0][1]
        assert new_sl > 1.1000  # moved above entry
