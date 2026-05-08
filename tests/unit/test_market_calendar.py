"""Tests unitarios para bot/core/scheduler.py — MarketCalendar."""

from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from bot.core.scheduler import MarketCalendar


def make_calendar(symbol_config: dict | None = None) -> MarketCalendar:
    """Creates a MarketCalendar with mocked connector and optionally overridden symbol config."""
    connector = MagicMock()
    cal = MarketCalendar(connector=connector)
    if symbol_config is not None:
        cal._symbol_config = symbol_config
    return cal


def dt_utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestIsWeekend:
    def test_is_weekend_saturday(self):
        # 2024-01-06 was a Saturday
        sat = dt_utc(2024, 1, 6, 12, 0)
        cal = make_calendar()
        assert cal.is_weekend(sat) is True

    def test_is_weekend_sunday_before_22(self):
        # Sunday before 22:00 UTC is still weekend
        sun = dt_utc(2024, 1, 7, 20, 0)
        cal = make_calendar()
        assert cal.is_weekend(sun) is True

    def test_is_weekend_sunday_at_21(self):
        # 21:00 UTC on Sunday is still weekend
        sun = dt_utc(2024, 1, 7, 21, 0)
        cal = make_calendar()
        assert cal.is_weekend(sun) is True

    def test_is_weekend_sunday_after_22_market_opens(self):
        # Sunday at 22:00 UTC = market open, not weekend
        sun = dt_utc(2024, 1, 7, 22, 0)
        cal = make_calendar()
        assert cal.is_weekend(sun) is False

    def test_is_not_weekend_monday(self):
        # 2024-01-08 was a Monday
        mon = dt_utc(2024, 1, 8, 9, 0)
        cal = make_calendar()
        assert cal.is_weekend(mon) is False

    def test_is_not_weekend_friday(self):
        # 2024-01-05 was a Friday
        fri = dt_utc(2024, 1, 5, 18, 0)
        cal = make_calendar()
        assert cal.is_weekend(fri) is False


class TestGetActiveSession:
    def test_get_active_session_tokyo(self):
        # 01:00 UTC = Tokyo session (0-9)
        t = dt_utc(2024, 1, 8, 1, 0)
        cal = make_calendar()
        session = cal.get_active_session(t)
        assert session == "tokyo"

    def test_get_active_session_london(self):
        # 10:00 UTC = London (7-16), not overlap (12-16)
        t = dt_utc(2024, 1, 8, 10, 0)
        cal = make_calendar()
        session = cal.get_active_session(t)
        assert session == "london"

    def test_get_active_session_overlap(self):
        # 13:00 UTC = Overlap (12-16) — most specific match
        t = dt_utc(2024, 1, 8, 13, 0)
        cal = make_calendar()
        session = cal.get_active_session(t)
        assert session == "overlap"

    def test_get_active_session_new_york(self):
        # 18:00 UTC = New York (12-21), after overlap ends (16)
        t = dt_utc(2024, 1, 8, 18, 0)
        cal = make_calendar()
        session = cal.get_active_session(t)
        assert session == "new_york"

    def test_get_active_session_none(self):
        # 23:00 UTC = no active session
        t = dt_utc(2024, 1, 8, 23, 0)
        cal = make_calendar()
        session = cal.get_active_session(t)
        assert session is None

    def test_get_active_session_boundary_tokyo_start(self):
        # 00:00 UTC = start of Tokyo
        t = dt_utc(2024, 1, 8, 0, 0)
        cal = make_calendar()
        session = cal.get_active_session(t)
        assert session == "tokyo"

    def test_get_active_session_boundary_london_start(self):
        # 07:00 UTC = start of London
        t = dt_utc(2024, 1, 8, 7, 0)
        cal = make_calendar()
        session = cal.get_active_session(t)
        assert session == "london"


class TestSymbolAllowedInCurrentSession:
    def _make_eurusd_calendar(self) -> MarketCalendar:
        return make_calendar(symbol_config={
            "EURUSD": {"sessions": ["london", "new_york", "overlap"]},
            "USDJPY": {"sessions": ["tokyo", "new_york", "overlap"]},
        })

    def test_symbol_allowed_eurusd_london(self):
        # 10:00 UTC = London session, EURUSD allowed in london
        t = dt_utc(2024, 1, 8, 10, 0)
        cal = self._make_eurusd_calendar()
        assert cal.symbol_allowed_in_current_session("EURUSD", t) is True

    def test_symbol_not_allowed_eurusd_tokyo(self):
        # 02:00 UTC = Tokyo session, EURUSD not in tokyo
        t = dt_utc(2024, 1, 8, 2, 0)
        cal = self._make_eurusd_calendar()
        assert cal.symbol_allowed_in_current_session("EURUSD", t) is False

    def test_symbol_allowed_usdjpy_tokyo(self):
        # 03:00 UTC = Tokyo session, USDJPY allowed in tokyo
        t = dt_utc(2024, 1, 8, 3, 0)
        cal = self._make_eurusd_calendar()
        assert cal.symbol_allowed_in_current_session("USDJPY", t) is True

    def test_symbol_not_allowed_when_no_session(self):
        # 23:00 UTC = no session → not allowed
        t = dt_utc(2024, 1, 8, 23, 0)
        cal = self._make_eurusd_calendar()
        assert cal.symbol_allowed_in_current_session("EURUSD", t) is False

    def test_unknown_symbol_not_allowed(self):
        # Symbol not in config → no sessions → not allowed
        t = dt_utc(2024, 1, 8, 10, 0)
        cal = make_calendar(symbol_config={})
        assert cal.symbol_allowed_in_current_session("XAUUSD", t) is False

    def test_eurusd_allowed_in_overlap(self):
        # 13:00 UTC = overlap session, EURUSD allowed in overlap
        t = dt_utc(2024, 1, 8, 13, 0)
        cal = self._make_eurusd_calendar()
        assert cal.symbol_allowed_in_current_session("EURUSD", t) is True


class TestNextMarketTimes:
    def test_next_market_open_returns_future(self):
        cal = make_calendar()
        next_open = cal.next_market_open("EURUSD")
        assert next_open > datetime.now(timezone.utc)
        assert next_open.weekday() == 0  # Monday
        assert next_open.hour == 0

    def test_next_market_close_returns_future(self):
        cal = make_calendar()
        next_close = cal.next_market_close("EURUSD")
        assert next_close > datetime.now(timezone.utc)
        assert next_close.weekday() == 4  # Friday
        assert next_close.hour == 21
