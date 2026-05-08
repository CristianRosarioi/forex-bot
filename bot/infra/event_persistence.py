"""Handler que persiste eventos del EventBus en la tabla bot_events de PostgreSQL."""

from __future__ import annotations

import threading
from typing import Callable

from bot.core.event_bus import EventBus, EventType
from bot.db.session import get_session
from bot.db.repository import BotEventRepository
from bot.infra.logger import get_logger

logger = get_logger(__name__)

# Mapeo EventType -> severity. Los que NO están aquí no se persisten.
_SEVERITY_MAP = {
    EventType.SIGNAL_GENERATED:      "INFO",
    EventType.ORDER_PLACED:          "INFO",
    EventType.ORDER_FILLED:          "INFO",
    EventType.POSITION_CLOSED:       "INFO",
    EventType.MT5_RECONNECTED:       "INFO",
    EventType.MT5_DISCONNECTED:      "WARNING",
    EventType.ORDER_REJECTED:        "WARNING",
    EventType.DRAWDOWN_LIMIT_HIT:    "ERROR",
    EventType.KILL_SWITCH_TRIGGERED: "CRITICAL",
    # BAR_CLOSED excluido intencionalmente (spam)
}

# Mensajes default por tipo de evento
_MESSAGE_TEMPLATE = {
    EventType.SIGNAL_GENERATED:      "Signal generated: {symbol} {direction} @ {entry_price}",
    EventType.ORDER_PLACED:          "Order placed: {symbol} {direction}",
    EventType.ORDER_FILLED:          "Order filled: ticket #{mt5_ticket}",
    EventType.POSITION_CLOSED:       "Position closed: {symbol} PnL={pnl_currency}",
    EventType.MT5_RECONNECTED:       "MT5 reconnected",
    EventType.MT5_DISCONNECTED:      "MT5 disconnected: {reason}",
    EventType.ORDER_REJECTED:        "Order rejected: {reason}",
    EventType.DRAWDOWN_LIMIT_HIT:    "Drawdown limit hit: {period} {drawdown_pct}%",
    EventType.KILL_SWITCH_TRIGGERED: "Kill switch triggered: balance={balance}",
}


class _SafeDict(dict):
    """dict que devuelve '{key}' en lugar de KeyError para format_map."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


class EventPersistenceHandler:
    """Suscriptor del EventBus que persiste eventos importantes en bot_events.

    Uso::

        handler = EventPersistenceHandler(event_bus)
        handler.start()
        # ... bot corriendo ...
        handler.stop()
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._lock = threading.Lock()
        self._started = False
        self._handlers: dict[str, Callable] = {}

    def _make_handler(self, event_type: str) -> Callable:
        """Crea un closure que captura el event_type específico."""

        def handler(payload: dict) -> None:
            severity = _SEVERITY_MAP.get(event_type, "INFO")
            template = _MESSAGE_TEMPLATE.get(event_type, f"Event: {event_type}")
            try:
                message = template.format_map(_SafeDict(payload))
            except Exception:
                message = f"Event: {event_type}"

            with self._lock:
                try:
                    with get_session() as session:
                        repo = BotEventRepository(session)
                        repo.log(
                            event_type=event_type,
                            severity=severity,
                            module="event_bus",
                            message=message,
                            context=payload,
                        )
                except Exception:
                    logger.exception(
                        "EventPersistenceHandler: failed to persist event %s", event_type
                    )

        return handler

    def start(self) -> None:
        """Suscribe al bus usando closures que capturan el event_type."""
        self._handlers = {}
        for event_type in _SEVERITY_MAP:
            h = self._make_handler(event_type)
            self._handlers[event_type] = h
            self._bus.subscribe(event_type, h)
        self._started = True
        logger.info(
            "EventPersistenceHandler started — subscribed to %d event types",
            len(_SEVERITY_MAP),
        )

    def stop(self) -> None:
        """Desuscribe limpiamente del bus."""
        for event_type, h in self._handlers.items():
            self._bus.unsubscribe(event_type, h)
        self._handlers = {}
        self._started = False
        logger.info("EventPersistenceHandler stopped")
