"""Integration tests: Signal → validate → VirtualOrderManager → Trade in DB"""

from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from bot.core.event_bus import EventBus, EventType
from bot.core.engine import TradingEngine
from bot.analysis.levels import LevelManager
from bot.strategy.base import Signal as StrategySignal
from bot.risk.validator import ValidationResult
from bot.execution.order_manager import VirtualOrderManager


def _make_signal(direction="BUY"):
    return StrategySignal(
        symbol="EURUSD",
        timeframe="M15",
        strategy_name="retest",
        signal_type="retest",
        direction=direction,
        entry_price=1.1000,
        sl_price=1.0950,
        tp_price=1.1100,
        reason="integration test",
        confidence=0.8,
    )


def _make_engine(event_bus, mode="PAPER", order_manager=None):
    settings = MagicMock()
    settings.mode.value = mode
    settings.min_confidence_to_trade = 0.0

    engine = TradingEngine(
        settings=settings,
        connector=MagicMock(),
        feed=MagicMock(),
        buffer=MagicMock(),
        event_bus=event_bus,
        validator=MagicMock(),
        registry=MagicMock(),
        level_manager=LevelManager(),
        signal_repo=None,
        order_manager=order_manager,
    )
    return engine


class TestPaperModeCallsOrderManager:
    def test_paper_mode_executes_approved_signal(self):
        """Engine in PAPER mode calls order_manager.execute() when signal is approved."""
        bus = EventBus()
        order_manager = MagicMock()
        order_manager.execute.return_value = MagicMock(id=42)

        engine = _make_engine(bus, mode="PAPER", order_manager=order_manager)

        signal = _make_signal()
        account_state = {"balance": 10000.0, "equity": 10000.0}
        approved_result = ValidationResult(approved=True, calculated_lots=Decimal("0.01"))
        engine._validator.validate.return_value = approved_result

        with patch.object(engine, "_get_account_state", return_value=account_state), \
             patch("bot.core.engine.get_session") as mock_ctx:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo_mock = MagicMock()
            repo_mock.create.return_value = MagicMock(id=1)
            with patch("bot.core.engine.SignalRepository", return_value=repo_mock):
                strategy_mock = MagicMock()
                strategy_mock.name = "retest"
                strategy_mock.analyze.return_value.confidence = 0.9  # numérico para el filtro
                engine._run_strategy(strategy_mock, MagicMock())

        # order_manager.execute not triggered because strategy returned None from mock
        # We need to call _run_strategy with a real signal directly
        order_manager.reset_mock()

        with patch.object(engine, "_get_account_state", return_value=account_state), \
             patch("bot.core.engine.get_session") as mock_ctx:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo_mock = MagicMock()
            repo_mock.create.return_value = MagicMock(id=1)
            repo_mock.mark_acted_on = MagicMock()
            with patch("bot.core.engine.SignalRepository", return_value=repo_mock):

                class FakeStrategy:
                    name = "retest"
                    def analyze(self, ctx):
                        return signal

                engine._run_strategy(FakeStrategy(), MagicMock())

        order_manager.execute.assert_called_once()
        call_args = order_manager.execute.call_args
        assert call_args[0][0] is signal
        assert call_args[0][1] == Decimal("0.01")

    def test_shadow_mode_does_not_call_order_manager(self):
        """Engine in SHADOW mode must NOT call order_manager even if signal is approved."""
        bus = EventBus()
        order_manager = MagicMock()

        engine = _make_engine(bus, mode="SHADOW", order_manager=order_manager)

        signal = _make_signal()
        account_state = {"balance": 10000.0, "equity": 10000.0}
        approved_result = ValidationResult(approved=True, calculated_lots=Decimal("0.01"))
        engine._validator.validate.return_value = approved_result

        with patch.object(engine, "_get_account_state", return_value=account_state), \
             patch("bot.core.engine.get_session") as mock_ctx:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo_mock = MagicMock()
            repo_mock.create.return_value = MagicMock(id=1)
            with patch("bot.core.engine.SignalRepository", return_value=repo_mock):

                class FakeStrategy:
                    name = "retest"
                    def analyze(self, ctx):
                        return signal

                engine._run_strategy(FakeStrategy(), MagicMock())

        order_manager.execute.assert_not_called()

    def test_rejected_signal_does_not_call_order_manager(self):
        """Rejected signals must never reach order_manager."""
        bus = EventBus()
        order_manager = MagicMock()

        engine = _make_engine(bus, mode="PAPER", order_manager=order_manager)

        signal = _make_signal()
        account_state = {"balance": 10000.0, "equity": 10000.0}
        rejected_result = ValidationResult(
            approved=False,
            rejection_reason="daily drawdown limit",
            rejection_severity="ERROR",
        )
        engine._validator.validate.return_value = rejected_result

        with patch.object(engine, "_get_account_state", return_value=account_state), \
             patch("bot.core.engine.get_session") as mock_ctx:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo_mock = MagicMock()
            repo_mock.create.return_value = MagicMock(id=1)
            with patch("bot.core.engine.SignalRepository", return_value=repo_mock):

                class FakeStrategy:
                    name = "retest"
                    def analyze(self, ctx):
                        return signal

                engine._run_strategy(FakeStrategy(), MagicMock())

        order_manager.execute.assert_not_called()

    def test_signal_persisted_as_acted_on_after_paper_execution(self):
        """After successful PAPER execution, the signal should be marked acted_on."""
        bus = EventBus()
        order_manager = MagicMock()
        order_manager.execute.return_value = MagicMock(id=99)

        engine = _make_engine(bus, mode="PAPER", order_manager=order_manager)

        signal = _make_signal()
        account_state = {"balance": 10000.0, "equity": 10000.0}
        approved_result = ValidationResult(approved=True, calculated_lots=Decimal("0.01"))
        engine._validator.validate.return_value = approved_result

        mark_acted_on_calls = []

        with patch.object(engine, "_get_account_state", return_value=account_state), \
             patch.object(engine, "_mark_signal_acted_on", side_effect=lambda sid: mark_acted_on_calls.append(sid)), \
             patch("bot.core.engine.get_session") as mock_ctx:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo_mock = MagicMock()
            repo_mock.create.return_value = MagicMock(id=7)
            with patch("bot.core.engine.SignalRepository", return_value=repo_mock):

                class FakeStrategy:
                    name = "retest"
                    def analyze(self, ctx):
                        return signal

                engine._run_strategy(FakeStrategy(), MagicMock())

        assert len(mark_acted_on_calls) == 1


