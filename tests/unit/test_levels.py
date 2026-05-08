"""Tests for bot/analysis/levels.py"""

import pytest
import pandas as pd

from bot.analysis.swing import SwingPoint
from bot.analysis.levels import Level, LevelManager


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


class TestLevelManagerDetect:
    def test_two_similar_highs_form_resistance(self):
        mgr = LevelManager()
        bars = make_bars(20, base=1.1000)

        # Two swing highs at nearly the same price
        swings = [
            swing(1003, 1.1050, "high", 3),
            swing(1010, 1.1051, "high", 10),  # very close
            swing(1006, 1.0950, "low", 6),
        ]
        levels = mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        resistances = [lv for lv in levels if lv.type == "resistance"]
        assert len(resistances) >= 1
        # Should have at least 2 touches
        r = resistances[0]
        assert len(r.touches) >= 2

    def test_two_similar_lows_form_support(self):
        mgr = LevelManager()
        bars = make_bars(20, base=1.1000)

        swings = [
            swing(1003, 1.0950, "low", 3),
            swing(1010, 1.0951, "low", 10),
        ]
        levels = mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        supports = [lv for lv in levels if lv.type == "support"]
        assert len(supports) >= 1

    def test_no_duplicate_levels(self):
        """Calling detect_levels twice should not double the levels."""
        mgr = LevelManager()
        bars = make_bars(20, base=1.1000)
        swings = [
            swing(1003, 1.1050, "high", 3),
            swing(1010, 1.1051, "high", 10),
        ]
        mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)
        mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        active = mgr.get_active_levels("EURUSD", "M15")
        # Should not have duplicates at the same price
        prices = [lv.price for lv in active if lv.type == "resistance"]
        assert len(prices) == len(set(round(p, 4) for p in prices))


class TestLevelManagerUpdate:
    def test_level_broken_when_close_above_resistance(self):
        mgr = LevelManager()
        bars_init = make_bars(20, base=1.1000)
        swings = [
            swing(1003, 1.1020, "high", 3),
            swing(1010, 1.1021, "high", 10),
        ]
        mgr.detect_levels(bars_init, swings, "EURUSD", "M15", tolerance_pct=0.05)

        # Create bars where price closes above the resistance level
        level_price = 1.1020  # approximately
        n = 22
        close_prices = [1.1000] * 21 + [1.1050]  # last bar breaks above
        bars_break = pd.DataFrame({
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * n,
            "high": close_prices,
            "low": [1.0990] * n,
            "close": close_prices,
            "tick_volume": [100] * n,
        })
        mgr.update_levels(bars_break, "EURUSD", "M15")

        active = mgr.get_active_levels("EURUSD", "M15")
        broken = [lv for lv in active if lv.is_broken and lv.flip_active]
        assert len(broken) >= 1

    def test_flip_active_after_break(self):
        mgr = LevelManager()
        bars_init = make_bars(20, base=1.1000)
        swings = [
            swing(1003, 1.1020, "high", 3),
            swing(1010, 1.1021, "high", 10),
        ]
        mgr.detect_levels(bars_init, swings, "EURUSD", "M15", tolerance_pct=0.05)

        n = 22
        bars_break = pd.DataFrame({
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * n,
            "high": [1.1000] * 21 + [1.1060],
            "low": [1.0990] * n,
            "close": [1.1000] * 21 + [1.1060],
            "tick_volume": [100] * n,
        })
        mgr.update_levels(bars_break, "EURUSD", "M15")

        all_levels = mgr._levels.get("EURUSD_M15", [])
        flip_levels = [lv for lv in all_levels if lv.flip_active]
        assert len(flip_levels) >= 1

    def test_level_removed_after_max_age(self):
        mgr = LevelManager(max_age_bars=5)
        # Create bars where the level was touched early
        n = 20
        bars = pd.DataFrame({
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * n,
            "high": [1.1005] * n,
            "low": [1.0995] * n,
            "close": [1.1000] * n,
            "tick_volume": [100] * n,
        })
        swings = [
            swing(1000, 1.1005, "high", 0),
            swing(1001, 1.1005, "high", 1),
        ]
        mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        # Extend bars far beyond max_age_bars
        n2 = 30
        bars_old = pd.DataFrame({
            "time": list(range(1000, 1000 + n2)),
            "open": [1.0980] * n2,
            "high": [1.0982] * n2,
            "low": [1.0978] * n2,
            "close": [1.0980] * n2,
            "tick_volume": [100] * n2,
        })
        mgr.update_levels(bars_old, "EURUSD", "M15")

        active = mgr.get_active_levels("EURUSD", "M15")
        # Level should have been removed due to age
        assert len(active) == 0


class TestGetNearestLevel:
    def test_nearest_above(self):
        mgr = LevelManager()
        bars = make_bars(20, base=1.1000)
        swings = [
            swing(1003, 1.1050, "high", 3),
            swing(1010, 1.1051, "high", 10),
        ]
        mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        nearest = mgr.get_nearest_level("EURUSD", "M15", price=1.1000, direction="above")
        assert nearest is not None
        assert nearest.price > 1.1000

    def test_nearest_below(self):
        mgr = LevelManager()
        bars = make_bars(20, base=1.1000)
        swings = [
            swing(1003, 1.0950, "low", 3),
            swing(1010, 1.0951, "low", 10),
        ]
        mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        nearest = mgr.get_nearest_level("EURUSD", "M15", price=1.1000, direction="below")
        assert nearest is not None
        assert nearest.price < 1.1000

    def test_no_level_returns_none(self):
        mgr = LevelManager()
        result = mgr.get_nearest_level("EURUSD", "M15", price=1.1000, direction="above")
        assert result is None
