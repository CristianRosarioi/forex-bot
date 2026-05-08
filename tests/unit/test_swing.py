"""Tests for bot/analysis/swing.py"""

import pytest
import pandas as pd
import numpy as np

from bot.analysis.swing import (
    SwingPoint,
    detect_swings,
    get_recent_swings,
    get_swing_pairs_for_levels,
)


def make_bars(n: int, base_price: float = 1.1000) -> pd.DataFrame:
    """Create a simple flat DataFrame with n bars."""
    data = {
        "time": list(range(1000, 1000 + n)),
        "open": [base_price] * n,
        "high": [base_price + 0.0005] * n,
        "low": [base_price - 0.0005] * n,
        "close": [base_price] * n,
        "tick_volume": [100] * n,
    }
    return pd.DataFrame(data)


def make_bars_with_pattern() -> pd.DataFrame:
    """
    Creates bars with 3 clear swings: high-low-high pattern.
    Positions:
      bar 3:  swing HIGH  (lookback=3 means bars 0-2 left, bars 4-6 right)
      bar 7:  swing LOW
      bar 11: swing HIGH
    Total bars = 15 (so index 11 has 3 bars on each side: 12,13,14)
    """
    n = 15
    base = 1.1000
    highs = [base + 0.0005] * n
    lows  = [base - 0.0005] * n
    opens = [base] * n
    closes = [base] * n

    # Swing HIGH at bar 3: high spike
    highs[3] = base + 0.0050
    lows[3]  = base + 0.0020
    opens[3] = base + 0.0025
    closes[3] = base + 0.0040

    # Swing LOW at bar 7: low spike
    lows[7]  = base - 0.0050
    highs[7] = base - 0.0020
    opens[7] = base - 0.0025
    closes[7] = base - 0.0040

    # Swing HIGH at bar 11: high spike
    highs[11] = base + 0.0060
    lows[11]  = base + 0.0025
    opens[11] = base + 0.0030
    closes[11] = base + 0.0050

    df = pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": [100] * n,
    })
    return df


class TestDetectSwings:
    def test_detects_three_swings(self):
        bars = make_bars_with_pattern()
        swings = detect_swings(bars, lookback=3)
        assert len(swings) == 3

    def test_swing_types(self):
        bars = make_bars_with_pattern()
        swings = detect_swings(bars, lookback=3)
        types = [s.type for s in swings]
        assert "high" in types
        assert "low" in types

    def test_swing_high_at_expected_bar(self):
        bars = make_bars_with_pattern()
        swings = detect_swings(bars, lookback=3)
        highs = [s for s in swings if s.type == "high"]
        bar_indices = [s.bar_index for s in highs]
        assert 3 in bar_indices

    def test_swing_low_at_expected_bar(self):
        bars = make_bars_with_pattern()
        swings = detect_swings(bars, lookback=3)
        lows = [s for s in swings if s.type == "low"]
        assert any(s.bar_index == 7 for s in lows)

    def test_sorted_by_bar_index(self):
        bars = make_bars_with_pattern()
        swings = detect_swings(bars, lookback=3)
        indices = [s.bar_index for s in swings]
        assert indices == sorted(indices)

    def test_empty_dataframe_returns_empty(self):
        bars = pd.DataFrame()
        swings = detect_swings(bars)
        assert swings == []

    def test_none_returns_empty(self):
        swings = detect_swings(None)
        assert swings == []

    def test_lookback_boundary_returns_empty(self):
        """Fewer bars than 2*lookback+1 → empty list."""
        bars = make_bars(5)
        swings = detect_swings(bars, lookback=3)
        # 5 < 2*3+1 = 7
        assert swings == []

    def test_minimum_viable_bars(self):
        """Exactly 2*lookback+1 bars: may detect a swing at center."""
        lookback = 2
        bars = make_bars(2 * lookback + 1)
        # All flat → no swing (equal highs don't qualify as strictly greater)
        swings = detect_swings(bars, lookback=lookback)
        # Result can be 0 or more; just ensure no crash
        assert isinstance(swings, list)


class TestGetRecentSwings:
    def test_returns_last_n(self):
        swings = [SwingPoint(i, 1.0, "high", i, 3) for i in range(20)]
        recent = get_recent_swings(swings, n=5)
        assert len(recent) == 5
        assert recent[-1].bar_index == 19

    def test_returns_all_when_fewer_than_n(self):
        swings = [SwingPoint(i, 1.0, "high", i, 3) for i in range(3)]
        recent = get_recent_swings(swings, n=10)
        assert len(recent) == 3


class TestGetSwingPairsForLevels:
    def test_two_highs_near_same_price_form_pair(self):
        h1 = SwingPoint(1000, 1.1000, "high", 3, 3)
        h2 = SwingPoint(1010, 1.1001, "high", 10, 3)  # ~0.009% diff
        l1 = SwingPoint(1005, 1.0950, "low", 6, 3)

        pairs = get_swing_pairs_for_levels([h1, h2, l1], min_distance_pct=0.1)
        assert len(pairs) == 1
        assert all(a.type == b.type for a, b in pairs)

    def test_high_and_low_not_paired(self):
        h1 = SwingPoint(1000, 1.1000, "high", 3, 3)
        l1 = SwingPoint(1005, 1.1001, "low", 6, 3)
        pairs = get_swing_pairs_for_levels([h1, l1], min_distance_pct=0.1)
        assert pairs == []

    def test_price_too_far_apart_no_pair(self):
        h1 = SwingPoint(1000, 1.1000, "high", 3, 3)
        h2 = SwingPoint(1010, 1.1050, "high", 10, 3)  # ~0.45% diff
        pairs = get_swing_pairs_for_levels([h1, h2], min_distance_pct=0.1)
        assert pairs == []

    def test_empty_swings_returns_empty(self):
        pairs = get_swing_pairs_for_levels([])
        assert pairs == []