class TestVirtualOrderManagerSignalToPipeline:
    def test_execute_creates_order_and_trade_with_correct_bot_mode(self):
        """VirtualOrderManager always tags created records with bot_mode='PAPER'."""
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

        tick = MagicMock()
        tick.ask = 1.10020
        tick.bid = 1.10000
        signal = _make_signal()

        with patch("bot.execution.order_manager.get_session") as mock_ctx, \
             patch("MetaTrader5.symbol_info_tick", return_value=tick):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            order_mock = MagicMock()
            order_mock.id = 5
            order_repo_mock = MagicMock()
            order_repo_mock.create.return_value = order_mock

            trade_mock = MagicMock()
            trade_mock.id = 10
            trade_repo_mock = MagicMock()
            trade_repo_mock.create.return_value = trade_mock

            with patch("bot.execution.order_manager.OrderRepository", return_value=order_repo_mock), \
                 patch("bot.execution.order_manager.TradeRepository", return_value=trade_repo_mock):
                result = manager.execute(signal, Decimal("0.01"), {})

        assert result is trade_mock

        order_data = order_repo_mock.create.call_args[0][0]
        trade_data = trade_repo_mock.create.call_args[0][0]
        assert order_data["bot_mode"] == "PAPER"
        assert trade_data["bot_mode"] == "PAPER"
        assert trade_data["sl_price"] == signal.sl_price
