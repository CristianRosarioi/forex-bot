"""Planificación de reportes automáticos: diario al cierre NY, semanal el viernes."""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from bot.analytics.reporter import PerformanceReporter
    from bot.core.event_bus import EventBus

logger = get_logger(__name__)

_DAILY_HOUR = 21
_DAILY_MINUTE = 0
_WEEKLY_HOUR = 21
_WEEKLY_MINUTE = 5

_DAILY_COOLDOWN_HOURS = 23
_WEEKLY_COOLDOWN_DAYS = 6


class ReportScheduler:
    """Envía reportes automáticos a horarios fijos verificando cada 60 segundos."""

    def __init__(self, reporter: "PerformanceReporter", event_bus: "EventBus") -> None:
        self._reporter = reporter
        self._bus = event_bus
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_daily: datetime | None = None
        self._last_weekly: datetime | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop,
            name="report-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("ReportScheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        logger.info("ReportScheduler stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Error in report scheduler tick")
            self._stop_event.wait(timeout=60.0)

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        if self._should_send_daily(now):
            logger.info("Sending daily report")
            self._last_daily = now
            self._reporter.send_daily_report()
        if self._should_send_weekly(now):
            logger.info("Sending weekly report")
            self._last_weekly = now
            self._reporter.send_weekly_report()

    def _should_send_daily(self, now: datetime) -> bool:
        """True si es 21:00-21:01 UTC de lunes a viernes y cooldown expiró."""
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        if now.hour != _DAILY_HOUR or now.minute > 1:
            return False
        if self._last_daily is not None:
            elapsed = (now - self._last_daily).total_seconds()
            if elapsed < _DAILY_COOLDOWN_HOURS * 3600:
                return False
        return True

    def _should_send_weekly(self, now: datetime) -> bool:
        """True si es viernes 21:05-21:06 UTC y cooldown de 6 días expiró."""
        if now.weekday() != 4:  # 4 = Friday
            return False
        if now.hour != _WEEKLY_HOUR or not (_WEEKLY_MINUTE <= now.minute <= _WEEKLY_MINUTE + 1):
            return False
        if self._last_weekly is not None:
            elapsed = (now - self._last_weekly).total_seconds()
            if elapsed < _WEEKLY_COOLDOWN_DAYS * 86400:
                return False
        return True
