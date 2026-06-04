"""Estrategia Retest: entrada en el pullback a un nivel clave previamente roto."""

from __future__ import annotations

from datetime import datetime, timedelta

from bot.strategy.base import BaseStrategy, StrategyContext, Signal
from bot.analysis.levels import Level
from bot.analysis.volume import is_volume_supportive
from bot.infra.logger import get_logger

logger = get_logger(__name__)

# Configuration constants (will move to yaml later)
MIN_TOUCHES_BEFORE_BREAK = 1
BUFFER_PCT_FOR_BREAK = 0.02 / 100      # 0.02% (~2 pips EURUSD)
BUFFER_PCT_FOR_RETEST = 0.05 / 100     # 0.05% (~5 pips EURUSD)
MAX_BARS_AFTER_BREAK = 30
REJECTION_BODY_MIN_PCT = 0.4
SL_BUFFER_PCT = 0.10 / 100             # 10 pips buffer
RR_TARGET = 2.0

# Cooldown after a signal fires: prevents re-detecting the same level for N bars
COOLDOWN_BARS_M15 = 16   # 16 × 15 min = 4 h
COOLDOWN_BARS_H1 = 8     # 8 × 60 min = 8 h


class RetestStrategy(BaseStrategy):
    # Nota: _level_cooldowns es estado operativo de sesión, no persistente.
    # Se resetea en cada reinicio del bot — comportamiento aceptado.
    name = "retest"

    def __init__(self) -> None:
        self._level_cooldowns: dict[str, datetime] = {}

    def analyze(self, ctx: StrategyContext) -> Signal | None:
        """
        Full retest logic:

        1. Get flip_active levels from ctx.levels
        2. For each flip level:
           a. Check broken_at exists and bars since break <= MAX_BARS_AFTER_BREAK
           b. Check len(touches before break) >= MIN_TOUCHES_BEFORE_BREAK
           c. Determine direction: broken resistance → BUY, broken support → SELL
           d. Check last bar retested the level (within BUFFER_PCT_FOR_RETEST of level.price)
           e. Check rejection candle
           f. Check body_pct >= REJECTION_BODY_MIN_PCT
           g. Compute confidence
           h. Build Signal
        3. If multiple valid signals: return highest confidence
        4. If none: return None
        """
        if ctx.bars is None or ctx.bars.empty:
            return None

        last_bar = ctx.bars.iloc[-1]
        bar_close = float(last_bar["close"])
        bar_open = float(last_bar["open"])
        bar_high = float(last_bar["high"])
        bar_low = float(last_bar["low"])
        current_bar_idx = len(ctx.bars) - 1

        # Purge expired cooldowns
        now = datetime.now()
        expired = [lid for lid, until in self._level_cooldowns.items() if now >= until]
        for lid in expired:
            del self._level_cooldowns[lid]

        # Compute cooldown duration from timeframe
        if ctx.timeframe == "M15":
            cooldown_delta = timedelta(minutes=COOLDOWN_BARS_M15 * 15)
        else:  # H1
            cooldown_delta = timedelta(hours=COOLDOWN_BARS_H1)

        # Get flip_active levels
        flip_levels = [lv for lv in ctx.levels if lv.flip_active and lv.is_broken]

        candidates: list[Signal] = []

        for level in flip_levels:
            # Skip levels in cooldown
            if level.id in self._level_cooldowns:
                continue

            # a. broken_at must exist and bars since break <= MAX_BARS_AFTER_BREAK
            if level.broken_at is None:
                continue

            # Estimate bars since break by finding bar index where break happened
            bars_since_break = _estimate_bars_since_break(ctx.bars, level.broken_at, current_bar_idx)
            if bars_since_break > MAX_BARS_AFTER_BREAK:
                continue

            # b. Count touches before break
            pre_break_touches = [t for t in level.touches if t < level.broken_at]
            if len(pre_break_touches) < MIN_TOUCHES_BEFORE_BREAK:
                continue

            # c. Direction: broken resistance → BUY, broken support → SELL
            if level.type == "resistance":
                direction = "BUY"
            else:
                direction = "SELL"

            # d. Check last bar retested the level
            level_price = level.price
            tol_abs = level_price * BUFFER_PCT_FOR_RETEST

            if direction == "BUY":
                # Bar's low should touch the level from above
                retested = bar_low <= level_price + tol_abs
            else:
                # Bar's high should touch the level from below
                retested = bar_high >= level_price - tol_abs

            if not retested:
                continue

            # e. Check rejection candle
            if direction == "BUY":
                # Bullish rejection: close above level, low touched level, bullish candle
                close_above_level = bar_close > level_price
                low_touched = bar_low <= level_price * (1 + BUFFER_PCT_FOR_RETEST)
                bullish_candle = bar_close > bar_open
                valid_candle = close_above_level and low_touched and bullish_candle
            else:
                # Bearish rejection: close below level, high touched level, bearish candle
                close_below_level = bar_close < level_price
                high_touched = bar_high >= level_price * (1 - BUFFER_PCT_FOR_RETEST)
                bearish_candle = bar_close < bar_open
                valid_candle = close_below_level and high_touched and bearish_candle

            if not valid_candle:
                continue

            # f. Check body_pct >= REJECTION_BODY_MIN_PCT (protect against doji)
            candle_range = bar_high - bar_low
            if candle_range <= 0:
                continue
            body_pct = abs(bar_close - bar_open) / candle_range
            if body_pct < REJECTION_BODY_MIN_PCT:
                continue

            # g. Compute confidence
            confidence = 0.7
            if ctx.trend.h4_bias.lower() == direction.lower().replace("buy", "bullish").replace("sell", "bearish"):
                confidence += 0.1
            if ctx.trend.d1_bias.lower() == direction.lower().replace("buy", "bullish").replace("sell", "bearish"):
                confidence += 0.1
            if is_volume_supportive(ctx.volume):
                confidence += 0.1
            confidence = min(confidence, 1.0)

            # h. Build Signal
            entry_price = bar_close

            if direction == "BUY":
                sl_price = bar_low - (bar_low * SL_BUFFER_PCT)
                risk = entry_price - sl_price
                tp_price = entry_price + risk * RR_TARGET
            else:
                sl_price = bar_high + (bar_high * SL_BUFFER_PCT)
                risk = sl_price - entry_price
                tp_price = entry_price - risk * RR_TARGET

            if risk <= 0:
                continue

            sig = Signal(
                symbol=ctx.symbol,
                timeframe=ctx.timeframe,
                strategy_name=self.name,
                signal_type="retest",
                direction=direction,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                reason=(
                    f"Retest of {level.type} level at {level_price:.5f} "
                    f"(breaks={len(pre_break_touches)}, confidence={confidence:.2f})"
                ),
                confidence=confidence,
                metadata={
                    "level_id": level.id,
                    "level_price": level_price,
                    "level_type": level.type,
                    "bars_since_break": bars_since_break,
                    "body_pct": round(body_pct, 3),
                },
            )
            self._level_cooldowns[level.id] = now + cooldown_delta
            candidates.append(sig)

        if not candidates:
            return None

        # Return highest confidence signal
        return max(candidates, key=lambda s: s.confidence)


def _estimate_bars_since_break(bars: "pd.DataFrame", broken_at: int, current_bar_idx: int) -> int:
    """Estimate how many bars ago the break occurred by comparing timestamps."""
    import pandas as pd
    times = bars["time"].values
    # Find the bar index closest to broken_at
    for idx in range(len(times) - 1, -1, -1):
        if int(times[idx]) <= broken_at:
            return current_bar_idx - idx
    return current_bar_idx  # break happened before our buffer
