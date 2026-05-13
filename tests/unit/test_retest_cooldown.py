"""Tests for per-level cooldown in RetestStrategy."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from bot.analysis.levels import Level
from bot.analysis.structure import MarketStructure
from bot.analysis.trend import TrendBias
from bot.analysis.volume import VolumeProfile
from bot.strategy.base import StrategyContext
from bot.strategy.retest import COOLDOWN_BARS_H1, COOLDOWN_BARS_M15, RetestStrategy


def _trend(h4="bullish", d1="bullish") -> TrendBias:
    conf = 1.0 if h4 == d1 else 0.5
    return TrendBias(h4_bias=h4, d1_bias=d1, overall=h4, confidence=conf)


def _volume(quality="normal") -> VolumeProfile:
    return VolumeProfile(
        avg_volume=100.0, current_volume=100.0,
        volume_ratio=1.0, is_above_average=True, quality=quality,
    )


def _valid_buy_bars(n: int = 20) -> pd.DataFrame:
    """20 bars where the last bar is a valid BUY retest of a resistance at ~1.1020."""
    return pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open":  [1.1000] * (n - 1) + [1.1015],
        "high":  [1.1010] * (n - 1) + [1.1035],
        "low":   [1.0990] * (n - 1) + [1.1018],
        "close": [1.1005] * (n - 1) + [1.1030],
        "tick_volume": [100] * n,
    })


def _resistance_level(level_id: str = "lvl-001", price: float = 1.1020, broken_at: int = 1005) -> Level:
    return Level(
        id=level_id,
        symbol="EURUSD",
        timeframe="M15",
        price=price,
        type="resistance",
        touches=[1000, 1001, broken_at],
        created_at=1000,
        last_touch_at=broken_at,
        is_broken=True,
        broken_at=broken_at,
        flip_active=True,
        confidence=0.8,
    )


def _ctx(bars: pd.DataFrame, levels: list, timeframe: str = "M15") -> StrategyContext:
    return StrategyContext(
        symbol="EURUSD",
        timeframe=timeframe,
        bars=bars,
        bars_h4=bars,
        bars_d1=bars,
        swings=[],
        structure=MarketStructure(),
        levels=levels,
        trend=_trend("bullish", "bullish"),
        volume=_volume("normal"),
        current_price=float(bars["close"].iloc[-1]),
        timestamp=int(bars["time"].iloc[-1]),
    )


class TestRetestCooldown:
    def test_second_signal_blocked_by_cooldown(self):
        """After a signal fires for a level, the same level must be skipped on the next call."""
        bars = _valid_buy_bars()
        level = _resistance_level()
        ctx = _ctx(bars, [level])
        strategy = RetestStrategy()

        sig1 = strategy.analyze(ctx)
        assert sig1 is not None, "First call must generate a signal"

        sig2 = strategy.analyze(ctx)
        assert sig2 is None, "Level in cooldown must not generate a second signal"

    def test_signal_fires_after_cooldown_expires(self):
        """After the cooldown expires, the level can generate a signal again."""
        bars = _valid_buy_bars()
        level = _resistance_level()
        ctx = _ctx(bars, [level])
        strategy = RetestStrategy()

        sig1 = strategy.analyze(ctx)
        assert sig1 is not None

        # Manually expire the cooldown by backdating it
        strategy._level_cooldowns[level.id] = datetime.now() - timedelta(seconds=1)

        sig2 = strategy.analyze(ctx)
        assert sig2 is not None, "After cooldown expires the level should fire again"

    def test_m15_cooldown_is_4_hours(self):
        """M15 cooldown must be COOLDOWN_BARS_M15 * 15 minutes = 4 hours."""
        bars = _valid_buy_bars()
        level = _resistance_level()
        ctx = _ctx(bars, [level], timeframe="M15")
        strategy = RetestStrategy()

        before = datetime.now()
        sig = strategy.analyze(ctx)

        assert sig is not None
        cooldown_until = strategy._level_cooldowns[level.id]
        expected_seconds = COOLDOWN_BARS_M15 * 15 * 60  # 4 h = 14400 s
        assert abs((cooldown_until - before).total_seconds() - expected_seconds) < 5

    def test_h1_cooldown_is_8_hours(self):
        """H1 cooldown must be COOLDOWN_BARS_H1 hours = 8 hours."""
        bars = _valid_buy_bars()
        level = _resistance_level()
        ctx = _ctx(bars, [level], timeframe="H1")
        strategy = RetestStrategy()

        before = datetime.now()
        sig = strategy.analyze(ctx)

        assert sig is not None
        cooldown_until = strategy._level_cooldowns[level.id]
        expected_seconds = COOLDOWN_BARS_H1 * 3600  # 8 h = 28800 s
        assert abs((cooldown_until - before).total_seconds() - expected_seconds) < 5
