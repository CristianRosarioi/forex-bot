"""Tests for bot/strategy/breakout.py"""

from __future__ import annotations

import pytest
import pandas as pd

from bot.analysis.levels import Level
from bot.analysis.structure import MarketStructure
from bot.analysis.swing import SwingPoint
from bot.analysis.trend import TrendBias
from bot.analysis.volume import VolumeProfile
from bot.strategy.base import StrategyContext
from bot.strategy.breakout import BreakoutStrategy, MIN_TOUCHES_BEFORE_BREAK


# ── helpers ──────────────────────────────────────────────────────────────────

def make_trend(h4: str = "bullish", d1: str = "bullish") -> TrendBias:
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
    last_open: float = 1.1015,
    last_high: float = 1.1040,
    last_low: float = 1.1010,
    last_close: float = 1.1035,
    last_time: int = 1019,
) -> pd.DataFrame:
    """Build an n-bar DataFrame; last bar has the supplied OHLC."""
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


# ── BUY (resistance breakout) ─────────────────────────────────────────────────

class TestBreakoutBuy:
    def test_resistance_breakout_generates_buy_signal(self):
        """Bullish candle closes above resistance with enough body → BUY."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        # close=1.1035 > 1.1020+buffer; bullish; body = 0.0020/0.0030 = 0.67 >= 0.50
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        assert signal.direction == "BUY"
        assert signal.strategy_name == "breakout"
        assert signal.signal_type == "breakout"
        assert signal.sl_price < signal.entry_price
        assert signal.tp_price > signal.entry_price

    def test_sl_is_below_broken_level(self):
        """SL must be placed just below the broken resistance level."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        assert signal.sl_price < 1.1020  # below broken level

    def test_rr_at_least_two(self):
        """TP - entry >= 2 × (entry - SL)."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        risk = signal.entry_price - signal.sl_price
        reward = signal.tp_price - signal.entry_price
        assert abs(reward / risk - 2.0) < 1e-9

    def test_no_signal_if_close_does_not_exceed_level(self):
        """Close stays at/below resistance → no breakout."""
        level = make_unbroken_level(price=1.1050, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        assert BreakoutStrategy().analyze(ctx) is None

    def test_no_signal_if_bearish_candle_on_resistance(self):
        """Bearish close above resistance is not a valid breakout (no bullish momentum)."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        # Bearish: open > close, but close still above level
        bars = make_bars(last_open=1.1040, last_high=1.1045, last_low=1.1010, last_close=1.1025)
        ctx = make_ctx(bars, levels=[level])

        assert BreakoutStrategy().analyze(ctx) is None

    def test_no_signal_doji_body(self):
        """Body < BREAKOUT_BODY_MIN_PCT → rejected (no momentum)."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        # Doji: open ≈ close, big wicks
        # body = 1.1026 - 1.1025 = 0.0001, range = 1.1040 - 1.1010 = 0.0030 → 3.3%
        bars = make_bars(last_open=1.1025, last_high=1.1040, last_low=1.1010, last_close=1.1026)
        ctx = make_ctx(bars, levels=[level])

        assert BreakoutStrategy().analyze(ctx) is None

    def test_no_signal_if_insufficient_touches(self):
        """Level with < MIN_TOUCHES_BEFORE_BREAK is ignored."""
        level = make_unbroken_level(
            price=1.1020, level_type="resistance", last_touch_at=1015, touches=1
        )
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        assert BreakoutStrategy().analyze(ctx) is None

    def test_no_signal_if_already_broken(self):
        """Broken levels (flip_active) are skipped by breakout strategy."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        level.is_broken = True
        level.broken_at = 1010
        level.flip_active = True
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        assert BreakoutStrategy().analyze(ctx) is None

    def test_no_signal_if_last_touch_too_old(self):
        """last_touch_at way before current bar window → skip."""
        # last_touch=900 predates bar series; _bars_since_timestamp returns current_bar_idx.
        # Use n=25 bars so current_bar_idx=24 > MAX_BARS_SINCE_LAST_TOUCH=20.
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=900)
        bars = make_bars(n=25, last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        assert BreakoutStrategy().analyze(ctx) is None

    def test_confidence_minimum_with_trend_only(self):
        """Aligned trend (mandatory now) but no volume/body boost → confidence == 0.75.

        Con REQUIRE_TREND_ALIGNMENT=True una señal sin alineación se descarta,
        así que la confianza mínima alcanzable es base 0.65 + 0.10 por tendencia.
        """
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        # trend aligned (bullish) → +0.10; low volume → no boost; body 0.67 < 0.70 → no boost
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bullish", "bullish"), volume=make_volume("low"))

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        assert signal.confidence == pytest.approx(0.75)

    def test_counter_trend_breakout_is_filtered(self):
        """REQUIRE_TREND_ALIGNMENT: breakout contra la tendencia se descarta."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        # BUY breakout but trend is bearish → counter-trend → no signal
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bearish", "bearish"))

        assert BreakoutStrategy().analyze(ctx) is None

    def test_confidence_boosted_by_trend_and_volume(self):
        """Aligned trend + high volume → confidence >= 0.85."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(
            bars, levels=[level],
            trend=make_trend("bullish", "bullish"),
            volume=make_volume("high"),
        )

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        assert signal.confidence >= 0.85


