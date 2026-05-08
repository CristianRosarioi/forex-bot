"""Consulta del calendario económico (Forex Factory). Bloquea trading 30min antes / 60min después de eventos High."""

from __future__ import annotations
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import httpx

from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from bot.db.repository import BotEventRepository
    from bot.core.event_bus import EventBus

logger = get_logger(__name__)

_FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
_BLOCK_BEFORE_MINUTES = 30
_BLOCK_AFTER_MINUTES = 60
_REFRESH_INTERVAL_HOURS = 12

# Mapeo símbolo -> currencies que le afectan
_SYMBOL_CURRENCIES: dict[str, list[str]] = {
    "EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"], "AUDUSD": ["AUD", "USD"],
    "NZDUSD": ["NZD", "USD"], "USDCAD": ["USD", "CAD"],
    "GBPJPY": ["GBP", "JPY"], "EURGBP": ["EUR", "GBP"],
    "EURJPY": ["EUR", "JPY"], "USDCHF": ["USD", "CHF"],
    "US500":  ["USD"], "NAS100": ["USD"],
}


class EconomicEvent:
    """Representa un evento del calendario económico."""
    __slots__ = ("event_time", "currency", "impact", "title", "forecast", "previous", "actual")

    def __init__(self, event_time: datetime, currency: str, impact: str,
                 title: str, forecast: str = "", previous: str = "", actual: str = "") -> None:
        self.event_time = event_time
        self.currency = currency
        self.impact = impact.lower()
        self.title = title
        self.forecast = forecast
        self.previous = previous
        self.actual = actual


class EconomicCalendar:
    """Calendario económico con feed de Forex Factory.

    Mantiene un cache en memoria y persiste en la DB.
    Refresh automático cada 12h en thread separado.
    """

    def __init__(self, event_bus: "EventBus") -> None:
        self._bus = event_bus
        self._events: list[EconomicEvent] = []
        self._last_fetch: datetime | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    def fetch_this_week(self) -> list[EconomicEvent]:
        """Descarga y parsea el feed XML de Forex Factory.

        Returns:
            Lista de EconomicEvent nuevos. Si falla, retorna lista vacía y usa cache.
        """
        try:
            response = httpx.get(_FF_URL, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
            events = self._parse_xml(response.text)
            with self._lock:
                self._events = events
                self._last_fetch = datetime.now(timezone.utc)
            logger.info("EconomicCalendar: fetched %d events from Forex Factory", len(events))
            return events
        except Exception as exc:
            age_msg = ""
            if self._last_fetch:
                age = (datetime.now(timezone.utc) - self._last_fetch).total_seconds() / 3600
                age_msg = f" (cache age: {age:.1f}h)"
                if age > 168:  # 7 días
                    logger.warning("EconomicCalendar: cache is >7 days old%s", age_msg)
            logger.error("EconomicCalendar: fetch failed: %s%s", exc, age_msg)
            return []

    def _parse_xml(self, xml_text: str) -> list[EconomicEvent]:
        """Parsea el XML de Forex Factory."""
        events = []
        try:
            root = ET.fromstring(xml_text)
            for event_elem in root.findall(".//event"):
                try:
                    title = (event_elem.findtext("title") or "").strip()
                    country = (event_elem.findtext("country") or "").strip().upper()
                    date_str = (event_elem.findtext("date") or "").strip()
                    time_str = (event_elem.findtext("time") or "").strip()
                    impact = (event_elem.findtext("impact") or "").strip().lower()
                    forecast = (event_elem.findtext("forecast") or "").strip()
                    previous = (event_elem.findtext("previous") or "").strip()
                    actual = (event_elem.findtext("actual") or "").strip()

                    if not date_str or not title:
                        continue

                    # Parsear datetime
                    dt_str = f"{date_str} {time_str}".strip()
                    event_time = self._parse_ff_datetime(dt_str)
                    if event_time is None:
                        continue

                    events.append(EconomicEvent(
                        event_time=event_time,
                        currency=country,
                        impact=impact,
                        title=title,
                        forecast=forecast,
                        previous=previous,
                        actual=actual,
                    ))
                except Exception:
                    continue
        except ET.ParseError as exc:
            logger.error("EconomicCalendar: XML parse error: %s", exc)
        return events

    def _parse_ff_datetime(self, dt_str: str) -> datetime | None:
        """Parsea fechas del feed de Forex Factory."""
        # Formatos posibles: "January 15, 2025 10:30am", "January 15, 2025 All Day"
        formats = [
            "%B %d, %Y %I:%M%p",
            "%B %d, %Y %I:%M %p",
            "%m-%d-%Y %I:%M%p",
        ]
        dt_str = dt_str.replace("am", "AM").replace("pm", "PM")
        for fmt in formats:
            try:
                return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        # "All Day" o formato desconocido — usar mediodía UTC del primer token de fecha
        try:
            parts = dt_str.split()
            date_part = " ".join(parts[:3]).rstrip(",")
            return datetime.strptime(date_part, "%B %d %Y").replace(
                hour=12, tzinfo=timezone.utc)
        except Exception:
            return None

    def is_blocked_window(self, currency: str, at_time: datetime | None = None) -> bool:
        """Verifica si estamos en ventana de bloqueo por evento High de esta currency.

        H-05: Si no se ha realizado ningún fetch (_last_fetch is None), retorna True
        como medida fail-closed (no operamos si no tenemos datos del calendario).
        """
        # H-05: fail-closed guard — no data means block all trading
        if self._last_fetch is None:
            return True
        t = at_time or datetime.now(timezone.utc)
        with self._lock:
            events = list(self._events)
        for event in events:
            if event.impact != "high":
                continue
            if event.currency.upper() != currency.upper():
                continue
            before = event.event_time - timedelta(minutes=_BLOCK_BEFORE_MINUTES)
            after = event.event_time + timedelta(minutes=_BLOCK_AFTER_MINUTES)
            if before <= t <= after:
                logger.info("Blocked: %s event '%s' at %s (window: %s – %s)",
                            currency, event.title, event.event_time, before, after)
                return True
        return False

    def is_blocked_for_symbol(self, symbol: str, at_time: datetime | None = None) -> bool:
        """Verifica si el símbolo está bloqueado por algún evento de sus currencies.

        H-05: Si no se ha realizado ningún fetch exitoso, bloquea todo el trading.
        """
        if self._last_fetch is None:
            logger.warning("EconomicCalendar: no successful fetch yet — blocking all trading (fail-safe)")
            return True
        currencies = _SYMBOL_CURRENCIES.get(symbol.upper(), [])
        return any(self.is_blocked_window(c, at_time) for c in currencies)

    def get_upcoming_events(self, within_minutes: int = 60) -> list[EconomicEvent]:
        """Retorna eventos High en los próximos N minutos."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=within_minutes)
        with self._lock:
            return [e for e in self._events
                    if e.impact == "high" and now <= e.event_time <= cutoff]

    def start_refresh_loop(self) -> None:
        """Arranca el thread de refresh automático cada 12h."""
        self._stop_event.clear()
        self._refresh_thread = threading.Thread(
            target=self._loop, name="eco-calendar-refresh", daemon=True)
        self._refresh_thread.start()
        logger.info("EconomicCalendar refresh loop started (every %dh)", _REFRESH_INTERVAL_HOURS)

    def stop_refresh_loop(self) -> None:
        self._stop_event.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=5)

    def _loop(self) -> None:
        self.fetch_this_week()  # fetch inmediato al arrancar
        while not self._stop_event.wait(timeout=_REFRESH_INTERVAL_HOURS * 3600):
            self.fetch_this_week()
