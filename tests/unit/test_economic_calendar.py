"""Tests unitarios para bot/infra/economic_calendar.py — EconomicCalendar."""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bot.core.event_bus import EventBus
from bot.infra.economic_calendar import EconomicCalendar, EconomicEvent


def make_calendar(events: list[EconomicEvent] | None = None) -> EconomicCalendar:
    bus = EventBus()
    cal = EconomicCalendar(event_bus=bus)
    if events is not None:
        cal._events = events
    return cal


def high_event(event_time: datetime, currency: str = "USD",
               title: str = "NFP") -> EconomicEvent:
    return EconomicEvent(
        event_time=event_time,
        currency=currency,
        impact="High",  # will be lowercased in __init__
        title=title,
    )


def low_event(event_time: datetime, currency: str = "USD") -> EconomicEvent:
    return EconomicEvent(
        event_time=event_time,
        currency=currency,
        impact="Low",
        title="Some Low Event",
    )


# Reference time: 2024-01-15 14:00 UTC
_BASE_TIME = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
# Event at 14:30 UTC
_EVENT_AT_14_30 = datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)


class TestIsBlockedWindow:
    def test_is_blocked_window_before_event_29min(self):
        """29 minutes before event = inside block window (30 min window)."""
        event = high_event(_EVENT_AT_14_30)
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=29)
        assert cal.is_blocked_window("USD", check_time) is True

    def test_is_blocked_window_before_event_exactly_30min(self):
        """Exactly 30 minutes before event = blocked (boundary inclusive)."""
        event = high_event(_EVENT_AT_14_30)
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=30)
        assert cal.is_blocked_window("USD", check_time) is True

    def test_not_blocked_before_window_31min(self):
        """31 minutes before event = outside block window."""
        event = high_event(_EVENT_AT_14_30)
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=31)
        assert cal.is_blocked_window("USD", check_time) is False

    def test_is_blocked_window_after_event_30min(self):
        """30 minutes after event = still blocked (60 min after window)."""
        event = high_event(_EVENT_AT_14_30)
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 + timedelta(minutes=30)
        assert cal.is_blocked_window("USD", check_time) is True

    def test_is_blocked_window_after_event_exactly_60min(self):
        """Exactly 60 minutes after event = blocked (boundary inclusive)."""
        event = high_event(_EVENT_AT_14_30)
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 + timedelta(minutes=60)
        assert cal.is_blocked_window("USD", check_time) is True

    def test_not_blocked_after_window_61min(self):
        """61 minutes after event = outside block window."""
        event = high_event(_EVENT_AT_14_30)
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 + timedelta(minutes=61)
        assert cal.is_blocked_window("USD", check_time) is False

    def test_low_impact_not_blocked(self):
        """Low impact events should NOT trigger blocking."""
        event = low_event(_EVENT_AT_14_30, currency="USD")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_window("USD", check_time) is False

    def test_different_currency_not_blocked(self):
        """Event for EUR should not block USD."""
        event = high_event(_EVENT_AT_14_30, currency="EUR")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_window("USD", check_time) is False

    def test_same_currency_blocked(self):
        """Event for USD blocks USD window."""
        event = high_event(_EVENT_AT_14_30, currency="USD")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_window("USD", check_time) is True


