"""Identificación de swing highs y swing lows relevantes en un DataFrame de velas."""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass


@dataclass
class SwingPoint:
    timestamp: int      # unix seconds (MT5 bar time)
    price: float
    type: str           # "high" / "low"
    bar_index: int      # index in original DataFrame
    strength: int       # lookback used (bars confirming on each side)


def detect_swings(bars: pd.DataFrame, lookback: int = 3) -> list[SwingPoint]:
    """Fractal-based swing detection.

    A swing high at index i requires:
      bars.high[i] > max(bars.high[i-lookback:i]) AND
      bars.high[i] > max(bars.high[i+1:i+lookback+1])
    Similarly for swing lows using bars.low.

    Args:
        bars: DataFrame with columns: time, open, high, low, close, tick_volume
        lookback: bars to confirm on each side (min 2, default 3)
    Returns:
        List of SwingPoint sorted by bar_index ascending
    """
    if bars is None or bars.empty:
        return []

    lookback = max(2, lookback)
    n = len(bars)

    # Need at least 2*lookback + 1 bars to detect any swing
    if n < 2 * lookback + 1:
        return []

    swings: list[SwingPoint] = []

    for i in range(lookback, n - lookback):
        high_i = bars["high"].iloc[i]
        low_i = bars["low"].iloc[i]

        # Swing high: higher than lookback bars on each side
        left_highs = bars["high"].iloc[i - lookback:i]
        right_highs = bars["high"].iloc[i + 1:i + lookback + 1]

        if high_i > left_highs.max() and high_i > right_highs.max():
            ts = int(bars["time"].iloc[i])
            swings.append(SwingPoint(
                timestamp=ts,
                price=float(high_i),
                type="high",
                bar_index=i,
                strength=lookback,
            ))

        # Swing low: lower than lookback bars on each side
        left_lows = bars["low"].iloc[i - lookback:i]
        right_lows = bars["low"].iloc[i + 1:i + lookback + 1]

        if low_i < left_lows.min() and low_i < right_lows.min():
            ts = int(bars["time"].iloc[i])
            swings.append(SwingPoint(
                timestamp=ts,
                price=float(low_i),
                type="low",
                bar_index=i,
                strength=lookback,
            ))

    # Sort by bar_index ascending
    swings.sort(key=lambda s: s.bar_index)
    return swings


def get_recent_swings(swings: list[SwingPoint], n: int = 10) -> list[SwingPoint]:
    """Returns last N swings."""
    return swings[-n:] if len(swings) >= n else swings[:]


def get_swing_pairs_for_levels(
    swings: list[SwingPoint],
    min_distance_pct: float = 0.1,
) -> list[tuple[SwingPoint, SwingPoint]]:
    """Finds pairs of swings forming levels (two similar-priced highs = resistance, etc.).

    min_distance_pct: max price difference to consider them the same level
                      (0.1% = ~10 pips EURUSD).
    Only pairs same-type swings (high+high or low+low).
    """
    pairs: list[tuple[SwingPoint, SwingPoint]] = []

    highs = [s for s in swings if s.type == "high"]
    lows = [s for s in swings if s.type == "low"]

    for group in (highs, lows):
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                avg_price = (a.price + b.price) / 2
                if avg_price == 0:
                    continue
                diff_pct = abs(a.price - b.price) / avg_price * 100
                if diff_pct <= min_distance_pct:
                    pairs.append((a, b))

    return pairs
