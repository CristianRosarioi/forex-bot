"""Planificador de tareas periódicas: cierre de posiciones, reportes, calendario económico."""

from __future__ import annotations
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from bot.core.connector import MT5Connector

logger = get_logger(__name__)

_SYMBOLS_YAML = Path(__file__).parent.parent.parent / "config" / "symbols.yaml"

# Sesiones UTC (hour_start, hour_end)
_SESSIONS = {
    "tokyo":    (0, 9),
    "london":   (7, 16),
    "new_york": (12, 21),
    "overlap":  (12, 16),  # london + new_york
}


class MarketCalendar:
    """Calendario de mercado: sesiones, fines de semana, holidays.

    Cachea resultados de MT5 por 5 minutos para no sobrecargar la API.
    """

    def __init__(self, connector: "MT5Connector") -> None:
        self._connector = connector
        self._cache: dict[str, tuple[float, object]] = {}  # key -> (expires_ts, value)
        self._cache_ttl = 300  # 5 minutos
        self._lock = threading.Lock()
        self._symbol_config = self._load_symbol_config()

    def _load_symbol_config(self) -> dict:
        try:
            with open(_SYMBOLS_YAML, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return cfg.get("symbols", {})
        except Exception:
            logger.exception("Could not load symbols.yaml")
            return {}

    def _cached(self, key: str, fn):
        with self._lock:
            entry = self._cache.get(key)
            now = time.monotonic()
            if entry and entry[0] > now:
                return entry[1]
            value = fn()
            self._cache[key] = (now + self._cache_ttl, value)
            return value

    def is_weekend(self, at_time: datetime | None = None) -> bool:
        """Retorna True si es sábado completo o domingo antes de las 22:00 UTC."""
        t = (at_time or datetime.now(timezone.utc))
        weekday = t.weekday()  # 5=Saturday, 6=Sunday
        if weekday == 5:
            return True
        if weekday == 6 and t.hour < 22:
            return True
        return False

    def get_active_session(self, at_time: datetime | None = None) -> str | None:
        """Retorna la sesión activa más específica o None."""
        t = at_time or datetime.now(timezone.utc)
        hour = t.hour
        # Overlap es más específico que London o NY individualmente
        lo, hi = _SESSIONS["overlap"]
        if lo <= hour < hi:
            return "overlap"
        lo, hi = _SESSIONS["london"]
        if lo <= hour < hi:
            return "london"
        lo, hi = _SESSIONS["new_york"]
        if lo <= hour < hi:
            return "new_york"
        lo, hi = _SESSIONS["tokyo"]
        if lo <= hour < hi:
            return "tokyo"
        return None

    def is_market_open(self, symbol: str, at_time: datetime | None = None) -> bool:
        """Verifica si el mercado está abierto para el símbolo."""
        if self.is_weekend(at_time):
            return False
        try:
            import MetaTrader5 as mt5
            def _check():
                info = mt5.symbol_info(symbol)
                if info is None:
                    return False
                return info.trade_mode != 0  # 0 = disabled
            return self._cached(f"market_open_{symbol}", _check)
        except Exception:
            logger.exception("Could not check market open for %s", symbol)
            return False

    def is_holiday(self, symbol: str, at_time: datetime | None = None) -> bool:
        """Verifica si hoy es holiday para el símbolo consultando MT5."""
        t = at_time or datetime.now(timezone.utc)
        try:
            import MetaTrader5 as mt5
            def _check():
                sessions = mt5.symbol_info_session_trade(symbol, t.weekday())
                if sessions is None or len(sessions) == 0:
                    return True  # sin sesiones = holiday
                return False
            return self._cached(f"holiday_{symbol}_{t.date()}", _check)
        except Exception:
            return False

    def symbol_allowed_in_current_session(self, symbol: str, at_time: datetime | None = None) -> bool:
        """Verifica si el símbolo tiene trading habilitado en la sesión actual."""
        current_session = self.get_active_session(at_time)
        if current_session is None:
            return False
        sym_cfg = self._symbol_config.get(symbol, {})
        allowed_sessions = sym_cfg.get("sessions", [])
        return current_session in allowed_sessions

    def next_market_open(self, symbol: str) -> datetime:
        """Retorna el próximo lunes a las 00:00 UTC (apertura Forex estándar)."""
        now = datetime.now(timezone.utc)
        days_ahead = (7 - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)

    def next_market_close(self, symbol: str) -> datetime:
        """Retorna el próximo viernes a las 21:00 UTC (cierre Forex estándar)."""
        now = datetime.now(timezone.utc)
        days_ahead = (4 - now.weekday()) % 7  # 4 = viernes
        target = (now + timedelta(days=days_ahead)).replace(hour=21, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(weeks=1)
        return target
