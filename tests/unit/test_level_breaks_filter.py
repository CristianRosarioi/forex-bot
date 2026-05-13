"""Tests for max-breaks filter and flip-expiry in LevelManager."""

import pandas as pd
import pytest

from bot.analysis.levels import Level, LevelManager, MAX_BREAKS_FOR_VALID_LEVEL, MAX_FLIP_AGE_BARS


def _level(
    level_id: str,
    price: float,
    n_touches: int,
    is_broken: bool = True,
    flip_active: bool = True,
    broken_at: int = 1000,
) -> Level:
    touches = list(range(1000, 1000 + n_touches))
    return Level(
        id=level_id,
        symbol="EURUSD",
        timeframe="M15",
        price=price,
        type="resistance",
        touches=touches,
        created_at=1000,
        last_touch_at=touches[-1],
        is_broken=is_broken,
        broken_at=broken_at if is_broken else None,
        flip_active=flip_active,
        confidence=0.8,
    )


def _bars(n: int = 20, start_ts: int = 1000, base: float = 1.1000) -> pd.DataFrame:
    return pd.DataFrame({
        "time": list(range(start_ts, start_ts + n)),
        "open":  [base] * n,
        "high":  [base + 0.0005] * n,
        "low":   [base - 0.0005] * n,
        "close": [base] * n,
        "tick_volume": [100] * n,
    })


class TestLevelBreaksFilter:
    def test_level_with_8_touches_is_filtered(self):
        """Level with MAX_BREAKS_FOR_VALID_LEVEL + 1 touches must not appear in get_active_levels."""
        mgr = LevelManager()
        level = _level("lvl-8", price=1.1020, n_touches=MAX_BREAKS_FOR_VALID_LEVEL + 1)
        assert len(level.touches) == 8

        mgr._levels["EURUSD_M15"] = [level]
        active = mgr.get_active_levels("EURUSD", "M15")
        assert all(lv.id != "lvl-8" for lv in active)

    def test_level_with_7_touches_is_filtered(self):
        """Level with exactly MAX_BREAKS_FOR_VALID_LEVEL (7) touches must also be filtered (>=)."""
        mgr = LevelManager()
        level = _level("lvl-7", price=1.1020, n_touches=MAX_BREAKS_FOR_VALID_LEVEL)
        assert len(level.touches) == MAX_BREAKS_FOR_VALID_LEVEL

        mgr._levels["EURUSD_M15"] = [level]
        active = mgr.get_active_levels("EURUSD", "M15")
        assert all(lv.id != "lvl-7" for lv in active)

    def test_level_with_6_touches_passes(self):
        """Level with MAX_BREAKS_FOR_VALID_LEVEL - 1 (6) touches must pass the filter."""
        mgr = LevelManager()
        level = _level("lvl-6", price=1.1020, n_touches=MAX_BREAKS_FOR_VALID_LEVEL - 1)
        assert len(level.touches) == 6

        mgr._levels["EURUSD_M15"] = [level]
        active = mgr.get_active_levels("EURUSD", "M15")
        assert any(lv.id == "lvl-6" for lv in active)

    def test_flip_expired_level_removed_by_update(self):
        """flip_active level with broken_at > MAX_FLIP_AGE_BARS bars ago is removed in update_levels."""
        mgr = LevelManager()
        # broken_at = ts 1000; bars run from ts 1000 to ts 1059 (60 bars → 59 bars since break)
        n = 60
        bars = _bars(n=n, start_ts=1000)
        level = _level("lvl-expired", price=1.1020, n_touches=3, is_broken=True,
                        flip_active=True, broken_at=1000)
        mgr._levels["EURUSD_M15"] = [level]

        mgr.update_levels(bars, "EURUSD", "M15")
        active = mgr.get_active_levels("EURUSD", "M15")
        assert all(lv.id != "lvl-expired" for lv in active)

    def test_flip_not_expired_within_limit_survives(self):
        """flip_active level broken only MAX_FLIP_AGE_BARS - 1 bars ago survives update_levels."""
        mgr = LevelManager()
        # broken_at = ts 1010; bars run from 1000 to 1059 → 59 - 10 = 49 bars since break (<= 50)
        n = 60
        bars = _bars(n=n, start_ts=1000)
        level = _level("lvl-fresh", price=1.1020, n_touches=3, is_broken=True,
                        flip_active=True, broken_at=1010)
        mgr._levels["EURUSD_M15"] = [level]

        mgr.update_levels(bars, "EURUSD", "M15")
        active = mgr.get_active_levels("EURUSD", "M15")
        assert any(lv.id == "lvl-fresh" for lv in active)
