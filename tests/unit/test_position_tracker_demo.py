"""Tests for DEMO-mode additions in bot/execution/position_tracker.py and get_open_live()."""

from __future__ import annotations

import sys
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

from bot.core.event_bus import EventBus, EventType


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_mt5_mock() -> MagicMock:
    m = MagicMock()
    m.DEAL_ENTRY_IN = 0
    m.DEAL_ENTRY_OUT = 1
    m.positions_get.return_value = []        # no open positions by default
    m.history_deals_get.return_value = []
    tick = MagicMock()
    tick.bid = 1.10050
    tick.ask = 1.10060
    m.symbol_info_tick.return_value = tick
    return m


def _make_open_trade(
    trade_id: int = 1,
    ticket: int = 12345678,
    symbol: str = "EURUSD",
    direction: str = "BUY",
    entry: float = 1.1000,
    sl: float = 1.0950,
    tp: float = 1.1100,
    bot_mode: str = "DEMO",
) -> MagicMock:
    t = MagicMock()
    t.id = trade_id
    t.mt5_ticket = ticket
    t.symbol = symbol
    t.direction = direction
    t.entry_price = entry
    t.sl_price = sl
    t.tp_price = tp
    t.volume = 0.01
    t.bot_mode = bot_mode
    t.strategy_name = "retest"
    now = datetime.now(timezone.utc)
    t.opened_at = now - timedelta(hours=2)
    t.closed_at = None
    return t


def _make_tracker(bus=None, bot_mode="DEMO"):
    from bot.execution.position_tracker import PositionTracker
    if bus is None:
        bus = EventBus()
    connector = MagicMock()
    trade_repo = MagicMock()
    drawdown_repo = MagicMock()
    settings = MagicMock()
    tracker = PositionTracker(
        connector=connector,
        event_bus=bus,
        trade_repo=trade_repo,
        drawdown_repo=drawdown_repo,
        settings=settings,
        bot_mode=bot_mode,
    )
    return tracker, bus


# ── get_open_live() repository test ──────────────────────────────────────────

class TestGetOpenLive:
    def test_get_open_live_returns_demo_and_live_modes(self):
        """get_open_live() must only return DEMO/LIVE trades, not PAPER."""
        from bot.db.repository import TradeRepository
        from bot.db.models import Trade

        session = MagicMock()

        demo_trade = MagicMock(spec=Trade)
        demo_trade.bot_mode = "DEMO"
        demo_trade.closed_at = None

        live_trade = MagicMock(spec=Trade)
        live_trade.bot_mode = "LIVE"
        live_trade.closed_at = None

        # Simulate scalars().all() returning DEMO + LIVE trades
        session.scalars.return_value.all.return_value = [demo_trade, live_trade]

        repo = TradeRepository(session)
        result = repo.get_open_live()

        assert len(result) == 2
        assert session.scalars.called


# ── _get_mt5_close_reason() ───────────────────────────────────────────────────

class TestGetMt5CloseReason:
    def test_sl_on_negative_profit(self):
        tracker, _ = _make_tracker()
        mt5 = _make_mt5_mock()

        losing_deal = MagicMock()
        losing_deal.entry = mt5.DEAL_ENTRY_OUT
        losing_deal.profit = -12.50
        mt5.history_deals_get.return_value = [losing_deal]

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            reason = tracker._get_mt5_close_reason(12345678)

        assert reason == "SL"

    def test_tp_on_positive_profit(self):
        tracker, _ = _make_tracker()
        mt5 = _make_mt5_mock()

        winning_deal = MagicMock()
        winning_deal.entry = mt5.DEAL_ENTRY_OUT
        winning_deal.profit = 25.00
        mt5.history_deals_get.return_value = [winning_deal]

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            reason = tracker._get_mt5_close_reason(12345678)

        assert reason == "TP"

    def test_unknown_when_no_deals(self):
        tracker, _ = _make_tracker()
        mt5 = _make_mt5_mock()
        mt5.history_deals_get.return_value = []

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            reason = tracker._get_mt5_close_reason(12345678)

        assert reason == "unknown"

    def test_unknown_when_no_out_deal(self):
        tracker, _ = _make_tracker()
        mt5 = _make_mt5_mock()

        in_deal = MagicMock()
        in_deal.entry = mt5.DEAL_ENTRY_IN
        in_deal.profit = 0.0
        mt5.history_deals_get.return_value = [in_deal]

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            reason = tracker._get_mt5_close_reason(12345678)

        assert reason == "unknown"

    def test_breakeven_profit_zero_maps_to_sl(self):
        """profit == 0 (breakeven) is treated as SL (not a win)."""
        tracker, _ = _make_tracker()
        mt5 = _make_mt5_mock()

        deal = MagicMock()
        deal.entry = mt5.DEAL_ENTRY_OUT
        deal.profit = 0.0
        mt5.history_deals_get.return_value = [deal]

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            reason = tracker._get_mt5_close_reason(12345678)

        assert reason == "SL"