# ── SELL (support breakdown) ──────────────────────────────────────────────────

class TestBreakoutSell:
    def test_support_breakdown_generates_sell_signal(self):
        """Bearish candle closes below support → SELL."""
        level = make_unbroken_level(price=1.1000, level_type="support", last_touch_at=1015)
        # close=1.0985 < 1.1000-buffer; bearish; body ok
        # open=1.1010, high=1.1015, low=1.0975, close=1.0985
        # body = 1.1010-1.0985 = 0.0025, range = 1.1015-1.0975 = 0.0040, body_pct=0.625
        bars = make_bars(last_open=1.1010, last_high=1.1015, last_low=1.0975, last_close=1.0985)
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bearish", "bearish"))

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        assert signal.direction == "SELL"
        assert signal.sl_price > signal.entry_price
        assert signal.tp_price < signal.entry_price

    def test_sl_is_above_broken_support(self):
        """SL must be placed just above the broken support level."""
        level = make_unbroken_level(price=1.1000, level_type="support", last_touch_at=1015)
        bars = make_bars(last_open=1.1010, last_high=1.1015, last_low=1.0975, last_close=1.0985)
        # SELL breakdown requires bearish trend to pass REQUIRE_TREND_ALIGNMENT
        ctx = make_ctx(bars, levels=[level], trend=make_trend("bearish", "bearish"))

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        assert signal.sl_price > 1.1000  # above broken level

    def test_no_signal_if_bullish_candle_on_support(self):
        """Bullish close below support is not a valid breakdown."""
        level = make_unbroken_level(price=1.1000, level_type="support", last_touch_at=1015)
        # Bullish: close > open, but close still below support
        bars = make_bars(last_open=1.0985, last_high=1.1015, last_low=1.0975, last_close=1.0995)
        ctx = make_ctx(bars, levels=[level])

        assert BreakoutStrategy().analyze(ctx) is None

    def test_no_signal_when_no_levels(self):
        bars = make_bars()
        ctx = make_ctx(bars, levels=[])

        assert BreakoutStrategy().analyze(ctx) is None


# ── Cooldown behaviour ────────────────────────────────────────────────────────

class TestBreakoutCooldown:
    def test_cooldown_prevents_repeat_signal_same_level(self):
        """After a signal fires, the same level is in cooldown."""
        level = make_unbroken_level(price=1.1020, level_type="resistance", last_touch_at=1015)
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(bars, levels=[level])

        strategy = BreakoutStrategy()
        first = strategy.analyze(ctx)
        assert first is not None

        second = strategy.analyze(ctx)
        assert second is None

    def test_highest_confidence_returned_for_multiple_levels(self):
        """When two levels both trigger, the highest-confidence one is returned."""
        level1 = make_unbroken_level(
            price=1.1020, level_type="resistance", last_touch_at=1015, level_id="lv-1"
        )
        level2 = make_unbroken_level(
            price=1.1021, level_type="resistance", last_touch_at=1015, level_id="lv-2"
        )
        bars = make_bars(last_open=1.1015, last_high=1.1040, last_low=1.1010, last_close=1.1035)
        ctx = make_ctx(
            bars, levels=[level1, level2],
            trend=make_trend("bullish", "bullish"),
            volume=make_volume("high"),
        )

        signal = BreakoutStrategy().analyze(ctx)

        assert signal is not None
        assert signal.direction == "BUY"
