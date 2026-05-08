"""Determinación del sesgo de tendencia (bullish / bearish / ranging) por timeframe."""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass

from bot.analysis.swing import detect_swings
from bot.analysis.structure import analyze_structure


@dataclass
class TrendBias:
    h4_bias: str    # "bullish" / "bearish" / "ranging"
    d1_bias: str
    overall: str    # "bullish" / "bearish" / "ranging" / mixed variants
    confidence: float   # 1.0 if both agree, 0.5 if one ranging, 0.3 if conflict


def analyze_trend(bars_h4: pd.DataFrame, bars_d1: pd.DataFrame) -> TrendBias:
    """
    overall logic:
    - "bullish" if both bullish
    - "bearish" if both bearish
    - "ranging" if both ranging
    - "h4_{h4}_d1_{d1}" for mixed (e.g., "h4_bullish_d1_ranging")
    confidence: 1.0 both same, 0.5 one is ranging, 0.3 if conflict (one bullish one bearish)
    """
    # Analyze H4 structure
    if bars_h4 is not None and not bars_h4.empty:
        h4_swings = detect_swings(bars_h4, lookback=3)
        h4_structure = analyze_structure(bars_h4, h4_swings)
        h4_bias = h4_structure.structure_state
    else:
        h4_bias = "ranging"

    # Analyze D1 structure
    if bars_d1 is not None and not bars_d1.empty:
        d1_swings = detect_swings(bars_d1, lookback=3)
        d1_structure = analyze_structure(bars_d1, d1_swings)
        d1_bias = d1_structure.structure_state
    else:
        d1_bias = "ranging"

    # Determine overall and confidence
    if h4_bias == d1_bias:
        overall = h4_bias
        confidence = 1.0
    elif h4_bias == "ranging" or d1_bias == "ranging":
        overall = f"h4_{h4_bias}_d1_{d1_bias}"
        confidence = 0.5
    else:
        # One bullish, one bearish → conflict
        overall = f"h4_{h4_bias}_d1_{d1_bias}"
        confidence = 0.3

    return TrendBias(
        h4_bias=h4_bias,
        d1_bias=d1_bias,
        overall=overall,
        confidence=confidence,
    )


def is_aligned_with_trend(direction: str, bias: TrendBias, require_overall: bool = False) -> bool:
    """True if direction matches bias.

    If require_overall=True: must match overall.
    If require_overall=False: matches if h4_bias OR d1_bias aligns.
    """
    dir_lower = direction.lower()
    bull_match = dir_lower == "buy"
    bear_match = dir_lower == "sell"

    if require_overall:
        if bull_match:
            return bias.overall == "bullish"
        if bear_match:
            return bias.overall == "bearish"
        return False

    # Match if either H4 or D1 aligns
    if bull_match:
        return bias.h4_bias == "bullish" or bias.d1_bias == "bullish"
    if bear_match:
        return bias.h4_bias == "bearish" or bias.d1_bias == "bearish"

    return False