# ── _sync_closed_positions() ──────────────────────────────────────────────────

class TestSyncClosedPositions:
    def _run_sync(self, tracker, open_trades, mt5_mock):
        """Runs _sync_closed_positions() with mocked session and repos."""
        trade_repo = MagicMock()
        trade_repo.get_open_live.return_value = open_trades
        drawdown_repo = MagicMock()

        with patch.dict(sys.modules, {"MetaTrader5": mt5_mock}):
            with patch("bot.execution.position_tracker.get_session") as mock_ctx, \
                 patch("bot.execution.position_tracker.TradeRepository", return_value=trade_repo), \
                 patch("bot.execution.position_tracker.DrawdownRepository", return_value=drawdown_repo):
                session = MagicMock()
                session.__enter__ = MagicMock(return_value=session)
                session.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = session

                tracker._sync_closed_positions()

        return trade_repo, drawdown_repo

    def test_closed_position_updates_db(self):
        """If MT5 has no position for a ticket, trade_repo.close() must be called."""
        tracker, _ = _make_tracker()
        trade = _make_open_trade(trade_id=1, ticket=12345678)

        mt5 = _make_mt5_mock()
        mt5.positions_get.return_value = []  # position gone from MT5

        out_deal = MagicMock()
        out_deal.entry = mt5.DEAL_ENTRY_OUT
        out_deal.profit = -10.0
        out_deal.price = 1.09500
        mt5.history_deals_get.return_value = [out_deal]

        trade_repo, _ = self._run_sync(tracker, [trade], mt5)

        trade_repo.close.assert_called_once()
        close_call = trade_repo.close.call_args[1]
        assert close_call["trade_id"] == 1
        assert close_call["close_reason"] == "SL"
        assert close_call["exit_price"] == pytest.approx(1.09500)

    def test_tp_close_on_positive_profit(self):
        """Positive profit deal → close_reason = TP."""
        tracker, _ = _make_tracker()
        trade = _make_open_trade(trade_id=2, ticket=99887766)

        mt5 = _make_mt5_mock()
        mt5.positions_get.return_value = []

        out_deal = MagicMock()
        out_deal.entry = mt5.DEAL_ENTRY_OUT
        out_deal.profit = 25.0
        out_deal.price = 1.11000
        mt5.history_deals_get.return_value = [out_deal]

        trade_repo, _ = self._run_sync(tracker, [trade], mt5)

        trade_repo.close.assert_called_once()
        close_call = trade_repo.close.call_args[1]
        assert close_call["close_reason"] == "TP"

    def test_still_open_position_not_closed(self):
        """If MT5 still holds the position, trade_repo.close() must NOT be called."""
        tracker, _ = _make_tracker()
        trade = _make_open_trade(trade_id=3, ticket=55554444)

        mt5 = _make_mt5_mock()
        position_stub = MagicMock()
        mt5.positions_get.return_value = [position_stub]  # still open in MT5

        trade_repo, _ = self._run_sync(tracker, [trade], mt5)

        trade_repo.close.assert_not_called()

    def test_closed_position_publishes_event(self):
        """A synced close must publish POSITION_CLOSED event."""
        bus = EventBus()
        tracker, _ = _make_tracker(bus=bus)
        trade = _make_open_trade(trade_id=4, ticket=11112222)

        closed_events = []
        bus.subscribe(EventType.POSITION_CLOSED, lambda p: closed_events.append(p))

        mt5 = _make_mt5_mock()
        mt5.positions_get.return_value = []

        out_deal = MagicMock()
        out_deal.entry = mt5.DEAL_ENTRY_OUT
        out_deal.profit = -5.0
        out_deal.price = 1.09500
        mt5.history_deals_get.return_value = [out_deal]

        trade_repo = MagicMock()
        trade_repo.get_open_live.return_value = [trade]
        trade_repo.get_today.return_value = []
        trade_repo.get_this_week.return_value = []
        trade_repo.get_this_month.return_value = []
        trade_repo.get_open.return_value = []
        trade_repo.get_consecutive_losses.return_value = 0
        drawdown_repo = MagicMock()

        # Mock account_info for _take_snapshot
        mt5.account_info.return_value = MagicMock(balance=1000.0, equity=995.0)

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            with patch("bot.execution.position_tracker.get_session") as mock_ctx, \
                 patch("bot.execution.position_tracker.TradeRepository", return_value=trade_repo), \
                 patch("bot.execution.position_tracker.DrawdownRepository", return_value=drawdown_repo):
                session = MagicMock()
                session.__enter__ = MagicMock(return_value=session)
                session.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = session
                tracker._sync_closed_positions()

        assert len(closed_events) == 1
        assert closed_events[0]["trade_id"] == 4
        assert closed_events[0]["close_reason"] == "SL"

    def test_no_deals_uses_current_price_as_fallback(self):
        """If history_deals_get returns nothing, falls back to current market price."""
        tracker, _ = _make_tracker()
        trade = _make_open_trade(trade_id=5, ticket=33334444, direction="BUY")

        mt5 = _make_mt5_mock()
        mt5.positions_get.return_value = []
        mt5.history_deals_get.return_value = []   # no history → fallback
        mt5.symbol_info_tick.return_value.bid = 1.09800  # BUY closes at bid

        trade_repo, _ = self._run_sync(tracker, [trade], mt5)

        trade_repo.close.assert_called_once()
        close_call = trade_repo.close.call_args[1]
        assert close_call["exit_price"] == pytest.approx(1.09800)


