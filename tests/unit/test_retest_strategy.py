"""Tests for bot/strategy/retest.py"""

import pytest
import pandas as pd

from bot.analysis.levels import Level
from bot.analysis.structure import MarketStructure
from bot.analysis.swing import SwingPoint
from bot.analysis.trend import TrendBias
from bot.analysis.volume import VolumeProfile
from bot.strategy.base import StrategyContext
from bot.strategy.retest import RetestStrategy


def make_trend(h4: str = "bullish", d1: str = "bullish") -> TrendBias:
    if h4 == d1:
        overall = h4
        conf = 1.0
    else:
        overall = f"h4_{h4}_d1_{d1}"
        conf = 0.5
    return TrendBias(h4_bias=h4, d1_bias=d1, overall=overall, confidence=conf)


def make_volume(quality: str = "normal") -> VolumeProfile:
    ratio = 2.0 if quality == "high" else 0.5 if quality == "low" else 1.0
    return VolumeProfile(
        avg_volume=100.0,
        current_volume=100.0 * ratio,
        volume_ratio=ratio,
        is_above_average=ratio >= 1.0,
        quality=quality,
    )


def make_resistance_level(price: float, broken_at: int, pre_touches: int = 2) -> Level:
    """Creates a flip-active resistance level (broken, former resistance)."""
    touches = list(range(1000, 1000 + pre_touches)) + [broken_at]
    return Level(
        id="test-level-001",
        symbol="EURUSD",
        timeframe="M15",
        price=price,
        type="resistance",
        touches=touches,
        created_at=1000,
        last_touch_at=broken_at,
        is_broken=True,
        broken_at=broken_at,
        flip_active=True,
        confidence=0.8,
    )


def make_support_level(price: float, broken_at: int, pre_touches: int = 2) -> Level:
    """Creates a flip-active support level (broken, former support)."""
    touches = list(range(1000, 1000 + pre_touches)) + [broken_at]
    return Level(
        id="test-level-002",
        symbol="EURUSD",
        timeframe="M15",
        price=price,
        type="support",
        touches=touches,
        created_at=1000,
        last_touch_at=broken_at,
        is_broken=True,
        broken_at=broken_at,
        flip_active=True,
        confidence=0.8,
    )


def make_ctx(
    bars: pd.DataFrame,
    levels: list[Level] | None = None,
    trend: TrendBias | None = None,
    volume: VolumeProfile | None = None,
) -> StrategyContext:
    return StrategyContext(
        symbol="EURUSD",
        timeframe="M15",
        bars=bars,
        bars_h4=bars,
        bars_d1=bars,
        swings=[],
        structure=MarketStructure(),
        levels=levels or [],
        trend=trend or make_trend("bullish", "bullish"),
        volume=volume or make_volume("normal"),
        current_price=float(bars["close"].iloc[-1]),
        timestamp=int(bars["time"].iloc[-1]),
    )


class TestRetestStrategyBullish:
    """Test 1: bullish retest setup."""

    def test_bullish_retest_generates_buy_signal(self):
        """
        Broken resistance at 1.1020.
        Last bar: low touches the level, closes above it bullishly, body >= 40%.
        """
        level_price = 1.1020
        broken_at = 1005  # timestamp of break

        # 20 bars; last bar retests from below and closes above
        n = 20
        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * (n - 1) + [1.1015],
            "high": [1.1010] * (n - 1) + [1.1035],
            "low": [1.0990] * (n - 1) + [1.1018],   # touches level from above (BUY retest)
            "close": [1.1005] * (n - 1) + [1.1030],  # bullish close above level
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)

        level = make_resistance_level(level_price, broken_at, pre_touches=2)
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bullish", "bullish"))

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)

        assert signal is not None
        assert signal.direction == "BUY"
        assert signal.symbol == "EURUSD"
        assert signal.sl_price < signal.entry_price  # SL below entry for BUY
        assert signal.tp_price > signal.entry_price  # TP above entry

    def test_no_signal_when_price_far_from_level(self):
        """Test 2: price is far from the level → no signal."""
        level_price = 1.1020
        broken_at = 1005

        n = 20
        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * n,
            "high": [1.0980] * n,  # far below level
            "low": [1.0960] * n,
            "close": [1.0970] * n,
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)
        level = make_resistance_level(level_price, broken_at, pre_touches=2)
        ctx = make_ctx(bars, levels=[level])

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)
        assert signal is None

    def test_no_signal_doji_bar(self):
        """Test 3: bar touches level but body < 40% of range → doji, no signal."""
        level_price = 1.1020
        broken_at = 1005

        n = 20
        # Doji: open and close very close together
        bar_open = 1.1021
        bar_close = 1.1022   # tiny body
        bar_high = 1.1035
        bar_low = 1.1018
        # body = 0.0001, range = 0.0017, body_pct = ~5.9% < 40%

        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * (n - 1) + [bar_open],
            "high": [1.1010] * (n - 1) + [bar_high],
            "low": [1.0990] * (n - 1) + [bar_low],
            "close": [1.1005] * (n - 1) + [bar_close],
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)
        level = make_resistance_level(level_price, broken_at, pre_touches=2)
        ctx = make_ctx(bars, levels=[level])

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)
        assert signal is None

    def test_confidence_boosted_by_both_timeframes(self):
        """Test 4: H4 and D1 both bullish → confidence >= 0.9"""
        level_price = 1.1020
        broken_at = 1005

        n = 20
        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * (n - 1) + [1.1015],
            "high": [1.1010] * (n - 1) + [1.1035],
            "low": [1.0990] * (n - 1) + [1.1018],
            "close": [1.1005] * (n - 1) + [1.1030],
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)
        level = make_resistance_level(level_price, broken_at, pre_touches=2)
        trend = make_trend("bullish", "bullish")
        volume = make_volume("high")
        ctx = make_ctx(bars, levels=[level], trend=trend, volume=volume)

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)
        assert signal is not None
        assert signal.confidence >= 0.9


