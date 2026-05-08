"""Tests for bot/analysis/trend.py"""

import pytest
import pandas as pd

from bot.analysis.trend import TrendBias, analyze_trend, is_aligned_with_trend


def make_bullish_bars() -> pd.DataFrame:
    """Creates bars with clear HH/HL structure (bullish)."""
    n = 40
    prices_high = [1.1005] * n
    prices_low = [1.0995] * n

    # Wave 1
    prices_low[3] = 1.0970
    prices_high[10] = 1.1030
    # Wave 2: HL, HH
    prices_low[17] = 1.0980
    prices_high[24] = 1.1040
    # Wave 3: HL, HH
    prices_low[31] = 1.0990
    prices_high[37] = 1.1050

    return pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open": [1.1000] * n,
        "high": prices_high,
        "low": prices_low,
        "close": [1.1000] * n,
        "tick_volume": [100] * n,
    })


def make_bearish_bars() -> pd.DataFrame:
    """Creates bars with clear LH/LL structure (bearish)."""
    n = 40
    prices_high = [1.1005] * n
    prices_low = [1.0995] * n

    # Wave 1
    prices_high[3] = 1.1040
    prices_low[10] = 1.0980
    # Wave 2: LH, LL
    prices_high[17] = 1.1030
    prices_low[24] = 1.0970
    # Wave 3: LH, LL
    prices_high[31] = 1.1020
    prices_low[37] = 1.0960

    return pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open": [1.1000] * n,
        "high": prices_high,
        "low": prices_low,
        "close": [1.1000] * n,
        "tick_volume": [100] * n,
    })


def make_flat_bars(n: int = 10, base: float = 1.1000) -> pd.DataFrame:
    """Flat bars → no detectable swings → ranging."""
    return pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open": [base] * n,
        "high": [base + 0.0005] * n,
        "low": [base - 0.0005] * n,
        "close": [base] * n,
        "tick_volume": [100] * n,
    })


class TestAnalyzeTrend:
    def test_both_bullish(self):
        h4 = make_bullish_bars()
        d1 = make_bullish_bars()
        bias = analyze_trend(h4, d1)
        assert bias.h4_bias == "bullish"
        assert bias.d1_bias == "bullish"
        assert bias.overall == "bullish"
        assert bias.confidence == 1.0

    def test_both_bearish(self):
        h4 = make_bearish_bars()
        d1 = make_bearish_bars()
        bias = analyze_trend(h4, d1)
        assert bias.h4_bias == "bearish"
        assert bias.d1_bias == "bearish"
        assert bias.overall == "bearish"
        assert bias.confidence == 1.0

    def test_h4_bullish_d1_ranging(self):
        h4 = make_bullish_bars()
        d1 = make_flat_bars(10)  # flat → ranging (too few bars for swings)
        bias = analyze_trend(h4, d1)
        assert bias.h4_bias == "bullish"
        assert bias.d1_bias == "ranging"
        assert bias.confidence == 0.5
        assert "ranging" in bias.overall

    def test_h4_bullish_d1_bearish_conflict(self):
        h4 = make_bullish_bars()
        d1 = make_bearish_bars()
        bias = analyze_trend(h4, d1)
        assert bias.h4_bias == "bullish"
        assert bias.d1_bias == "bearish"
        assert bias.confidence == 0.3

    def test_both_ranging(self):
        h4 = make_flat_bars(10)
        d1 = make_flat_bars(10)
        bias = analyze_trend(h4, d1)
        assert bias.overall == "ranging"
        assert bias.confidence == 1.0

    def test_empty_bars_returns_ranging(self):
        bias = analyze_trend(pd.DataFrame(), pd.DataFrame())
        assert bias.h4_bias == "ranging"
        assert bias.d1_bias == "ranging"
        assert bias.overall == "ranging"


class TestIsAlignedWithTrend:
    def test_buy_aligned_bullish(self):
        bias = TrendBias(h4_bias="bullish", d1_bias="bullish", overall="bullish", confidence=1.0)
        assert is_aligned_with_trend("BUY", bias) is True

    def test_sell_aligned_bearish(self):
        bias = TrendBias(h4_bias="bearish", d1_bias="bearish", overall="bearish", confidence=1.0)
        assert is_aligned_with_trend("SELL", bias) is True

    def test_buy_not_aligned_bearish(self):
        bias = TrendBias(h4_bias="bearish", d1_bias="bearish", overall="bearish", confidence=1.0)
        assert is_aligned_with_trend("BUY", bias) is False

    def test_buy_partial_alignment(self):
        """BUY with H4 bullish but D1 ranging → aligned (h4 matches)."""
        bias = TrendBias(h4_bias="bullish", d1_bias="ranging", overall="h4_bullish_d1_ranging", confidence=0.5)
        assert is_aligned_with_trend("BUY", bias, require_overall=False) is True

    def test_require_overall_strict(self):
        """require_overall=True: overall must exactly match."""
        bias = TrendBias(h4_bias="bullish", d1_bias="ranging", overall="h4_bullish_d1_ranging", confidence=0.5)
        assert is_aligned_with_trend("BUY", bias, require_overall=True) is False

    def test_require_overall_bullish_passes(self):
        bias = TrendBias(h4_bias="bullish", d1_bias="bullish", overall="bullish", confidence=1.0)
        assert is_aligned_with_trend("BUY", bias, require_overall=True) is True
