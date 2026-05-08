"""Tests for bot/analysis/volume.py"""

import pytest
import pandas as pd

from bot.analysis.volume import VolumeProfile, analyze_volume, is_volume_supportive


def make_bars_with_volumes(volumes: list[float], base: float = 1.1000) -> pd.DataFrame:
    n = len(volumes)
    return pd.DataFrame({
        "time": list(range(1000, 1000 + n)),
        "open": [base] * n,
        "high": [base + 0.0005] * n,
        "low": [base - 0.0005] * n,
        "close": [base] * n,
        "tick_volume": volumes,
    })


class TestAnalyzeVolume:
    def test_high_volume_bar(self):
        """Current bar volume is 2x average → quality=high."""
        volumes = [100.0] * 20 + [200.0]  # 21 bars total
        bars = make_bars_with_volumes(volumes)
        profile = analyze_volume(bars, lookback=20)
        assert profile.quality == "high"
        assert profile.volume_ratio >= 1.5
        assert profile.is_above_average is True

    def test_low_volume_bar(self):
        """Current bar volume is 0.5x average → quality=low."""
        volumes = [100.0] * 20 + [50.0]
        bars = make_bars_with_volumes(volumes)
        profile = analyze_volume(bars, lookback=20)
        assert profile.quality == "low"
        assert profile.volume_ratio < 0.7

    def test_normal_volume_bar(self):
        """Current bar volume is 1.0x average → quality=normal."""
        volumes = [100.0] * 20 + [100.0]
        bars = make_bars_with_volumes(volumes)
        profile = analyze_volume(bars, lookback=20)
        assert profile.quality == "normal"

    def test_insufficient_data_returns_normal(self):
        """Fewer than 2 bars → quality=normal, no crash."""
        bars = make_bars_with_volumes([100.0])
        profile = analyze_volume(bars)
        assert profile.quality == "normal"

    def test_empty_dataframe_returns_normal(self):
        bars = pd.DataFrame()
        profile = analyze_volume(bars)
        assert profile.quality == "normal"

    def test_none_returns_normal(self):
        profile = analyze_volume(None)
        assert profile.quality == "normal"

    def test_current_volume_is_last_bar(self):
        volumes = [100.0] * 20 + [300.0]
        bars = make_bars_with_volumes(volumes)
        profile = analyze_volume(bars, lookback=20)
        assert profile.current_volume == 300.0

    def test_avg_excludes_current_bar(self):
        volumes = [100.0] * 20 + [999.0]  # current bar shouldn't affect avg
        bars = make_bars_with_volumes(volumes)
        profile = analyze_volume(bars, lookback=20)
        assert abs(profile.avg_volume - 100.0) < 1.0


class TestIsVolumeSupportive:
    def test_high_volume_is_supportive(self):
        profile = VolumeProfile(
            avg_volume=100.0, current_volume=200.0,
            volume_ratio=2.0, is_above_average=True, quality="high"
        )
        assert is_volume_supportive(profile) is True

    def test_normal_volume_is_supportive(self):
        profile = VolumeProfile(
            avg_volume=100.0, current_volume=100.0,
            volume_ratio=1.0, is_above_average=True, quality="normal"
        )
        assert is_volume_supportive(profile) is True

    def test_low_volume_is_not_supportive(self):
        profile = VolumeProfile(
            avg_volume=100.0, current_volume=50.0,
            volume_ratio=0.5, is_above_average=False, quality="low"
        )
        assert is_volume_supportive(profile) is False