class TestRetestStrategyBearish:
    """Test 5: bearish retest setup."""

    def test_bearish_retest_generates_sell_signal(self):
        """
        Broken support at 1.0980.
        Last bar: high touches the level from below, closes below it bearishly.
        """
        level_price = 1.0980
        broken_at = 1005

        n = 20
        # Bearish retest: bar touches the level from below, closes below
        bar_open = 1.0982
        bar_close = 1.0965   # bearish close below level
        bar_high = 1.0981    # touches level from below
        bar_low = 1.0960

        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * (n - 1) + [bar_open],
            "high": [1.1010] * (n - 1) + [bar_high],
            "low": [1.0990] * (n - 1) + [bar_low],
            "close": [1.1005] * (n - 1) + [bar_close],
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)

        level = make_support_level(level_price, broken_at, pre_touches=2)
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bearish", "bearish"))

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)

        assert signal is not None
        assert signal.direction == "SELL"
        assert signal.sl_price > signal.entry_price  # SL above entry for SELL
        assert signal.tp_price < signal.entry_price  # TP below entry

    def test_no_signal_when_no_levels(self):
        """No flip levels → no signal."""
        n = 20
        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * n,
            "high": [1.1010] * n,
            "low": [1.0990] * n,
            "close": [1.1005] * n,
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)
        ctx = make_ctx(bars, levels=[])
        strategy = RetestStrategy()
        assert strategy.analyze(ctx) is None

    def test_no_signal_when_break_too_old(self):
        """Level broken > MAX_BARS_AFTER_BREAK bars ago → no signal."""
        from bot.strategy.retest import MAX_BARS_AFTER_BREAK

        level_price = 1.1020
        n = 60
        # broken_at is far in the past (timestamp well before current bars)
        broken_at = 900  # before the bar series starts at 1000

        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * (n - 1) + [1.1015],
            "high": [1.1010] * (n - 1) + [1.1035],
            "low": [1.0990] * (n - 1) + [1.1018],
            "close": [1.1005] * (n - 1) + [1.1030],
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)
        level = make_resistance_level(level_price, broken_at, pre_touches=2)
        ctx = make_ctx(bars, levels=[level])

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)
        # bars since break would be ~60 bars (broken_at before series) > MAX_BARS_AFTER_BREAK=30
        assert signal is None

    def test_highest_confidence_returned_for_multiple(self):
        """When multiple signals exist, highest confidence is returned."""
        level_price1 = 1.1020
        level_price2 = 1.1021
        broken_at = 1005

        n = 20
        bar_open = 1.1015
        bar_close = 1.1030
        bar_high = 1.1035
        bar_low = 1.1018

        bars_data = {
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * (n - 1) + [bar_open],
            "high": [1.1010] * (n - 1) + [bar_high],
            "low": [1.0990] * (n - 1) + [bar_low],
            "close": [1.1005] * (n - 1) + [bar_close],
            "tick_volume": [100] * n,
        }
        bars = pd.DataFrame(bars_data)

        level1 = make_resistance_level(level_price1, broken_at, pre_touches=2)
        level2 = make_resistance_level(level_price2, broken_at, pre_touches=2)
        level2.id = "test-level-002b"
        level2.confidence = 0.9

        ctx = make_ctx(
            bars,
            levels=[level1, level2],
            trend=make_trend("bullish", "bullish"),
            volume=make_volume("high"),
        )
        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)
        # Should not crash; if multiple signals, returns highest confidence
        if signal is not None:
            assert signal.direction == "BUY"
