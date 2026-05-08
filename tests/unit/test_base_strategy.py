"""Tests for bot/strategy/base.py"""

import pytest
import pandas as pd

from bot.strategy.base import BaseStrategy, StrategyContext, Signal
from bot.analysis.swing import SwingPoint
from bot.analysis.structure import MarketStructure
from bot.analysis.trend import TrendBias
from bot.analysis.volume import VolumeProfile


def make_ctx() -> StrategyContext:
    bars = pd.DataFrame({
        "time": [1000, 1001, 1002],
        "open": [1.1000, 1.1000, 1.1000],
        "high": [1.1010, 1.1010, 1.1010],
        "low": [1.0990, 1.0990, 1.0990],
        "close": [1.1005, 1.1005, 1.1005],
        "tick_volume": [100, 100, 100],
    })
    return StrategyContext(
        symbol="EURUSD",
        timeframe="M15",
        bars=bars,
        bars_h4=bars,
        bars_d1=bars,
        swings=[],
        structure=MarketStructure(),
        levels=[],
        trend=TrendBias("ranging", "ranging", "ranging", 1.0),
        volume=VolumeProfile(100.0, 100.0, 1.0, True, "normal"),
        current_price=1.1005,
        timestamp=1002,
    )


class MinimalStrategy(BaseStrategy):
    name = "minimal"

    def analyze(self, ctx: StrategyContext) -> Signal | None:
        return None


class TestBaseStrategy:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BaseStrategy()

    def test_minimal_subclass_works(self):
        strategy = MinimalStrategy()
        ctx = make_ctx()
        result = strategy.analyze(ctx)
        assert result is None

    def test_is_enabled_default_true(self):
        strategy = MinimalStrategy()
        assert strategy.is_enabled() is True

    def test_strategy_name_attribute(self):
        strategy = MinimalStrategy()
        assert strategy.name == "minimal"


class TestSignalDataclass:
    def test_signal_defaults(self):
        sig = Signal(
            symbol="EURUSD",
            timeframe="M15",
            strategy_name="retest",
            signal_type="retest",
            direction="BUY",
            entry_price=1.1000,
            sl_price=1.0950,
            tp_price=1.1100,
            reason="test signal",
            confidence=0.8,
        )
        assert sig.metadata == {}
        assert sig.symbol == "EURUSD"
        assert sig.direction == "BUY"
        assert sig.sl_price == 1.0950

    def test_signal_with_metadata(self):
        sig = Signal(
            symbol="GBPUSD",
            timeframe="H1",
            strategy_name="retest",
            signal_type="retest",
            direction="SELL",
            entry_price=1.2700,
            sl_price=1.2750,
            tp_price=1.2600,
            reason="bearish retest",
            confidence=0.85,
            metadata={"level_id": "abc123"},
        )
        assert sig.metadata["level_id"] == "abc123"
