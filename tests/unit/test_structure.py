"""Tests for bot/analysis/structure.py"""

import pytest
import pandas as pd

from bot.analysis.swing import SwingPoint
from bot.analysis.structure import (
    MarketStructure,
    analyze_structure,
    detect_bos,
    detect_choch,
)


def make_bars(n: int = 20, base: float = 1.1000) -> pd.DataFrame:
    return pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open": [base] * n,
        "high": [base + 0.0005] * n,
        "low": [base - 0.0005] * n,
        "close": [base] * n,
        "tick_volume": [100] * n,
    })


def swing(ts: int, price: float, stype: str, idx: int) -> SwingPoint:
    return SwingPoint(timestamp=ts, price=price, type=stype, bar_index=idx, strength=3)


class TestAnalyzeStructure:
    def test_bullish_structure(self):
        """HH, HL, HH pattern → bullish"""
        swings = [
            swing(1000, 1.1010, "high", 3),   # first high
            swing(1005, 1.0990, "low", 6),    # first low
            swing(1010, 1.1020, "high", 9),   # HH
            swing(1015, 1.1000, "low", 12),   # HL
            swing(1020, 1.1030, "high", 15),  # HH
        ]
        bars = make_bars(20)
        ms = analyze_structure(bars, swings)
        assert ms.structure_state == "bullish"

    def test_bearish_structure(self):
        """LH, LL, LH pattern → bearish"""
        swings = [
            swing(1000, 1.1020, "high", 3),   # first high
            swing(1005, 1.0990, "low", 6),    # first low
            swing(1010, 1.1010, "high", 9),   # LH
            swing(1015, 1.0980, "low", 12),   # LL
            swing(1020, 1.1000, "high", 15),  # LH
        ]
        bars = make_bars(20)
        ms = analyze_structure(bars, swings)
        assert ms.structure_state == "bearish"

    def test_ranging_mixed(self):
        """Mixed signals → ranging"""
        swings = [
            swing(1000, 1.1010, "high", 3),
            swing(1005, 1.0990, "low", 6),
            swing(1010, 1.1005, "high", 9),  # LH
            swing(1015, 1.0995, "low", 12),  # HL
        ]
        bars = make_bars(20)
        ms = analyze_structure(bars, swings)
        # LH and HL → one of each, not purely bullish or bearish
        assert ms.structure_state == "ranging"

    def test_empty_swings_returns_ranging(self):
        bars = make_bars(20)
        ms = analyze_structure(bars, [])
        assert ms.structure_state == "ranging"
        assert ms.last_bos is None
        assert ms.last_choch is None

    def test_last_swing_high_and_low_set(self):
        swings = [
            swing(1000, 1.1010, "high", 3),
            swing(1005, 1.0990, "low", 6),
        ]
        bars = make_bars(20)
        ms = analyze_structure(bars, swings)
        assert ms.last_swing_high is not None
        assert ms.last_swing_low is not None
        assert ms.last_swing_high.price == 1.1010
        assert ms.last_swing_low.price == 1.0990


class TestDetectBos:
    def test_bullish_bos_detected(self):
        """Last bar's high breaks above last swing high."""
        bars_data = {
            "time": [1000, 1001, 1002],
            "open": [1.1000, 1.1000, 1.1000],
            "high": [1.1010, 1.1010, 1.1030],  # last bar breaks above 1.1020
            "low": [1.0990, 1.0990, 1.0990],
            "close": [1.1005, 1.1005, 1.1025],
            "tick_volume": [100, 100, 200],
        }
        bars = pd.DataFrame(bars_data)
        ms = MarketStructure(
            last_swing_high=swing(1001, 1.1020, "high", 1),
            last_swing_low=swing(1000, 1.0990, "low", 0),
        )
        bos = detect_bos(bars, ms)
        assert bos is not None
        assert bos["direction"] == "bullish"

    def test_no_bos_when_price_below_swing_high(self):
        bars_data = {
            "time": [1000, 1001],
            "open": [1.1000, 1.1000],
            "high": [1.1010, 1.1015],
            "low": [1.0990, 1.0990],
            "close": [1.1005, 1.1010],
            "tick_volume": [100, 100],
        }
        bars = pd.DataFrame(bars_data)
        ms = MarketStructure(
            last_swing_high=swing(1000, 1.1020, "high", 0),  # bar doesn't break this
        )
        bos = detect_bos(bars, ms)
        assert bos is None

    def test_empty_bars_returns_none(self):
        ms = MarketStructure()
        bos = detect_bos(pd.DataFrame(), ms)
        assert bos is None


class TestDetectChoch:
    def test_choch_bearish_in_bullish_structure(self):
        """In bullish trend, if there's a lower_low → CHoCH bearish."""
        bars = make_bars(20)
        ms = MarketStructure(
            structure_state="bullish",
            lower_lows=[swing(1010, 1.0980, "low", 12)],  # a LL in bullish trend
        )
        choch = detect_choch(bars, ms)
        assert choch is not None
        assert choch["direction"] == "bearish"

    def test_no_choch_in_bullish_without_ll(self):
        bars = make_bars(20)
        ms = MarketStructure(structure_state="bullish", lower_lows=[])
        choch = detect_choch(bars, ms)
        assert choch is None

    def test_choch_bullish_in_bearish_structure(self):
        """In bearish trend, HH forms → CHoCH bullish."""
        bars = make_bars(20)
        ms = MarketStructure(
            structure_state="bearish",
            higher_highs=[swing(1010, 1.1030, "high", 12)],
        )
        choch = detect_choch(bars, ms)
        assert choch is not None
        assert choch["direction"] == "bullish"
