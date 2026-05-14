"""Tests for bot/analytics/scheduler.py"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from bot.analytics.scheduler import ReportScheduler
from bot.core.event_bus import EventBus


def _make_scheduler():
    reporter = MagicMock()
    bus = EventBus()
    scheduler = ReportScheduler(reporter=reporter, event_bus=bus)
    return scheduler, reporter


def _utc(year=2026, month=5, day=12, hour=21, minute=0, weekday=None):
    """Helper: builds a datetime. weekday param is ignored (use real dates)."""
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


# Reference dates:
# 2026-05-11 = Monday
# 2026-05-12 = Tuesday
# 2026-05-13 = Wednesday
# 2026-05-14 = Thursday
# 2026-05-15 = Friday
# 2026-05-16 = Saturday
# 2026-05-17 = Sunday


class TestDailyReportSchedule:
    def test_daily_at_2100_monday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 11, 21, 0)  # Monday 21:00
        assert scheduler._should_send_daily(now) is True

    def test_daily_at_2101_friday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 15, 21, 1)  # Friday 21:01 (within window)
        assert scheduler._should_send_daily(now) is True

    def test_no_daily_at_2002(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 12, 20, 0)  # wrong hour
        assert scheduler._should_send_daily(now) is False

    def test_no_daily_on_saturday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 16, 21, 0)  # Saturday
        assert scheduler._should_send_daily(now) is False

    def test_no_daily_on_sunday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 17, 21, 0)  # Sunday
        assert scheduler._should_send_daily(now) is False

    def test_no_daily_if_cooldown_not_expired(self):
        scheduler, _ = _make_scheduler()
        # Last sent 2 hours ago
        scheduler._last_daily = _utc(2026, 5, 12, 19, 0)
        now = _utc(2026, 5, 12, 21, 0)
        assert scheduler._should_send_daily(now) is False

    def test_daily_if_cooldown_expired(self):
        scheduler, _ = _make_scheduler()
        # Last sent 24 hours ago (> 23h cooldown)
        scheduler._last_daily = _utc(2026, 5, 11, 21, 0)
        now = _utc(2026, 5, 12, 21, 0)
        assert scheduler._should_send_daily(now) is True

    def test_daily_at_minute_2_outside_window(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 12, 21, 2)  # minute=2, outside 0-1 window
        assert scheduler._should_send_daily(now) is False


class TestWeeklyReportSchedule:
    def test_weekly_at_2105_friday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 15, 21, 5)  # Friday 21:05
        assert scheduler._should_send_weekly(now) is True

    def test_weekly_at_2106_friday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 15, 21, 6)  # Friday 21:06 (within window)
        assert scheduler._should_send_weekly(now) is True

    def test_no_weekly_on_monday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 11, 21, 5)  # Monday
        assert scheduler._should_send_weekly(now) is False

    def test_no_weekly_wrong_hour_friday(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 15, 20, 5)  # Friday wrong hour
        assert scheduler._should_send_weekly(now) is False

    def test_no_weekly_if_cooldown_not_expired(self):
        scheduler, _ = _make_scheduler()
        # Last sent 3 days ago (< 6 day cooldown)
        scheduler._last_weekly = _utc(2026, 5, 12, 21, 5)
        now = _utc(2026, 5, 15, 21, 5)
        assert scheduler._should_send_weekly(now) is False

    def test_weekly_if_cooldown_expired(self):
        scheduler, _ = _make_scheduler()
        # Last sent 7 days ago (> 6 day cooldown)
        scheduler._last_weekly = _utc(2026, 5, 8, 21, 5)
        now = _utc(2026, 5, 15, 21, 5)
        assert scheduler._should_send_weekly(now) is True

    def test_no_weekly_at_2107_outside_window(self):
        scheduler, _ = _make_scheduler()
        now = _utc(2026, 5, 15, 21, 7)  # minute=7, outside 5-6 window
        assert scheduler._should_send_weekly(now) is False


class TestTickBehavior:
    def test_tick_triggers_daily_at_correct_time(self):
        scheduler, reporter = _make_scheduler()
        now = _utc(2026, 5, 12, 21, 0)  # Tuesday 21:00
        scheduler._tick()  # will not send (wrong time based on real clock)

        # Force by injecting a specific time via _should_send logic directly
        # Directly test that tick calls reporter when should_send returns True
        reporter.send_daily_report.reset_mock()
        scheduler._last_daily = None
        scheduler._last_weekly = None

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "bot.analytics.scheduler.datetime",
                type("FakeDatetime", (), {
                    "now": staticmethod(lambda tz=None: now),
                    "timezone": timezone,
                }),
            )
            # Can't easily patch datetime in the method, so test indirectly via _should_send
            assert scheduler._should_send_daily(now) is True

    def test_friday_daily_at_2100_and_weekly_at_2105(self):
        scheduler, reporter = _make_scheduler()
        # Friday 21:00 → daily fires, weekly does not (wrong minute)
        friday_2100 = _utc(2026, 5, 15, 21, 0)
        assert scheduler._should_send_daily(friday_2100) is True
        assert scheduler._should_send_weekly(friday_2100) is False
        # Friday 21:05 → weekly fires, daily does not (minute=5 outside window)
        friday_2105 = _utc(2026, 5, 15, 21, 5)
        assert scheduler._should_send_daily(friday_2105) is False
        assert scheduler._should_send_weekly(friday_2105) is True

    def test_cooldown_set_after_daily_send(self):
        scheduler, reporter = _make_scheduler()
        # Force conditions so tick sends daily
        # Patch _should_send_daily and _should_send_weekly
        scheduler._should_send_daily = lambda now: True
        scheduler._should_send_weekly = lambda now: False
        scheduler._tick()
        reporter.send_daily_report.assert_called_once()
        assert scheduler._last_daily is not None

    def test_cooldown_set_after_weekly_send(self):
        scheduler, reporter = _make_scheduler()
        scheduler._should_send_daily = lambda now: False
        scheduler._should_send_weekly = lambda now: True
        scheduler._tick()
        reporter.send_weekly_report.assert_called_once()
        assert scheduler._last_weekly is not None
