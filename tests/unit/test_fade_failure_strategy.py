"""Tests for bot/strategy/fade_failure.py"""

from __future__ import annotations

import pytest
import pandas as pd

from bot.analysis.levels import Level
from bot.analysis.structure import MarketStructure
from bot.analysis.trend import TrendBias
from bot.analysis.volume import VolumeProfile
from bot.strategy.base import StrategyContext
from bot.strategy.fade_failure import FadeFailureStrategy


# ── helpers ──────────────────────────────────────────────────────────────────

def make_trend(h4: str = "bearish", d1: str = "bearish") -> TrendBias:
    if h4 == d1:
        overall, conf = h4, 1.0
    else:
        overall, conf = f"h4_{h4}_d1_{d1}", 0.5
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


def make_unbroken_level(
    price: float,
    level_type: str = "resistance",
    last_touch_at: int = 1015,
    touches: int = 3,
    level_id: str = "level-001",
) -> Level:
    touch_list = list(range(1000, 1000 + touches))
    return Level(
        id=level_id,
        symbol="EURUSD",
        timeframe="M15",
        price=price,
        type=level_type,
        touches=touch_list,
        created_at=1000,
        last_touch_at=last_touch_at,
        is_broken=False,
        broken_at=None,
        flip_active=False,
        confidence=0.6,
    )


def make_bars(
    n: int = 20,
    last_open: float = 1.1010,
    last_high: float = 1.1040,
    last_low: float = 1.0998,
    last_close: float = 1.1000,
    last_time: int = 1019,
) -> pd.DataFrame:
    times = list(range(1000, 1000 + n - 1)) + [last_time]
    data = {
        "time": times,
        "open": [1.1000] * (n - 1) + [last_open],
        "high": [1.1010] * (n - 1) + [last_high],
        "low": [1.0990] * (n - 1) + [last_low],
        "close": [1.1005] * (n - 1) + [last_close],
        "tick_volume": [100] * n,
    }
    return pd.DataFrame(data)


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
        trend=trend or make_trend(),
        volume=volume or make_volume(),
        current_price=float(bars["close"].iloc[-1]),
        timestamp=int(bars["time"].iloc[-1]),
    )


# ── SELL fade (resistance false breakout) ─────────────────────────────────────

class TestFadeSell:
    """Wick pierced above resistance, close came back below → SELL."""

    def _resistance_fade_bars(self) -> pd.DataFrame:
        # open=1.1015, high=1.1040, low=1.0998, close=1.1000
        # level_price = 1.1020
        # high (1.1040) > 1.1020 + probe (0.0002) = 1.1022 ✓
        # close (1.1000) < 1.1020 ✓
        # upper_wick = 1.1040 - max(1.1015, 1.1000) = 1.1040 - 1.1015 = 0.0025
        # range = 1.1040 - 1.0998 = 0.0042
        # wick_pct = 0.0025/0.0042 = 0.595 >= 0.30 ✓
        # body = |1.1015-1.1000| = 0.0015, body_pct = 0.0015/0.0042 = 0.357 >= 0.35 ✓
        return make_bars(
            last_open=1.1015, last_high=1.1040, last_low=1.0998, last_close=1.1000
        )

    def test_resistance_false_breakout_generates_sell(self):
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = self._resistance_fade_bars()
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bearish", "bearish"))

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.direction == "SELL"
        assert signal.strategy_name == "fade_failure"
        assert signal.signal_type == "fade"

    def test_sl_above_wick_high(self):
        """SL must be above the wick high (bar_high + buffer)."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = self._resistance_fade_bars()
        ctx = make_ctx(bars, levels=[level])

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.sl_price > 1.1040  # above bar_high

    def test_tp_below_entry(self):
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = self._resistance_fade_bars()
        ctx = make_ctx(bars, levels=[level])

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.tp_price < signal.entry_price

    def test_rr_at_least_two(self):
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = self._resistance_fade_bars()
        ctx = make_ctx(bars, levels=[level])

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        risk = signal.sl_price - signal.entry_price
        reward = signal.entry_price - signal.tp_price
        assert abs(reward / risk - 2.0) < 1e-9

    def test_no_signal_if_close_above_resistance(self):
        """Close stayed above resistance → actual breakout, not a fade."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        # close=1.1030 > level_price=1.1020 → not a fade
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.0998, last_close=1.1030)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_no_signal_if_wick_too_small(self):
        """Upper wick < WICK_MIN_PCT → not enough rejection."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        # high barely above level: bar_high=1.1022, range=0.0024, upper_wick≈0.0007 → 29%
        # open=1.1015, high=1.1022, low=1.0998, close=1.1000
        # upper_wick = 1.1022 - 1.1015 = 0.0007; range = 0.0024; pct = 29% < 30%
        bars = make_bars(last_open=1.1015, last_high=1.1022, last_low=1.0998, last_close=1.1000)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_no_signal_if_bar_high_not_above_level(self):
        """Bar never pierced above resistance → no fade setup."""
        level = make_unbroken_level(price=1.1050, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.0998, last_close=1.1000)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_no_signal_if_doji_body(self):
        """Body < BODY_MIN_PCT → no signal."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        # Doji: open≈close both below level, but big wick above
        # open=1.1010, close=1.1011, high=1.1040, low=1.0998
        # body = 0.0001, range = 0.0042, body_pct = 2.4% < 35%
        bars = make_bars(last_open=1.1010, last_high=1.1040, last_low=1.0998, last_close=1.1011)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_no_signal_if_insufficient_touches(self):
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015, touches=1)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.0998, last_close=1.1000)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_no_signal_if_level_already_broken(self):
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        level.is_broken = True
        level.broken_at = 1010
        level.flip_active = True
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.0998, last_close=1.1000)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_confidence_base_no_extras(self):
        """Ranging trend + low volume → base confidence 0.60."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = self._resistance_fade_bars()
        ctx = make_ctx(
            bars, levels=[level],
            trend=make_trend("ranging", "ranging"),
            volume=make_volume("low"),
        )

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.confidence == pytest.approx(0.60)

    def test_confidence_boosted_by_trend_and_volume(self):
        """Bearish trend + normal volume → confidence >= 0.70."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = self._resistance_fade_bars()
        ctx = make_ctx(
            bars, levels=[level],
            trend=make_trend("bearish", "bearish"),
            volume=make_volume("normal"),
        )

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.confidence >= 0.70


