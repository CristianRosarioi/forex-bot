"""Tests unitarios para bot.core.timeframe_buffer."""

import pandas as pd
import pytest
from bot.core.timeframe_buffer import TimeframeBuffer


def _make_bars(start_ts: int, count: int) -> pd.DataFrame:
    """Genera un DataFrame de velas sintéticas."""
    return pd.DataFrame(
        [
            {
                "time": start_ts + i * 900,  # 15 min en segundos
                "open": 1.1000 + i * 0.0001,
                "high": 1.1010 + i * 0.0001,
                "low": 1.0990 + i * 0.0001,
                "close": 1.1005 + i * 0.0001,
                "tick_volume": 100 + i,
            }
            for i in range(count)
        ]
    )


class TestTimeframeBufferUpdate:
    def test_update_stores_bars(self):
        buf = TimeframeBuffer()
        bars = _make_bars(1_000_000, 10)
        buf.update("EURUSD", "M15", bars)
        assert buf.has_data("EURUSD", "M15")

    def test_get_returns_correct_count(self):
        buf = TimeframeBuffer()
        bars = _make_bars(1_000_000, 50)
        buf.update("EURUSD", "M15", bars)
        result = buf.get("EURUSD", "M15", count=10)
        assert len(result) == 10

    def test_get_without_count_returns_all(self):
        buf = TimeframeBuffer()
        bars = _make_bars(1_000_000, 30)
        buf.update("EURUSD", "M15", bars)
        result = buf.get("EURUSD", "M15")
        assert len(result) == 30

    def test_duplicate_timestamps_not_added(self):
        buf = TimeframeBuffer()
        bars = _make_bars(1_000_000, 10)
        buf.update("EURUSD", "M15", bars)
        buf.update("EURUSD", "M15", bars)  # mismos datos
        result = buf.get("EURUSD", "M15")
        assert len(result) == 10  # no duplicados

    def test_new_bars_appended(self):
        buf = TimeframeBuffer()
        bars_a = _make_bars(1_000_000, 10)
        bars_b = _make_bars(1_000_000 + 10 * 900, 5)  # continúan desde donde terminó A
        buf.update("EURUSD", "M15", bars_a)
        buf.update("EURUSD", "M15", bars_b)
        result = buf.get("EURUSD", "M15")
        assert len(result) == 15

    def test_max_bars_respected(self):
        buf = TimeframeBuffer(max_bars=20)
        # Cargar 30 barras en dos lotes para que el deque las descarte
        buf.update("EURUSD", "M15", _make_bars(1_000_000, 20))
        buf.update("EURUSD", "M15", _make_bars(1_000_000 + 20 * 900, 10))
        result = buf.get("EURUSD", "M15")
        assert len(result) == 20


class TestTimeframeBufferGetLatest:
    def test_get_latest_returns_last_bar(self):
        buf = TimeframeBuffer()
        bars = _make_bars(1_000_000, 5)
        buf.update("EURUSD", "M15", bars)
        latest = buf.get_latest("EURUSD", "M15")
        assert latest is not None
        assert latest["time"] == 1_000_000 + 4 * 900

    def test_get_latest_returns_none_when_empty(self):
        buf = TimeframeBuffer()
        assert buf.get_latest("EURUSD", "M15") is None

    def test_get_latest_is_dict(self):
        buf = TimeframeBuffer()
        bars = _make_bars(1_000_000, 3)
        buf.update("EURUSD", "M15", bars)
        latest = buf.get_latest("EURUSD", "M15")
        assert isinstance(latest, dict)
        assert "open" in latest and "close" in latest


class TestTimeframeBufferHasData:
    def test_has_data_false_initially(self):
        buf = TimeframeBuffer()
        assert not buf.has_data("EURUSD", "M15")

    def test_has_data_true_after_update(self):
        buf = TimeframeBuffer()
        buf.update("EURUSD", "M15", _make_bars(1_000_000, 1))
        assert buf.has_data("EURUSD", "M15")

    def test_different_keys_are_independent(self):
        buf = TimeframeBuffer()
        buf.update("EURUSD", "M15", _make_bars(1_000_000, 5))
        assert buf.has_data("EURUSD", "M15")
        assert not buf.has_data("GBPUSD", "M15")
        assert not buf.has_data("EURUSD", "H1")


class TestTimeframeBufferGetEmpty:
    def test_get_empty_returns_empty_dataframe(self):
        buf = TimeframeBuffer()
        result = buf.get("UNKNOWN", "M1")
        assert isinstance(result, pd.DataFrame)
        assert result.empty
