"""Tests for RealOrderManager in bot/execution/order_manager.py"""

from __future__ import annotations

import sys
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from bot.core.event_bus import EventBus, EventType
from bot.strategy.base import Signal as StrategySignal


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_mt5_mock(retcode: int = 10009) -> MagicMock:
    """Fake MetaTrader5 module with all needed constants and a configured order_send."""
    m = MagicMock()
    m.TRADE_RETCODE_DONE = 10009
    m.TRADE_ACTION_DEAL = 1
    m.ORDER_TYPE_BUY = 0
    m.ORDER_TYPE_SELL = 1
    m.ORDER_TIME_GTC = 1
    m.ORDER_FILLING_IOC = 1
    m.ORDER_FILLING_RETURN = 2
    m.DEAL_ENTRY_IN = 0
    m.DEAL_ENTRY_OUT = 1

    result = MagicMock()
    result.retcode = retcode
    result.order = 12345678
    result.price = 1.10020
    result.comment = "Request executed"
    result.commission = -0.5
    m.order_send.return_value = result

    tick = MagicMock()
    tick.ask = 1.10020
    tick.bid = 1.10000
    m.symbol_info_tick.return_value = tick

    return m


def _make_signal(direction="BUY", entry=1.1000, sl=1.0950, tp=1.1100, symbol="EURUSD"):
    return StrategySignal(
        symbol=symbol,
        timeframe="M15",
        strategy_name="retest",
        signal_type="retest",
        direction=direction,
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
        reason="test",
        confidence=0.8,
    )


def _make_manager(bus=None):
    if bus is None:
        bus = EventBus()
    from bot.execution.order_manager import RealOrderManager
    connector = MagicMock()
    trade_repo = MagicMock()
    signal_repo = MagicMock()
    manager = RealOrderManager(
        connector=connector,
        event_bus=bus,
        trade_repo=trade_repo,
        signal_repo=signal_repo,
    )
    return manager, bus


def _run_execute(manager, signal, mt5_mock, lots=Decimal("0.01")):
    """Helper: runs manager.execute() with full mocking of session + repos + mt5."""
    order_mock = MagicMock()
    order_mock.id = 99
    order_repo = MagicMock()
    order_repo.create.return_value = order_mock

    trade_mock = MagicMock()
    trade_mock.id = 42
    trade_repo = MagicMock()
    trade_repo.create.return_value = trade_mock

    with patch.dict(sys.modules, {"MetaTrader5": mt5_mock}):
        with patch("bot.execution.order_manager.get_session") as mock_ctx, \
             patch("bot.execution.order_manager.OrderRepository", return_value=order_repo), \
             patch("bot.execution.order_manager.TradeRepository", return_value=trade_repo):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            result = manager.execute(signal, lots, {})

    return result, order_repo, trade_repo


# ── Successful fill ───────────────────────────────────────────────────────────

class TestRealOrderManagerSuccess:
    def test_creates_order_and_trade_on_success(self):
        manager, _ = _make_manager()
        result, order_repo, trade_repo = _run_execute(manager, _make_signal(), _make_mt5_mock())

        assert result is not None
        order_repo.create.assert_called_once()
        trade_repo.create.assert_called_once()

    def test_order_data_status_filled_and_demo_mode(self):
        manager, _ = _make_manager()
        _, order_repo, _ = _run_execute(manager, _make_signal(), _make_mt5_mock())

        data = order_repo.create.call_args[0][0]
        assert data["status"] == "filled"
        assert data["bot_mode"] == "DEMO"
        assert data["symbol"] == "EURUSD"

    def test_trade_data_uses_real_mt5_ticket(self):
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()
        mt5.order_send.return_value.order = 99887766
        _, _, trade_repo = _run_execute(manager, _make_signal(), mt5)

        data = trade_repo.create.call_args[0][0]
        assert data["mt5_ticket"] == 99887766
        assert data["bot_mode"] == "DEMO"

    def test_trade_entry_price_from_mt5_fill_result(self):
        """entry_price must be result.price (actual fill), not requested price."""
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()
        mt5.order_send.return_value.price = 1.10025
        _, _, trade_repo = _run_execute(manager, _make_signal(), mt5)

        data = trade_repo.create.call_args[0][0]
        assert data["entry_price"] == pytest.approx(1.10025)

    def test_buy_uses_ask_price_in_request(self):
        """BUY request must use tick.ask as price."""
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()
        mt5.symbol_info_tick.return_value.ask = 1.10030

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            with patch("bot.execution.order_manager.get_session") as mock_ctx, \
                 patch("bot.execution.order_manager.OrderRepository") as mock_or, \
                 patch("bot.execution.order_manager.TradeRepository") as mock_tr:
                session = MagicMock()
                session.__enter__ = MagicMock(return_value=session)
                session.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = session
                mock_or.return_value.create.return_value = MagicMock(id=1)
                mock_tr.return_value.create.return_value = MagicMock(id=2)
                manager.execute(_make_signal(direction="BUY"), Decimal("0.01"), {})

        call_request = mt5.order_send.call_args[0][0]
        assert call_request["price"] == pytest.approx(1.10030)

    def test_sell_uses_bid_price_in_request(self):
        """SELL request must use tick.bid as price."""
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()
        mt5.symbol_info_tick.return_value.bid = 1.09990

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            with patch("bot.execution.order_manager.get_session") as mock_ctx, \
                 patch("bot.execution.order_manager.OrderRepository") as mock_or, \
                 patch("bot.execution.order_manager.TradeRepository") as mock_tr:
                session = MagicMock()
                session.__enter__ = MagicMock(return_value=session)
                session.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = session
                mock_or.return_value.create.return_value = MagicMock(id=1)
                mock_tr.return_value.create.return_value = MagicMock(id=2)
                manager.execute(_make_signal(direction="SELL", sl=1.1050, tp=1.09), Decimal("0.01"), {})

        call_request = mt5.order_send.call_args[0][0]
        assert call_request["price"] == pytest.approx(1.09990)

    def test_magic_number_is_234000(self):
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            with patch("bot.execution.order_manager.get_session") as mock_ctx, \
                 patch("bot.execution.order_manager.OrderRepository") as mock_or, \
                 patch("bot.execution.order_manager.TradeRepository") as mock_tr:
                session = MagicMock()
                session.__enter__ = MagicMock(return_value=session)
                session.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = session
                mock_or.return_value.create.return_value = MagicMock(id=1)
                mock_tr.return_value.create.return_value = MagicMock(id=2)
                manager.execute(_make_signal(), Decimal("0.01"), {})

        assert mt5.order_send.call_args[0][0]["magic"] == 234000

    def test_publishes_order_placed_and_filled_events(self):
        bus = EventBus()
        manager, _ = _make_manager(bus)
        placed, filled = [], []
        bus.subscribe(EventType.ORDER_PLACED, lambda p: placed.append(p))
        bus.subscribe(EventType.ORDER_FILLED, lambda p: filled.append(p))

        _run_execute(manager, _make_signal(), _make_mt5_mock())

        assert len(placed) == 1
        assert len(filled) == 1
        assert placed[0]["bot_mode"] == "DEMO"
        assert filled[0]["mt5_ticket"] == 12345678

    def test_no_fill_when_tick_unavailable(self):
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()
        mt5.symbol_info_tick.return_value = None

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            result = manager.execute(_make_signal(), Decimal("0.01"), {})

        assert result is None

    def test_no_fill_when_mt5_not_connected(self):
        manager, _ = _make_manager()
        manager._connector.ensure_connected.side_effect = RuntimeError("not connected")
        mt5 = _make_mt5_mock()

        with patch.dict(sys.modules, {"MetaTrader5": mt5}):
            result = manager.execute(_make_signal(), Decimal("0.01"), {})

        assert result is None


