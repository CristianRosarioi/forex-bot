"""Estrategia Breakout: entrada en la ruptura confirmada de un nivel clave con momentum."""

from __future__ import annotations

from datetime import datetime, timedelta

from bot.strategy.base import BaseStrategy, StrategyContext, Signal
from bot.analysis.trend import is_aligned_with_trend
from bot.analysis.volume import is_volume_supportive
from bot.infra.logger import get_logger

logger = get_logger(__name__)

# Filtro duro: si está activo, descarta señales que van contra la tendencia H4/D1.
REQUIRE_TREND_ALIGNMENT = True

MIN_TOUCHES_BEFORE_BREAK = 2
BREAKOUT_BODY_MIN_PCT = 0.50
BREAKOUT_BUFFER_PCT = 0.02 / 100       # close must exceed level by 0.02%
SL_BUFFER_PCT = 0.08 / 100             # SL just inside the broken level
RR_TARGET = 2.0

COOLDOWN_BARS_M15 = 8     # 8 × 15 min = 2 h
COOLDOWN_BARS_H1 = 4      # 4 × 60 min = 4 h

MAX_BARS_SINCE_LAST_TOUCH = 20


class BreakoutStrategy(BaseStrategy):
    # _level_cooldowns is in-session state; resets on bot restart — accepted behaviour.
    name = "breakout"

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

        now = datetime.now()
        expired = [lid for lid, until in self._level_cooldowns.items() if now >= until]
        for lid in expired:
            del self._level_cooldowns[lid]

        if ctx.timeframe == "M15":
            cooldown_delta = timedelta(minutes=COOLDOWN_BARS_M15 * 15)
        else:
            cooldown_delta = timedelta(hours=COOLDOWN_BARS_H1)

        # Only unbroken levels with enough pre-break touches
        active_levels = [
            lv for lv in ctx.levels
            if not lv.is_broken and len(lv.touches) >= MIN_TOUCHES_BEFORE_BREAK
        ]

        candidates: list[Signal] = []

        for level in active_levels:
            if level.id in self._level_cooldowns:
                continue

            # Recent activity: last touch must be within lookback window
            bars_since_touch = _bars_since_timestamp(ctx.bars, level.last_touch_at, current_bar_idx)
            if bars_since_touch > MAX_BARS_SINCE_LAST_TOUCH:
                continue

            level_price = level.price
            buffer_abs = level_price * BREAKOUT_BUFFER_PCT

            if level.type == "resistance":
                # BUY: close crossed above resistance with momentum
                if not (bar_close > level_price + buffer_abs and bar_close > bar_open):
                    continue
                direction = "BUY"
            else:  # support
                # SELL: close dropped below support with momentum
                if not (bar_close < level_price - buffer_abs and bar_close < bar_open):
                    continue
                direction = "SELL"

            # Momentum filter: body must dominate the candle
            body_pct = abs(bar_close - bar_open) / candle_range
            if body_pct < BREAKOUT_BODY_MIN_PCT:
                continue

            # Filtro duro de tendencia: breakout NUNCA opera contra H4/D1.
            if REQUIRE_TREND_ALIGNMENT and not is_aligned_with_trend(direction, ctx.trend):
                logger.info(
                    "Breakout %s %s skipped: counter-trend (h4=%s d1=%s)",
                    ctx.symbol, direction, ctx.trend.h4_bias, ctx.trend.d1_bias,
                )
                continue

            # Confidence
            confidence = 0.65
            if is_aligned_with_trend(direction, ctx.trend):
                confidence += 0.10
            if is_volume_supportive(ctx.volume):
                confidence += 0.10
            if body_pct >= 0.70:
                confidence += 0.05
            confidence = min(confidence, 1.0)

            # SL behind the broken level; TP at RR_TARGET × risk
            sl_buffer = level_price * SL_BUFFER_PCT
            if direction == "BUY":
                sl_price = level_price - sl_buffer
                risk = bar_close - sl_price
                tp_price = bar_close + risk * RR_TARGET
            else:
                sl_price = level_price + sl_buffer
                risk = sl_price - bar_close
                tp_price = bar_close - risk * RR_TARGET

            if risk <= 0:
                continue

            sig = Signal(
                symbol=ctx.symbol,
                timeframe=ctx.timeframe,
                strategy_name=self.name,
                signal_type="breakout",
                direction=direction,
                entry_price=bar_close,
                sl_price=sl_price,
                tp_price=tp_price,
                reason=(
                    f"Breakout of {level.type} at {level_price:.5f} "
                    f"(touches={len(level.touches)}, body={body_pct:.2f}, "
                    f"confidence={confidence:.2f})"
                ),
                confidence=confidence,
                metadata={
                    "level_id": level.id,
                    "level_price": level_price,
                    "level_type": level.type,
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
