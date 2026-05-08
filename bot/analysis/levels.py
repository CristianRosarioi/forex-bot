"""Detección y gestión de niveles de soporte/resistencia basados en swing points."""

from __future__ import annotations

import uuid
import pandas as pd
from dataclasses import dataclass, field

from bot.analysis.swing import SwingPoint, get_swing_pairs_for_levels


@dataclass
class Level:
    id: str
    symbol: str
    timeframe: str
    price: float
    type: str           # "resistance" / "support"
    touches: list[int]  # unix timestamps of touches
    created_at: int
    last_touch_at: int
    is_broken: bool = False
    broken_at: int | None = None
    flip_active: bool = False   # True after break: former R → S or S → R
    confidence: float = 0.5


class LevelManager:
    def __init__(self, max_age_bars: int = 200, min_touches: int = 1):
        self._levels: dict[str, list[Level]] = {}  # key = "SYMBOL_TF"
        self.max_age_bars = max_age_bars
        self.min_touches = min_touches

    def _key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"

    def detect_levels(
        self,
        bars: pd.DataFrame,
        swings: list[SwingPoint],
        symbol: str,
        timeframe: str,
        tolerance_pct: float = 0.05,
    ) -> list[Level]:
        """Creates Level objects from swing pairs.

        Two highs close in price → resistance at average price.
        Two lows close in price → support at average price.
        touches = list of timestamps of swings within tolerance.
        confidence = min(touches/5, 1.0) — more touches = higher confidence.
        Does NOT add duplicates (checks if level at same price ±tolerance already exists).
        """
        key = self._key(symbol, timeframe)
        if key not in self._levels:
            self._levels[key] = []

        existing = self._levels[key]

        # get_swing_pairs_for_levels expects min_distance_pct in percent
        pairs = get_swing_pairs_for_levels(swings, min_distance_pct=tolerance_pct * 2)

        new_levels: list[Level] = []
        for a, b in pairs:
            avg_price = (a.price + b.price) / 2
            level_type = "resistance" if a.type == "high" else "support"

            # Collect all touch timestamps (swings near this price)
            touch_timestamps = []
            for s in swings:
                if s.type != a.type:
                    continue
                if avg_price == 0:
                    continue
                diff_pct = abs(s.price - avg_price) / avg_price * 100
                if diff_pct <= tolerance_pct * 2:
                    touch_timestamps.append(s.timestamp)

            # Deduplicate timestamps
            touch_timestamps = sorted(set(touch_timestamps))

            # Check if duplicate already exists
            tol_abs = avg_price * tolerance_pct / 100
            is_duplicate = any(
                abs(lv.price - avg_price) <= tol_abs and lv.type == level_type
                for lv in existing
            )
            if is_duplicate:
                continue

            # Also skip if already in new_levels this round
            is_dup_new = any(
                abs(lv.price - avg_price) <= tol_abs and lv.type == level_type
                for lv in new_levels
            )
            if is_dup_new:
                continue

            created_ts = touch_timestamps[0] if touch_timestamps else int(bars["time"].iloc[0])
            last_ts = touch_timestamps[-1] if touch_timestamps else created_ts

            confidence = min(len(touch_timestamps) / 5, 1.0)

            level = Level(
                id=str(uuid.uuid4()),
                symbol=symbol,
                timeframe=timeframe,
                price=avg_price,
                type=level_type,
                touches=touch_timestamps,
                created_at=created_ts,
                last_touch_at=last_ts,
                confidence=confidence,
            )
            new_levels.append(level)

        self._levels[key].extend(new_levels)
        return new_levels

    def update_levels(self, bars: pd.DataFrame, symbol: str, timeframe: str) -> None:
        """For each active level:
        - If last bar's close crossed the level by > tolerance: mark is_broken=True, flip_active=True
        - If last bar's high/low touched the level (within tolerance) without breaking: add touch timestamp
        - Remove levels where (current_bar_index - last_touch_bar) > max_age_bars
        """
        key = self._key(symbol, timeframe)
        if key not in self._levels or bars.empty:
            return

        last_bar = bars.iloc[-1]
        last_close = float(last_bar["close"])
        last_high = float(last_bar["high"])
        last_low = float(last_bar["low"])
        last_ts = int(last_bar["time"])
        current_bar_idx = len(bars) - 1

        tolerance_pct = 0.05 / 100  # 0.05%

        to_remove: list[str] = []

        for level in self._levels[key]:
            if level.is_broken and not level.flip_active:
                to_remove.append(level.id)
                continue

            tol_abs = level.price * tolerance_pct

            # Check if broken: close crossed level by more than tolerance
            if not level.is_broken:
                if level.type == "resistance" and last_close > level.price + tol_abs:
                    level.is_broken = True
                    level.broken_at = last_ts
                    level.flip_active = True
                    level.touches.append(last_ts)
                    level.last_touch_at = last_ts
                    continue
                elif level.type == "support" and last_close < level.price - tol_abs:
                    level.is_broken = True
                    level.broken_at = last_ts
                    level.flip_active = True
                    level.touches.append(last_ts)
                    level.last_touch_at = last_ts
                    continue

            # Check if touched (within tolerance) without breaking
            touched = False
            if level.type == "resistance":
                if abs(last_high - level.price) <= tol_abs:
                    touched = True
            else:  # support
                if abs(last_low - level.price) <= tol_abs:
                    touched = True

            if touched:
                level.touches.append(last_ts)
                level.last_touch_at = last_ts
                level.confidence = min(len(level.touches) / 5, 1.0)

            # Check max_age_bars: find bar index of last_touch_at
            # Approximate: find bar whose timestamp is closest to last_touch_at
            if "time" in bars.columns:
                times = bars["time"].values
                last_touch_bar_idx = 0
                for idx, t in enumerate(times):
                    if int(t) <= level.last_touch_at:
                        last_touch_bar_idx = idx
                age_bars = current_bar_idx - last_touch_bar_idx
                if age_bars > self.max_age_bars:
                    to_remove.append(level.id)

        # Remove stale/broken-inactive levels
        if to_remove:
            self._levels[key] = [lv for lv in self._levels[key] if lv.id not in to_remove]

    def get_active_levels(self, symbol: str, timeframe: str) -> list[Level]:
        """Returns levels that are either unbroken (is_broken=False) or flip_active=True."""
        key = self._key(symbol, timeframe)
        return [
            lv for lv in self._levels.get(key, [])
            if not lv.is_broken or lv.flip_active
        ]

    def get_nearest_level(
        self,
        symbol: str,
        timeframe: str,
        price: float,
        direction: str,  # "above" / "below"
    ) -> Level | None:
        """Returns closest active level above or below price."""
        active = self.get_active_levels(symbol, timeframe)

        if direction == "above":
            candidates = [lv for lv in active if lv.price > price]
            if not candidates:
                return None
            return min(candidates, key=lambda lv: lv.price - price)
        else:  # below
            candidates = [lv for lv in active if lv.price < price]
            if not candidates:
                return None
            return max(candidates, key=lambda lv: lv.price)
