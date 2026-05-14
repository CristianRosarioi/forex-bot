"""Estrategia Fade/Failure: entrada contra el fallo de un breakout (trampa de liquidez)."""

from __future__ import annotations

from datetime import datetime, timedelta

from bot.strategy.base import BaseStrategy, StrategyContext, Signal
from bot.analysis.trend import is_aligned_with_trend
from bot.analysis.volume import is_volume_supportive
from bot.infra.logger import get_logger

logger = get_logger(__name__)

MIN_TOUCHES_FOR_FADE = 2
PROBE_BUFFER_PCT = 0.02 / 100    # wick must pierce at least 0.02% beyond level
WICK_MIN_PCT = 0.30              # rejection wick >= 30% of candle range
BODY_MIN_PCT = 0.35              # body >= 35% (avoids doji)
SL_BUFFER_PCT = 0.05 / 100      # SL beyond the wick extreme
RR_TARGET = 2.0

COOLDOWN_BARS_M15 = 8
COOLDOWN_BARS_H1 = 4

MAX_BARS_SINCE_LAST_TOUCH = 20


class FadeFailureStrategy(BaseStrategy):
    # _level_cooldowns is in-session state; resets on bot restart — accepted behaviour.
    name = "fade_failure"

    def __init__(self) -> None:
        self._level_cooldowns: dict[str, datetime] = {}

    def analyze(self, ctx: StrategyContext) -> Signal | None:
        if ctx.bars is None or ctx.bars.empty:
            return None

        last_bar = ctx.bars.iloc[-1]
        bar_close = float(last_bar["close"])
        bar_open = float(last_bar["open"])
        bar_high = float(last_bar["high"])
        bar_low = float(last_bar["low"])
        current_bar_idx = len(ctx.bars) - 1

        candle_range = bar_high - bar_low
        if candle_range <= 0:
            return None

        body_pct = abs(bar_close - bar_open) / candle_range

        now = datetime.now()
        expired = [lid for lid, until in self._level_cooldowns.items() if now >= until]
        for lid in expired:
            del self._level_cooldowns[lid]

        if ctx.timeframe == "M15":
            cooldown_delta = timedelta(minutes=COOLDOWN_BARS_M15 * 15)
        else:
            cooldown_delta = timedelta(hours=COOLDOWN_BARS_H1)

        # Unbroken levels with enough touches
        fade_levels = [
            lv for lv in ctx.levels
            if not lv.is_broken and len(lv.touches) >= MIN_TOUCHES_FOR_FADE
        ]

        candidates: list[Signal] = []

        for level in fade_levels:
            if level.id in self._level_cooldowns:
                continue

            bars_since_touch = _bars_since_timestamp(ctx.bars, level.last_touch_at, current_bar_idx)
            if bars_since_touch > MAX_BARS_SINCE_LAST_TOUCH:
                continue

            level_price = level.price
            probe_abs = level_price * PROBE_BUFFER_PCT

            if level.type == "resistance":
                # Wick pierced above resistance, close came back below → SELL
                probed_above = bar_high > level_price + probe_abs
                closed_below = bar_close < level_price
                if not (probed_above and closed_below):
                    continue

                upper_wick = bar_high - max(bar_close, bar_open)
                if upper_wick / candle_range < WICK_MIN_PCT:
                    continue
                if body_pct < BODY_MIN_PCT:
                    continue

                direction = "SELL"
                sl_price = bar_high + bar_high * SL_BUFFER_PCT
                risk = sl_price - bar_close
                tp_price = bar_close - risk * RR_TARGET
                wick_extreme = bar_high

            else:  # support
                # Wick pierced below support, close came back above → BUY
                probed_below = bar_low < level_price - probe_abs
                closed_above = bar_close > level_price
                if not (probed_below and closed_above):
                    continue

                lower_wick = min(bar_close, bar_open) - bar_low
                if lower_wick / candle_range < WICK_MIN_PCT:
                    continue
                if body_pct < BODY_MIN_PCT:
                    continue

                direction = "BUY"
                sl_price = bar_low - bar_low * SL_BUFFER_PCT
                risk = bar_close - sl_price
                tp_price = bar_close + risk * RR_TARGET
                wick_extreme = bar_low

            if risk <= 0:
                continue

            confidence = 0.60
            if is_aligned_with_trend(direction, ctx.trend):
                confidence += 0.10
            if is_volume_supportive(ctx.volume):
                confidence += 0.10
            if body_pct >= 0.50:
                confidence += 0.05
            confidence = min(confidence, 1.0)

            sig = Signal(
                symbol=ctx.symbol,
                timeframe=ctx.timeframe,
                strategy_name=self.name,
                signal_type="fade",
                direction=direction,
                entry_price=bar_close,
                sl_price=sl_price,
                tp_price=tp_price,
                reason=(
                    f"False breakout of {level.type} at {level_price:.5f} "
                    f"(wick_extreme={wick_extreme:.5f}, body={body_pct:.2f}, "
                    f"confidence={confidence:.2f})"
                ),
                confidence=confidence,
                metadata={
                    "level_id": level.id,
                    "level_price": level_price,
                    "level_type": level.type,
                    "wick_extreme": round(wick_extreme, 5),
                    "body_pct": round(body_pct, 3),
                },
            )
            self._level_cooldowns[level.id] = now + cooldown_delta
            candidates.append(sig)

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.confidence)


def _bars_since_timestamp(bars: "pd.DataFrame", timestamp: int, current_bar_idx: int) -> int:
    """Estimate how many bars ago a timestamp occurred."""
    times = bars["time"].values
    for idx in range(len(times) - 1, -1, -1):
        if int(times[idx]) <= timestamp:
            return current_bar_idx - idx
    return current_bar_idx
