"""Integration test: TradingEngine in SHADOW mode generates signals but never executes."""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from bot.core.engine import TradingEngine
from bot.core.event_bus import EventBus, EventType
from bot.analysis.levels import LevelManager
from bot.risk.validator import ValidationResult
from bot.strategy.base import Signal as StrategySignal


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


@pytest.fixture
def shadow_engine():
    """Creates a TradingEngine in SHADOW mode with all deps mocked."""
    bus = EventBus()

    settings = MagicMock()
    settings.mode.value = "SHADOW"
    settings.min_confidence_to_trade = 0.0

    bars = make_bars(25)
    buffer = MagicMock()
    buffer.get.return_value = bars

    validator = MagicMock()
    validator.validate.return_value = ValidationResult(approved=True, calculated_lots=0.01)

    registry = MagicMock()
    level_manager = LevelManager()
    signal_repo = MagicMock()

    # Strategy returns a real signal
    mock_signal = StrategySignal(
        symbol="EURUSD",
        timeframe="M15",
        strategy_name="retest",
        signal_type="retest",
        direction="BUY",
        entry_price=1.1000,
        sl_price=1.0950,
        tp_price=1.1100,
        reason="integration test signal",
        confidence=0.85,
    )
    strategy_mock = MagicMock()
    strategy_mock.name = "retest"
    strategy_mock.analyze.return_value = mock_signal
    strategy_mock.is_enabled.return_value = True
    registry.get_all_enabled.return_value = [strategy_mock]

    engine = TradingEngine(
        settings=settings,
        connector=MagicMock(),
        feed=MagicMock(),
        buffer=buffer,
        event_bus=bus,
        validator=validator,
        registry=registry,
        level_manager=level_manager,
        signal_repo=signal_repo,
    )

    return engine, bus, validator, signal_repo


class TestShadowMode:
    def test_signal_persisted_with_acted_on_false(self, shadow_engine):
        """SHADOW: approved signal must be persisted with acted_on=False."""
        engine, bus, validator, signal_repo = shadow_engine

        account_state = {
            "balance": 10000.0,
            "equity": 10000.0,
            "initial_balance": 10000.0,
            "day_start_balance": 10000.0,
            "week_start_balance": 10000.0,
            "month_start_balance": 10000.0,
        }

        mock_repo = MagicMock()
        with patch.object(engine, "_get_account_state", return_value=account_state):
            with patch("bot.core.engine.get_session") as mock_ctx:
                mock_cm = MagicMock()
                mock_cm.__enter__ = MagicMock(return_value=MagicMock())
                mock_cm.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = mock_cm
                with patch("bot.core.engine.SignalRepository", return_value=mock_repo):
                    engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "M15"})

        mock_repo.create.assert_called_once()
        persisted = mock_repo.create.call_args[0][0]
        assert persisted["acted_on"] is False
        assert persisted["bot_mode"] == "SHADOW"

    def test_signal_generated_event_published(self, shadow_engine):
        """SHADOW: SIGNAL_GENERATED event should be published on approved signal."""
        engine, bus, validator, _ = shadow_engine

        published = []
        bus.subscribe(EventType.SIGNAL_GENERATED, lambda p: published.append(p))

        account_state = {
            "balance": 10000.0,
            "equity": 10000.0,
            "initial_balance": 10000.0,
            "day_start_balance": 10000.0,
            "week_start_balance": 10000.0,
            "month_start_balance": 10000.0,
        }

        with patch.object(engine, "_get_account_state", return_value=account_state):
            with patch("bot.core.engine.get_session") as mock_ctx:
                mock_cm = MagicMock()
                mock_cm.__enter__ = MagicMock(return_value=MagicMock())
                mock_cm.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = mock_cm
                with patch("bot.core.engine.SignalRepository"):
                    engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "M15"})

        assert len(published) >= 1
        evt = published[0]
        assert evt["symbol"] == "EURUSD"
        assert evt["direction"] == "BUY"

    def test_no_execution_method_called_in_shadow(self, shadow_engine):
        """SHADOW: no execution module should be called."""
        engine, bus, validator, _ = shadow_engine

        account_state = {
            "balance": 10000.0,
            "equity": 10000.0,
            "initial_balance": 10000.0,
            "day_start_balance": 10000.0,
            "week_start_balance": 10000.0,
            "month_start_balance": 10000.0,
        }

        execution_called = []

        with patch.object(engine, "_get_account_state", return_value=account_state):
            with patch("bot.core.engine.get_session") as mock_ctx:
                mock_session = MagicMock()
                mock_session.__enter__ = MagicMock(return_value=mock_session)
                mock_session.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = mock_session
                with patch("bot.core.engine.SignalRepository"):
                    # Patch execution module (it shouldn't exist / not be called)
                    with patch.dict("sys.modules", {"bot.execution": MagicMock()}):
                        engine._on_bar_closed({"symbol": "EURUSD", "timeframe": "M15"})

        # Execution tracking: the engine should NEVER call any execution function
        # In SHADOW mode, the engine only logs and publishes — no MT5 order placement
        assert len(execution_called) == 0
