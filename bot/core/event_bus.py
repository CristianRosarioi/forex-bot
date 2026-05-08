"""Bus de eventos interno. Desacopla productores (feed, señales) de consumidores (execution, logger)."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable

from bot.infra.logger import get_logger

logger = get_logger(__name__)


class EventType:
    """Constantes de tipos de eventos del sistema."""

    BAR_CLOSED = "bar_closed"
    SIGNAL_GENERATED = "signal_generated"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"
    POSITION_CLOSED = "position_closed"
    MT5_DISCONNECTED = "mt5_disconnected"
    MT5_RECONNECTED = "mt5_reconnected"
    DRAWDOWN_LIMIT_HIT = "drawdown_limit_hit"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"


class EventBus:
    """Pub/sub síncrono en proceso. Thread-safe para subscribe/unsubscribe/publish."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        """Registra un handler para un tipo de evento.

        Args:
            event_type: Uno de los valores de EventType.
            handler: Callable que recibe el payload como dict.
        """
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)
                logger.debug(
                    "Subscribed handler",
                    extra={"context": {"event_type": event_type, "handler": handler.__qualname__}},
                )

    def unsubscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        """Elimina un handler previamente registrado.

        Args:
            event_type: Tipo de evento del que desuscribirse.
            handler: El mismo callable que se pasó a subscribe().
        """
        with self._lock:
            try:
                self._handlers[event_type].remove(handler)
                logger.debug(
                    "Unsubscribed handler",
                    extra={"context": {"event_type": event_type, "handler": handler.__qualname__}},
                )
            except ValueError:
                pass  # handler no estaba registrado, no es un error

    def publish(self, event_type: str, payload: dict) -> None:
        """Emite un evento a todos los handlers suscritos.

        Los handlers se ejecutan secuencialmente. Si uno lanza una excepción,
        se loggea y se continúa con los demás.

        Args:
            event_type: Tipo de evento a emitir.
            payload: Datos del evento como dict.
        """
        logger.debug(
            f"Publishing event: {event_type}",
            extra={"context": {"event_type": event_type, "payload_keys": list(payload.keys())}},
        )

        with self._lock:
            handlers = list(self._handlers.get(event_type, []))

        for handler in handlers:
            try:
                handler(payload)
            except Exception:
                logger.exception(
                    f"Handler {handler.__qualname__!r} raised an exception for event {event_type!r}"
                )
