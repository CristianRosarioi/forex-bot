"""Gestión de la conexión con MetaTrader 5: inicialización, reconexión y watchdog."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import MetaTrader5 as mt5

from bot.core.event_bus import EventBus, EventType
from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from config.settings import MT5Settings

logger = get_logger(__name__)

# Intervalos de backoff exponencial para reconexión (segundos)
_BACKOFF_SEQUENCE = [5, 10, 30, 60, 60]
_WATCHDOG_INTERVAL = 30  # segundos entre checks del watchdog


class MT5Connector:
    """Gestiona el ciclo de vida de la conexión con MetaTrader 5.

    Incluye watchdog en thread separado que detecta desconexiones y
    reintenta con backoff exponencial.
    """

    def __init__(self, settings: "MT5Settings", event_bus: EventBus) -> None:
        self._settings = settings
        self._bus = event_bus
        self._connected = False
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None

    # ──────────────────────────────────────────────
    # API pública
    # ──────────────────────────────────────────────

    def connect(self) -> bool:
        """Inicializa y autentica la conexión con MT5.

        Returns:
            True si la conexión fue exitosa, False en caso contrario.
        """
        logger.info(
            "Connecting to MT5",
            extra={"context": {"server": self._settings.server, "login": self._settings.login}},
        )
        initialized = mt5.initialize(
            path=self._settings.path,
            login=self._settings.login,
            password=self._settings.password,
            server=self._settings.server,
        )
        if not initialized:
            error = mt5.last_error()
            logger.error(
                "MT5 initialization failed",
                extra={"context": {"error_code": error[0], "error_msg": error[1]}},
            )
            self._connected = False
            return False

        info = mt5.terminal_info()
        if info is None or not info.connected:
            logger.error("MT5 terminal not connected after initialize")
            mt5.shutdown()
            self._connected = False
            return False

        self._connected = True
        logger.info(
            "MT5 connected",
            extra={"context": {"server": self._settings.server, "build": info.build}},
        )
        self._start_watchdog()
        return True

    def disconnect(self) -> None:
        """Cierra la conexión MT5 y detiene el watchdog limpiamente."""
        logger.info("Disconnecting from MT5")
        self._shutdown_event.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=5)
        mt5.shutdown()
        self._connected = False
        logger.info("MT5 disconnected")

    def is_connected(self) -> bool:
        """Comprueba en tiempo real si el terminal MT5 está conectado.

        Returns:
            True si terminal_info().connected es True.
        """
        try:
            info = mt5.terminal_info()
            return info is not None and info.connected
        except Exception:
            return False

    def ensure_connected(self) -> None:
        """Garantiza que hay conexión activa. Si no, intenta reconectar.

        Útil para llamar antes de operaciones críticas.
        Raises:
            RuntimeError: si no consigue reconectar tras todos los reintentos.
        """
        if self.is_connected():
            return
        logger.warning("Connection lost — attempting reconnect")
        success = self._reconnect_with_backoff()
        if not success:
            raise RuntimeError("Unable to reconnect to MT5 after all retries")

    # ──────────────────────────────────────────────
    # Internos
    # ──────────────────────────────────────────────

    def _start_watchdog(self) -> None:
        self._shutdown_event.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="mt5-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        logger.debug("MT5 watchdog started")

    def _watchdog_loop(self) -> None:
        """Loop del watchdog: verifica conexión cada _WATCHDOG_INTERVAL segundos."""
        while not self._shutdown_event.wait(timeout=_WATCHDOG_INTERVAL):
            if not self.is_connected():
                logger.warning("Watchdog detected disconnection")
                self._bus.publish(EventType.MT5_DISCONNECTED, {"reason": "watchdog_check"})
                success = self._reconnect_with_backoff()
                if success:
                    self._bus.publish(EventType.MT5_RECONNECTED, {})
                else:
                    logger.critical("Watchdog could not restore MT5 connection — giving up")
                    break

    def _reconnect_with_backoff(self) -> bool:
        """Intenta reconectar siguiendo la secuencia de backoff.

        Returns:
            True si consigue reconectar, False si agota todos los intentos.
        """
        mt5.shutdown()
        for attempt, wait in enumerate(_BACKOFF_SEQUENCE, start=1):
            logger.info(
                f"Reconnect attempt {attempt}/{len(_BACKOFF_SEQUENCE)} — waiting {wait}s"
            )
            if self._shutdown_event.wait(timeout=wait):
                logger.info("Shutdown requested during reconnect backoff")
                return False

            initialized = mt5.initialize(
                path=self._settings.path,
                login=self._settings.login,
                password=self._settings.password,
                server=self._settings.server,
            )
            if initialized and self.is_connected():
                logger.info(f"Reconnected successfully on attempt {attempt}")
                self._connected = True
                return True

            error = mt5.last_error()
            logger.warning(
                f"Reconnect attempt {attempt} failed",
                extra={"context": {"error_code": error[0], "error_msg": error[1]}},
            )

        self._connected = False
        return False
