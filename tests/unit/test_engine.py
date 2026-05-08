"""Tests for bot/core/engine.py"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, call

from bot.core.engine import TradingEngine
from bot.core.event_bus import EventBus, EventType
from bot.analysis.levels import LevelManager
from bot.strategy.base import Signal as StrategySignal
from bot.risk.validator import ValidationResult


def make_bars(n: int = 25, base: float = 1.1000) -> pd.DataFrame:
    return pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open": [base] * n,
        "high": [base + 0.0005] * n,
        "low": [base - 0.0005] * n,
        "close": [base] * n,
        "tick_volume": [100] * n,
        "spread": [1] * n,
        "real_volume": [0] * n,
    })


def make_engine(event_bus: EventBus | None = None) -> tuple[TradingEngine, dict]:
    """Creates a TradingEngine with all dependencies mocked."""
    if event_bus is None:
        event_bus = EventBus()

    settings = MagicMock()
    settings.mode.value = "SHADOW"

    connector = MagicMock()
    feed = MagicMock()
    buffer = MagicMock()
    validator = MagicMock()
    registry = MagicMock()
    level_manager = LevelManager()
    signal_repo = MagicMock()

    # Default buffer returns bars for M15/H1
    bars = make_bars(25)
    buffer.get.return_value = bars

    engine = TradingEngine(
        settings=settings,
        connector=connector,
        feed=feed,
        buffer=buffer,
        event_bus=event_bus,
        validator=validator,
        registry=registry,
        level_manager=level_manager,
        signal_repo=signal_repo,
    )

    mocks = {
        "settings": settings,
        "connector": connector,
        "feed": feed,
        "buffer": buffer,
        "validator": validator,
        "registry": registry,
        "signal_repo": signal_repo,
        "event_bus": event_bus,
    }
    return engine, mocks


class TestOnBarClosed:
    def test_ignores_non_m15_h1_timeframes(self):
        engine, mocks = make_engine()

        # Trigger BAR_CLOSED for M1 (should be ignored)
        engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "M1"})
        engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "H4"})
        engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "D1"})

        # Registry should not be consulted
        mocks["registry"].get_all_enabled.assert_not_called()

    def test_processes_m15_timeframe(self):
        engine, mocks = make_engine()

        # Strategy returns None (no signal)
        strategy_mock = MagicMock()
        strategy_mock.analyze.return_value = None
        mocks["registry"].get_all_enabled.return_value = [strategy_mock]

        engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "M15"})

        strategy_mock.analyze.assert_called_once()

    def test_processes_h1_timeframe(self):
        engine, mocks = make_engine()
        strategy_mock = MagicMock()
        strategy_mock.analyze.return_value = None
        mocks["registry"].get_all_enabled.return_value = [strategy_mock]

        engine._on_bar_closed({"symbol": "GBPUSD", "timeframe": "H1"})

        strategy_mock.analyze.assert_called_once()

    def test_insufficient_bars_skips_strategies(self):
        """Buffer with < 20 bars → strategies not called."""
        engine, mocks = make_engine()
        mocks["buffer"].get.return_value = make_bars(5)  # too few

        strategy_mock = MagicMock()
        mocks["registry"].get_all_enabled.return_value = [strategy_mock]

        engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "M15"})

        strategy_mock.analyze.assert_not_called()


class TestRunStrategy:
    def test_approved_signal_persisted_and_published(self):
        event_bus = EventBus()
        engine, mocks = make_engine(event_bus)

        published_events = []
        event_bus.subscribe(EventType.SIGNAL_GENERATED, lambda p: published_events.append(p))

        mock_signal = StrategySignal(
            symbol="EURUSD",
            timeframe="M15",
            strategy_name="retest",
            signal_type="retest",
            direction="BUY",
            entry_price=1.1000,
            sl_price=1.0950,
            tp_price=1.1100,
            reason="test",
            confidence=0.8,
        )

        account_state = {
            "balance": 10000.0,
            "equity": 10000.0,
            "initial_balance": 10000.0,
            "day_start_balance": 10000.0,
            "week_start_balance": 10000.0,
            "month_start_balance": 10000.0,
        }

        approved_result = ValidationResult(approved=True, calculated_lots=0.01)
        mocks["validator"].validate.return_value = approved_result

        strategy_mock = MagicMock()
        strategy_mock.name = "retest"
        strategy_mock.analyze.return_value = mock_signal

        mock_repo = MagicMock()

        with patch.object(engine, "_get_account_state", return_value=account_state):
            with patch("bot.core.engine.get_session") as mock_session_ctx:
                mock_cm = MagicMock()
                mock_cm.__enter__ = MagicMock(return_value=MagicMock())
                mock_cm.__exit__ = MagicMock(return_value=False)
                mock_session_ctx.return_value = mock_cm
                with patch("bot.core.engine.SignalRepository", return_value=mock_repo):
                    engine._run_strategy(strategy_mock, MagicMock())

        # Signal should have been persisted
        mock_repo.create.assert_called_once()
        call_args = mock_repo.create.call_args[0][0]
        assert call_args["acted_on"] is False
        assert call_args["symbol"] == "EURUSD"

        # SIGNAL_GENERATED should have been published
        assert len(published_events) >= 1

    def test_rejected_signal_persisted_without_publishing(self):
        event_bus = EventBus()
        engine, mocks = make_engine(event_bus)

        published_events = []
        event_bus.subscribe(EventType.SIGNAL_GENERATED, lambda p: published_events.append(p))

        mock_signal = StrategySignal(
            symbol="EURUSD",
            timeframe="M15",
            strategy_name="retest",
            signal_type="retest",
            direction="BUY",
            entry_price=1.1000,
            sl_price=1.0950,
            tp_price=1.1100,
            reason="test",
            confidence=0.8,
        )

        account_state = {
            "balance": 10000.0,
            "equity": 10000.0,
            "initial_balance": 10000.0,
            "day_start_balance": 10000.0,
            "week_start_balance": 10000.0,
            "month_start_balance": 10000.0,
        }

        rejected_result = ValidationResult(
            approved=False,
            rejection_reason="Daily drawdown limit hit",
            rejection_severity="ERROR",
        )
        mocks["validator"].validate.return_value = rejected_result

        strategy_mock = MagicMock()
        strategy_mock.name = "retest"
        strategy_mock.analyze.return_value = mock_signal

        with patch.object(engine, "_get_account_state", return_value=account_state):
            with patch("bot.core.engine.get_session") as mock_session_ctx:
                mock_session = MagicMock()
                mock_session.__enter__ = MagicMock(return_value=mock_session)
                mock_session.__exit__ = MagicMock(return_value=False)
                mock_session_ctx.return_value = mock_session

                mock_repo = MagicMock()
                with patch("bot.core.engine.SignalRepository", return_value=mock_repo):
                    engine._run_strategy(strategy_mock, MagicMock())

        # Signal should still be persisted (with rejection_reason)
        mock_repo.create.assert_called_once()
        call_args = mock_repo.create.call_args[0][0]
        assert call_args["rejection_reason"] == "Daily drawdown limit hit"

        # SIGNAL_GENERATED should NOT have been published
        assert len(published_events) == 0

    def test_strategy_exception_does_not_block_others(self):
        """If one strategy raises an exception, others are still called."""
        engine, mocks = make_engine()

        bad_strategy = MagicMock()
        bad_strategy.name = "bad"
        bad_strategy.analyze.side_effect = RuntimeError("strategy crashed")

        good_strategy = MagicMock()
        good_strategy.name = "good"
        good_strategy.analyze.return_value = None

        mocks["registry"].get_all_enabled.return_value = [bad_strategy, good_strategy]

        engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "M15"})

        good_strategy.analyze.assert_called_once()

    def test_no_signal_when_account_unavailable(self):
        """If _get_account_state returns None, no DB write or publish."""
        event_bus = EventBus()
        engine, mocks = make_engine(event_bus)

        mock_signal = StrategySignal(
            symbol="EURUSD",
            timeframe="M15",
            strategy_name="retest",
            signal_type="retest",
            direction="BUY",
            entry_price=1.1000,
            sl_price=1.0950,
            tp_price=1.1100,
            reason="test",
            confidence=0.8,
        )

        strategy_mock = MagicMock()
        strategy_mock.analyze.return_value = mock_signal

        with patch.object(engine, "_get_account_state", return_value=None):
            with patch("bot.core.engine.get_session") as mock_session_ctx:
                mock_repo = MagicMock()
                engine._run_strategy(strategy_mock, MagicMock())

        # Validator should not be called
        mocks["validator"].validate.assert_not_called()