# ── Rejected order ────────────────────────────────────────────────────────────

class TestRealOrderManagerRejected:
    def test_rejected_returns_none(self):
        manager, _ = _make_manager()
        result, _, _ = _run_execute(manager, _make_signal(), _make_mt5_mock(retcode=10006))
        assert result is None

    def test_rejected_persists_order_with_rejected_status(self):
        manager, _ = _make_manager()
        _, order_repo, _ = _run_execute(manager, _make_signal(), _make_mt5_mock(retcode=10006))

        order_repo.create.assert_called_once()
        data = order_repo.create.call_args[0][0]
        assert data["status"] == "rejected"
        assert "MT5 retcode=10006" in data["rejection_reason"]
        assert data["bot_mode"] == "DEMO"

    def test_rejected_does_not_create_trade(self):
        manager, _ = _make_manager()
        _, _, trade_repo = _run_execute(manager, _make_signal(), _make_mt5_mock(retcode=10006))
        trade_repo.create.assert_not_called()

    def test_rejected_publishes_order_rejected_event(self):
        bus = EventBus()
        manager, _ = _make_manager(bus)
        rejected_events = []
        bus.subscribe(EventType.ORDER_REJECTED, lambda p: rejected_events.append(p))

        _run_execute(manager, _make_signal(), _make_mt5_mock(retcode=10006))

        assert len(rejected_events) == 1
        assert rejected_events[0]["retcode"] == 10006
        assert rejected_events[0]["bot_mode"] == "DEMO"


# ── IOC → RETURN retry ────────────────────────────────────────────────────────

class TestRealOrderManagerRetry:
    def test_ioc_10030_retries_with_return_filling(self):
        """When first call returns 10030 (INVALID_FILL), retry with ORDER_FILLING_RETURN."""
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()

        first = MagicMock()
        first.retcode = 10030

        second = MagicMock()
        second.retcode = 10009
        second.order = 99999
        second.price = 1.10020
        second.commission = 0.0

        mt5.order_send.side_effect = [first, second]

        _, order_repo, trade_repo = _run_execute(manager, _make_signal(), mt5)

        assert mt5.order_send.call_count == 2
        retry_request = mt5.order_send.call_args_list[1][0][0]
        assert retry_request["type_filling"] == mt5.ORDER_FILLING_RETURN

        # Second call succeeded: Order + Trade persisted
        order_repo.create.assert_called_once()
        trade_repo.create.assert_called_once()

    def test_non_10030_error_does_not_retry(self):
        """Errors other than 10030 must NOT trigger a second order_send call."""
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock(retcode=10006)

        _run_execute(manager, _make_signal(), mt5)

        assert mt5.order_send.call_count == 1

    def test_10030_retry_that_also_fails_returns_none(self):
        """If the RETURN retry also fails, return None and persist rejected order."""
        manager, _ = _make_manager()
        mt5 = _make_mt5_mock()

        first = MagicMock()
        first.retcode = 10030
        second = MagicMock()
        second.retcode = 10006
        second.comment = "Still rejected"
        mt5.order_send.side_effect = [first, second]

        result, order_repo, _ = _run_execute(manager, _make_signal(), mt5)

        assert result is None
        assert mt5.order_send.call_count == 2
        data = order_repo.create.call_args[0][0]
        assert data["status"] == "rejected"