# ── bot_mode routing in _check_positions() ───────────────────────────────────

class TestCheckPositionsRouting:
    def test_demo_mode_uses_get_open_live(self):
        """In DEMO mode, _check_positions must query get_open_live(), not get_open_paper()."""
        tracker, _ = _make_tracker(bot_mode="DEMO")
        mt5 = _make_mt5_mock()
        mt5.positions_get.return_value = [MagicMock()]  # all positions still open

        trade_repo = MagicMock()
        trade_repo.get_open_live.return_value = []
        trade_repo.get_open_paper.return_value = [_make_open_trade()]

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            with patch("bot.execution.position_tracker.get_session") as mock_ctx, \
                 patch("bot.execution.position_tracker.TradeRepository", return_value=trade_repo), \
                 patch("bot.execution.position_tracker.DrawdownRepository"):
                session = MagicMock()
                session.__enter__ = MagicMock(return_value=session)
                session.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = session
                tracker._check_positions()

        trade_repo.get_open_live.assert_called()
        trade_repo.get_open_paper.assert_not_called()

    def test_paper_mode_uses_get_open_paper(self):
        """In PAPER mode, _check_positions must query get_open_paper()."""
        tracker, _ = _make_tracker(bot_mode="PAPER")

        trade_repo = MagicMock()
        trade_repo.get_open_paper.return_value = []

        with patch("bot.execution.position_tracker.get_session") as mock_ctx, \
             patch("bot.execution.position_tracker.TradeRepository", return_value=trade_repo), \
             patch("bot.execution.position_tracker.DrawdownRepository"):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session
            tracker._check_positions()

        trade_repo.get_open_paper.assert_called()
        trade_repo.get_open_live.assert_not_called()
