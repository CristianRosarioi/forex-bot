"""Detección de estructura de mercado: Break of Structure (BOS) y Change of Character (CHoCH)."""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass, field

from bot.analysis.swing import SwingPoint


@dataclass
class MarketStructure:
    last_swing_high: SwingPoint | None = None
    last_swing_low: SwingPoint | None = None
    higher_highs: list[SwingPoint] = field(default_factory=list)
    higher_lows: list[SwingPoint] = field(default_factory=list)
    lower_highs: list[SwingPoint] = field(default_factory=list)
    lower_lows: list[SwingPoint] = field(default_factory=list)
    last_bos: dict | None = None      # {direction, price, timestamp, bar_index}
    last_choch: dict | None = None
    structure_state: str = "ranging"  # "bullish" / "bearish" / "ranging"


def analyze_structure(bars: pd.DataFrame, swings: list[SwingPoint]) -> MarketStructure:
    """
    Uses last 6 swings max to classify structure.

    Algorithm:
    1. Separate swings into highs and lows (by type)
    2. Compare consecutive same-type swings:
       - Two consecutive highs: if h2 > h1 → HH, else → LH
       - Two consecutive lows: if l2 > l1 → HL, else → LL
    3. structure_state:
       - "bullish" if recent pattern has HH AND HL
       - "bearish" if recent pattern has LH AND LL
       - "ranging" otherwise
    4. last_bos: if latest bar's high breaks last swing high (bullish BOS)
       or latest bar's low breaks last swing low (bearish BOS)
    5. last_choch: in bullish trend, if a new LL forms → CHoCH bearish
       in bearish trend, if a new HH forms → CHoCH bullish
    """
    ms = MarketStructure()

    if not swings:
        return ms

    # Use last 6 swings
    recent = swings[-6:]

    highs = [s for s in recent if s.type == "high"]
    lows = [s for s in recent if s.type == "low"]

    if highs:
        ms.last_swing_high = highs[-1]
    if lows:
        ms.last_swing_low = lows[-1]

    # Classify consecutive highs
    for i in range(1, len(highs)):
        if highs[i].price > highs[i - 1].price:
            ms.higher_highs.append(highs[i])
        else:
            ms.lower_highs.append(highs[i])

    # Classify consecutive lows
    for i in range(1, len(lows)):
        if lows[i].price > lows[i - 1].price:
            ms.higher_lows.append(lows[i])
        else:
            ms.lower_lows.append(lows[i])

    has_hh = len(ms.higher_highs) > 0
    has_hl = len(ms.higher_lows) > 0
    has_lh = len(ms.lower_highs) > 0
    has_ll = len(ms.lower_lows) > 0

    if has_hh and has_hl:
        ms.structure_state = "bullish"
    elif has_lh and has_ll:
        ms.structure_state = "bearish"
    else:
        ms.structure_state = "ranging"

    # BOS detection on last bar
    ms.last_bos = detect_bos(bars, ms)

    # CHoCH detection
    ms.last_choch = detect_choch(bars, ms)

    return ms


def detect_bos(bars: pd.DataFrame, structure: MarketStructure) -> dict | None:
    """Detects BOS on the last closed bar."""
    if bars is None or bars.empty:
        return None

    last_bar = bars.iloc[-1]

    # Bullish BOS: last bar's high breaks above last swing high
    if structure.last_swing_high is not None:
        if float(last_bar["high"]) > structure.last_swing_high.price:
            ts = int(last_bar["time"])
            return {
                "direction": "bullish",
                "price": structure.last_swing_high.price,
                "timestamp": ts,
                "bar_index": len(bars) - 1,
            }

    # Bearish BOS: last bar's low breaks below last swing low
    if structure.last_swing_low is not None:
        if float(last_bar["low"]) < structure.last_swing_low.price:
            ts = int(last_bar["time"])
            return {
                "direction": "bearish",
                "price": structure.last_swing_low.price,
                "timestamp": ts,
                "bar_index": len(bars) - 1,
            }

    return None


def detect_choch(bars: pd.DataFrame, structure: MarketStructure) -> dict | None:
    """Detects CHoCH."""
    if bars is None or bars.empty:
        return None

    last_bar = bars.iloc[-1]
    ts = int(last_bar["time"])

    # In bullish trend, if a new LL forms → CHoCH bearish
    if structure.structure_state == "bullish":
        if len(structure.lower_lows) > 0:
            return {
                "direction": "bearish",
                "timestamp": ts,
                "bar_index": len(bars) - 1,
                "reason": "LL in bullish structure",
            }

    # In bearish trend, if a new HH forms → CHoCH bullish
    if structure.structure_state == "bearish":
        if len(structure.higher_highs) > 0:
            return {
                "direction": "bullish",
                "timestamp": ts,
                "bar_index": len(bars) - 1,
                "reason": "HH in bearish structure",
            }

    return None
