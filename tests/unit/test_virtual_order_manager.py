"""Tests for bot/execution/order_manager.py"""

from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

from bot.execution.order_manager import VirtualOrderManager, RealOrderManager, MAX_SLIPPAGE_PIPS
from bot.core.event_bus import EventBus, EventType
from bot.strategy.base import Signal as StrategySignal


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


def _make_tick(ask=1.1002, bid=1.1000):
    tick = MagicMock()
    tick.ask = ask
    tick.bid = bid
    return tick


def _make_manager(bus=None):
    if bus is None:
        bus = EventBus()
    connector = MagicMock()
    trade_repo = MagicMock()
    signal_repo = MagicMock()
    manager = VirtualOrderManager(
        connector=connector,
        event_bus=bus,
        trade_repo=trade_repo,
        signal_repo=signal_repo,
    )
    return manager, bus


class TestVirtualOrderManagerFillPrice:
    def test_buy_uses_ask_price(self):
        manager, _ = _make_manager()
        tick = _make_tick(ask=1.10020, bid=1.10000)
        signal = _make_signal(direction="BUY", entry=1.10000)

        captured_trade_data = {}

        def fake_create(data):
            if "entry_price" in data:
                captured_trade_data.update(data)
                obj = MagicMock()
                obj.id = 1
                return obj
            obj = MagicMock()
            obj.id = 99
            return obj

        with patch("bot.execution.order_manager.get_session") as mock_ctx, \
             patch("MetaTrader5.symbol_info_tick", return_value=tick):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            order_repo = MagicMock()
            order_mock = MagicMock()
            order_mock.id = 99
            order_repo.create.return_value = order_mock

            trade_mock = MagicMock()
            trade_mock.id = 1
            trade_repo = MagicMock()
            trade_repo.create.side_effect = lambda d: trade_mock

            with patch("bot.execution.order_manager.OrderRepository", return_value=order_repo), \
                 patch("bot.execution.order_manager.TradeRepository", return_value=trade_repo):
                manager.execute(signal, Decimal("0.01"), {})

        trade_call = trade_repo.create.call_args[0][0]
        assert trade_call["entry_price"] == pytest.approx(1.10020)

    def test_sell_uses_bid_price(self):
        manager, _ = _make_manager()
        tick = _make_tick(ask=1.10020, bid=1.10000)
        signal = _make_signal(direction="SELL", entry=1.10010, sl=1.10060, tp=1.09910)

        with patch("bot.execution.order_manager.get_session") as mock_ctx, \
             patch("MetaTrader5.symbol_info_tick", return_value=tick):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            order_repo = MagicMock()
            order_mock = MagicMock()
            order_mock.id = 99
            order_repo.create.return_value = order_mock

            trade_mock = MagicMock()
            trade_mock.id = 1
            trade_repo = MagicMock()
            trade_repo.create.return_value = trade_mock

            with patch("bot.execution.order_manager.OrderRepository", return_value=order_repo), \
                 patch("bot.execution.order_manager.TradeRepository", return_value=trade_repo):
                manager.execute(signal, Decimal("0.01"), {})

        trade_call = trade_repo.create.call_args[0][0]
        assert trade_call["entry_price"] == pytest.approx(1.10000)


class TestVirtualOrderManagerDB:
    def test_creates_order_and_trade(self):
        manager, _ = _make_manager()
        tick = _make_tick(ask=1.10020, bid=1.10000)
        signal = _make_signal(direction="BUY")

        with patch("bot.execution.order_manager.get_session") as mock_ctx, \
             patch("MetaTrader5.symbol_info_tick", return_value=tick):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            order_repo = MagicMock()
            order_mock = MagicMock()
            order_mock.id = 10
            order_repo.create.return_value = order_mock

            trade_mock = MagicMock()
            trade_mock.id = 20
            trade_repo = MagicMock()
            trade_repo.create.return_value = trade_mock

            with patch("bot.execution.order_manager.OrderRepository", return_value=order_repo), \
                 patch("bot.execution.order_manager.TradeRepository", return_value=trade_repo):
                manager.execute(signal, Decimal("0.02"), {})

        order_repo.create.assert_called_once()
        trade_repo.create.assert_called_once()

        order_data = order_repo.create.call_args[0][0]
        assert order_data["status"] == "filled"
        assert order_data["bot_mode"] == "PAPER"
        assert order_data["symbol"] == "EURUSD"
        assert order_data["direction"] == "BUY"

        trade_data = trade_repo.create.call_args[0][0]
        assert trade_data["bot_mode"] == "PAPER"
        assert trade_data["symbol"] == "EURUSD"
        assert trade_data["volume"] == pytest.approx(0.02)
        assert trade_data["order_id"] == 10
        assert trade_data["mt5_ticket"] is not None

    def test_returns_trade_object(self):
        manager, _ = _make_manager()
        tick = _make_tick()
        signal = _make_signal()

        with patch("bot.execution.order_manager.get_session") as mock_ctx, \
             patch("MetaTrader5.symbol_info_tick", return_value=tick):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            order_mock = MagicMock()
            order_mock.id = 1
            order_repo = MagicMock()
            order_repo.create.return_value = order_mock

            trade_mock = MagicMock()
            trade_mock.id = 42
            trade_repo = MagicMock()
            trade_repo.create.return_value = trade_mock

            with patch("bot.execution.order_manager.OrderRepository", return_value=order_repo), \
                 patch("bot.execution.order_manager.TradeRepository", return_value=trade_repo):
                result = manager.execute(signal, Decimal("0.01"), {})

        assert result is trade_mock


class TestVirtualOrderManagerEvents:
    def test_publishes_order_placed_and_filled(self):
        bus = EventBus()
        manager, _ = _make_manager(bus)
        tick = _make_tick()
        signal = _make_signal()

        placed_events = []
        filled_events = []
        bus.subscribe(EventType.ORDER_PLACED, lambda p: placed_events.append(p))
        bus.subscribe(EventType.ORDER_FILLED, lambda p: filled_events.append(p))

        with patch("bot.execution.order_manager.get_session") as mock_ctx, \
             patch("MetaTrader5.symbol_info_tick", return_value=tick):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            order_mock = MagicMock()
            order_mock.id = 1
            order_repo = MagicMock()
            order_repo.create.return_value = order_mock

            trade_mock = MagicMock()
            trade_mock.id = 5
            trade_repo = MagicMock()
            trade_repo.create.return_value = trade_mock

            with patch("bot.execution.order_manager.OrderRepository", return_value=order_repo), \
                 patch("bot.execution.order_manager.TradeRepository", return_value=trade_repo):
                manager.execute(signal, Decimal("0.01"), {})

        assert len(placed_events) == 1
        assert len(filled_events) == 1
        assert placed_events[0]["symbol"] == "EURUSD"
        assert placed_events[0]["bot_mode"] == "PAPER"
        assert filled_events[0]["trade_id"] == 5

    def test_no_events_when_tick_unavailable(self):
        bus = EventBus()
        manager, _ = _make_manager(bus)

        placed_events = []
        bus.subscribe(EventType.ORDER_PLACED, lambda p: placed_events.append(p))

        with patch("MetaTrader5.symbol_info_tick", return_value=None):
            result = manager.execute(_make_signal(), Decimal("0.01"), {})

        assert result is None
        assert len(placed_events) == 0


class TestRealOrderManager:
    def test_raises_not_implemented(self):
        mgr = RealOrderManager()
        with pytest.raises(NotImplementedError):
            mgr.execute(MagicMock(), Decimal("0.01"), {})