class TestIsBlockedForSymbol:
    def test_is_blocked_for_symbol_eurusd_usd_event(self):
        """EURUSD is blocked if USD or EUR has a high impact event."""
        event = high_event(_EVENT_AT_14_30, currency="USD")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_for_symbol("EURUSD", check_time) is True

    def test_is_blocked_for_symbol_eurusd_eur_event(self):
        """EURUSD also blocked by EUR event."""
        event = high_event(_EVENT_AT_14_30, currency="EUR")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_for_symbol("EURUSD", check_time) is True

    def test_symbol_not_blocked_by_unrelated_currency(self):
        """EURUSD not blocked by JPY event."""
        event = high_event(_EVENT_AT_14_30, currency="JPY")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_for_symbol("EURUSD", check_time) is False

    def test_usdjpy_blocked_by_jpy_event(self):
        """USDJPY blocked by JPY event."""
        event = high_event(_EVENT_AT_14_30, currency="JPY")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_for_symbol("USDJPY", check_time) is True

    def test_unknown_symbol_not_blocked(self):
        """Symbol not in mapping should not be blocked."""
        event = high_event(_EVENT_AT_14_30, currency="USD")
        cal = make_calendar([event])
        check_time = _EVENT_AT_14_30 - timedelta(minutes=10)
        assert cal.is_blocked_for_symbol("XAUUSD", check_time) is False

    def test_empty_events_not_blocked(self):
        """No events should never block."""
        cal = make_calendar([])
        assert cal.is_blocked_for_symbol("EURUSD") is False
        assert cal.is_blocked_window("USD") is False


class TestXmlParsing:
    _SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<weeklyevents>
    <event>
        <title>Nonfarm Payrolls</title>
        <country>USD</country>
        <date>January 17, 2025</date>
        <time>8:30am</time>
        <impact>High</impact>
        <forecast>200K</forecast>
        <previous>220K</previous>
        <actual></actual>
    </event>
    <event>
        <title>GDP q/q</title>
        <country>EUR</country>
        <date>January 20, 2025</date>
        <time>2:00pm</time>
        <impact>Medium</impact>
        <forecast>0.2%</forecast>
        <previous>0.1%</previous>
        <actual>0.3%</actual>
    </event>
    <event>
        <title>Bank Holiday</title>
        <country>GBP</country>
        <date>January 21, 2025</date>
        <time>All Day</time>
        <impact>High</impact>
        <forecast></forecast>
        <previous></previous>
        <actual></actual>
    </event>
</weeklyevents>
"""

    def test_xml_parsing_returns_events(self):
        cal = make_calendar()
        events = cal._parse_xml(self._SAMPLE_XML)
        # At least the first two events with parseable dates
        assert len(events) >= 2

    def test_xml_parsing_nfp_event(self):
        cal = make_calendar()
        events = cal._parse_xml(self._SAMPLE_XML)
        nfp = next((e for e in events if "Nonfarm" in e.title), None)
        assert nfp is not None
        assert nfp.currency == "USD"
        assert nfp.impact == "high"
        assert nfp.event_time.year == 2025

    def test_xml_parsing_impact_lowercased(self):
        cal = make_calendar()
        events = cal._parse_xml(self._SAMPLE_XML)
        for event in events:
            assert event.impact == event.impact.lower()

    def test_xml_parsing_invalid_xml(self):
        cal = make_calendar()
        events = cal._parse_xml("<invalid>xml</broken>")
        assert events == []

    def test_xml_parsing_empty_xml(self):
        cal = make_calendar()
        events = cal._parse_xml("<weeklyevents></weeklyevents>")
        assert events == []


class TestGetUpcomingEvents:
    def test_get_upcoming_events_within_window(self):
        now = datetime.now(timezone.utc)
        future_event = high_event(now + timedelta(minutes=30))
        cal = make_calendar([future_event])
        upcoming = cal.get_upcoming_events(within_minutes=60)
        assert len(upcoming) == 1

    def test_get_upcoming_events_outside_window(self):
        now = datetime.now(timezone.utc)
        far_future = high_event(now + timedelta(hours=5))
        cal = make_calendar([far_future])
        upcoming = cal.get_upcoming_events(within_minutes=60)
        assert len(upcoming) == 0

    def test_get_upcoming_events_past_events_excluded(self):
        past_event = high_event(datetime.now(timezone.utc) - timedelta(hours=1))
        cal = make_calendar([past_event])
        upcoming = cal.get_upcoming_events(within_minutes=60)
        assert len(upcoming) == 0

    def test_get_upcoming_events_low_impact_excluded(self):
        now = datetime.now(timezone.utc)
        low = low_event(now + timedelta(minutes=10))
        cal = make_calendar([low])
        upcoming = cal.get_upcoming_events(within_minutes=60)
        assert len(upcoming) == 0