# ── BUY fade (support false breakdown) ───────────────────────────────────────

class TestFadeBuy:
    """Wick pierced below support, close came back above → BUY."""

    def _support_fade_bars(self) -> pd.DataFrame:
        # open=1.1025, high=1.1046, low=1.0995, close=1.1044
        # level_price = 1.1020
        # low (1.0995) < 1.1020 - probe (0.0002) = 1.1018 ✓
        # close (1.1044) > 1.1020 ✓
        # lower_wick = min(1.1025, 1.1044) - 1.0995 = 1.1025 - 1.0995 = 0.0030
        # range = 1.1046 - 1.0995 = 0.0051
        # wick_pct = 0.0030/0.0051 = 0.588 >= 0.30 ✓
        # body = 1.1044-1.1025 = 0.0019, body_pct = 0.0019/0.0051 = 0.373 >= 0.35 ✓
        return make_bars(
            last_open=1.1025, last_high=1.1046, last_low=1.0995, last_close=1.1044
        )

    def test_support_false_breakdown_generates_buy(self):
        level = make_unbroken_level(price=1.1020, level_type="support", last_touch_at=1015)
        bars = self._support_fade_bars()
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bullish", "bullish"))

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.direction == "BUY"
        assert signal.signal_type == "fade"

    def test_sl_below_wick_low(self):
        """SL must be below the wick low (bar_low - buffer)."""
        level = make_unbroken_level(price=1.1020, level_type="support", last_touch_at=1015)
        bars = self._support_fade_bars()
        ctx = make_ctx(bars, levels=[level])

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.sl_price < 1.0995  # below bar_low

    def test_tp_above_entry(self):
        level = make_unbroken_level(price=1.1020, level_type="support", last_touch_at=1015)
        bars = self._support_fade_bars()
        ctx = make_ctx(bars, levels=[level])

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.tp_price > signal.entry_price

    def test_no_signal_if_close_below_support(self):
        """Close stayed below support → actual breakdown, not a fade."""
        level = make_unbroken_level(price=1.1020, level_type="support", last_touch_at=1015)
        bars = make_bars(last_open=1.1025, last_high=1.1046, last_low=1.0995, last_close=1.1010)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_no_signal_if_lower_wick_too_small(self):
        """Lower wick < WICK_MIN_PCT → no fade setup."""
        level = make_unbroken_level(price=1.1020, level_type="support", last_touch_at=1015)
        # low barely below support: low=1.1017, range small, wick tiny
        # open=1.1025, high=1.1046, low=1.1017, close=1.1044
        # lower_wick = 1.1025 - 1.1017 = 0.0008; range = 0.0029; pct = 27.6% < 30%
        bars = make_bars(last_open=1.1025, last_high=1.1046, last_low=1.1017, last_close=1.1044)
        ctx = make_ctx(bars, levels=[level])

        assert FadeFailureStrategy().analyze(ctx) is None

    def test_no_signal_when_no_levels(self):
        bars = make_bars()
        ctx = make_ctx(bars, levels=[])

        assert FadeFailureStrategy().analyze(ctx) is None


# ── Cooldown behaviour ────────────────────────────────────────────────────────

class TestFadeCooldown:
    def test_cooldown_prevents_repeat_on_same_level(self):
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        # Resistance fade bars
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.0998, last_close=1.1000)
        ctx = make_ctx(bars, levels=[level])

        strategy = FadeFailureStrategy()
        first = strategy.analyze(ctx)
        assert first is not None

        second = strategy.analyze(ctx)
        assert second is None

    def test_highest_confidence_returned_for_multiple_levels(self):
        level1 = make_unbroken_level(
            price=1.1020, level_type="resistance", last_touch_at=1015, level_id="lv-1"
        )
        level2 = make_unbroken_level(
            price=1.1022, level_type="resistance", last_touch_at=1015, level_id="lv-2"
        )
        # Both levels have their wick pierced and close below both
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.0998, last_close=1.1000)
        ctx = make_ctx(
            bars, levels=[level1, level2],
            trend=make_trend("bearish", "bearish"),
            volume=make_volume("high"),
        )

        signal = FadeFailureStrategy().analyze(ctx)

        assert signal is not None
        assert signal.direction == "SELL"
