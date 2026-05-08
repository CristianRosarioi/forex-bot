"""Obtención y normalización de datos de mercado (velas OHLCV) desde MT5."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import MetaTrader5 as mt5
import pandas as pd
import yaml

from bot.core.event_bus import EventBus, EventType
from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from bot.core.connector import MT5Connector
    from bot.core.timeframe_buffer import TimeframeBuffer

logger = get_logger(__name__)

_SYMBOLS_YAML = Path(__file__).parent.parent.parent / "config" / "symbols.yaml"

# Mapping de nombre de timeframe a constante MT5
_TF_MAP: dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# Polling rate por timeframe (segundos entre cada lectura)
_POLL_INTERVAL: dict[str, int] = {
    "M1": 5,
    "M5": 15,
    "M15": 30,
    "H1": 60,
    "H4": 60,
    "D1": 60,
}

# Timeframes que el feed monitorea
_TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"]

# Cuántas velas solicitar en cada poll (para inicializar el buffer)
_INITIAL_BARS = 200
_BARS_PER_POLL = 5  # suficiente para detectar cierre de vela


def _load_enabled_symbols() -> list[str]:
    """Lee symbols.yaml y devuelve los símbolos con enabled=true."""
    with open(_SYMBOLS_YAML, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return [sym for sym, data in cfg.get("symbols", {}).items() if data.get("enabled", False)]


class MarketDataFeed:
    """Lectura periódica de velas multi-símbolo/multi-timeframe desde MT5.

    Detecta el cierre de velas comparando timestamps y emite eventos BAR_CLOSED
    al EventBus para que los consumidores (estrategias, analytics) reaccionen.
    """

    def __init__(
        self,
        connector: "MT5Connector",
        event_bus: EventBus,
        buffer: "TimeframeBuffer",
    ) -> None:
        self._connector = connector
        self._bus = event_bus
        self._buffer = buffer
        self._symbols: list[str] = _load_enabled_symbols()
        # Rastrea el timestamp de la última vela conocida por (symbol, timeframe)
        self._last_bar_time: dict[tuple[str, str], int] = {}
        self._running = False

    def get_latest_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Solicita las últimas `count` velas directamente a MT5.

        Args:
            symbol: Símbolo del instrumento, e.g. "EURUSD".
            timeframe: Timeframe como string, e.g. "M15".
            count: Número de velas a solicitar.

        Returns:
            DataFrame con columnas: time, open, high, low, close, tick_volume.
            Vacío si MT5 no devuelve datos.
        """
        tf_const = _TF_MAP.get(timeframe)
        if tf_const is None:
            logger.warning(f"Unknown timeframe: {timeframe}")
            return pd.DataFrame()

        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            logger.warning(
                f"No data for {symbol} {timeframe}",
                extra={"context": {"error_code": error[0], "error_msg": error[1]}},
            )
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df[["time", "open", "high", "low", "close", "tick_volume"]]

    def poll_loop(self) -> None:
        """Loop principal de polling. Bloquea hasta que se llame a stop().

        Itera sobre todos los símbolos y timeframes, detecta cierres de vela
        y emite eventos BAR_CLOSED.
        """
        self._running = True
        logger.info(
            "MarketDataFeed started",
            extra={"context": {"symbols": self._symbols, "timeframes": _TIMEFRAMES}},
        )

        # Inicializar buffer con velas históricas
        self._initialize_buffer()

        # Tracking de cuándo toca pollear cada (symbol, timeframe)
        next_poll: dict[tuple[str, str], float] = {}
        now = time.monotonic()
        for sym in self._symbols:
            for tf in _TIMEFRAMES:
                next_poll[(sym, tf)] = now

        while self._running:
            now = time.monotonic()
            for sym in self._symbols:
                for tf in _TIMEFRAMES:
                    key = (sym, tf)
                    if now < next_poll[key]:
                        continue

                    self._poll_symbol_timeframe(sym, tf)
                    next_poll[key] = time.monotonic() + _POLL_INTERVAL[tf]

            time.sleep(1)  # granularidad mínima del loop

        logger.info("MarketDataFeed stopped")

    def stop(self) -> None:
        """Señala al poll_loop que debe terminar en el siguiente ciclo."""
        self._running = False

    # ──────────────────────────────────────────────
    # Internos
    # ──────────────────────────────────────────────

    def _initialize_buffer(self) -> None:
        """Carga velas históricas al arrancar para tener contexto inmediato."""
        for sym in self._symbols:
            for tf in _TIMEFRAMES:
                try:
                    self._connector.ensure_connected()
                    df = self.get_latest_bars(sym, tf, _INITIAL_BARS)
                    if df.empty:
                        continue
                    # Convertir time a unix int para el buffer interno
                    df_raw = _df_to_raw(df)
                    self._buffer.update(sym, tf, df_raw)
                    self._last_bar_time[(sym, tf)] = int(df_raw.iloc[-1]["time"])
                    logger.debug(f"Initialized buffer {sym}/{tf} with {len(df_raw)} bars")
                except Exception:
                    logger.exception(f"Failed to initialize buffer for {sym}/{tf}")

    def _poll_symbol_timeframe(self, symbol: str, timeframe: str) -> None:
        """Solicita velas recientes y detecta si se cerró una nueva vela."""
        try:
            self._connector.ensure_connected()
        except RuntimeError:
            logger.error("Cannot poll: MT5 not connected")
            return

        if not _symbol_available(symbol):
            logger.warning(f"Symbol {symbol} not available in MT5 — skipping")
            return

        df = self.get_latest_bars(symbol, timeframe, _BARS_PER_POLL)
        if df.empty:
            return

        df_raw = _df_to_raw(df)
        last_ts = int(df_raw.iloc[-1]["time"])
        key = (symbol, timeframe)
        prev_ts = self._last_bar_time.get(key, 0)

        if last_ts > prev_ts:
            # Nueva vela cerrada: la penúltima (index -2) es la que se cerró;
            # la última (index -1) es la vela abierta actual.
            closed_bar = df_raw.iloc[-2].to_dict() if len(df_raw) >= 2 else df_raw.iloc[-1].to_dict()
            self._buffer.update(symbol, timeframe, df_raw)
            self._last_bar_time[key] = last_ts

            self._bus.publish(
                EventType.BAR_CLOSED,
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar": closed_bar,
                },
            )
            logger.debug(
                f"BAR_CLOSED {symbol}/{timeframe}",
                extra={"context": {"timestamp": last_ts}},
            )


def _df_to_raw(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte columna time de datetime a unix int para almacenamiento interno."""
    df = df.copy()
    if pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = df["time"].astype("int64") // 10**9
    return df


def _symbol_available(symbol: str) -> bool:
    """Verifica que el símbolo está disponible en MT5."""
    info = mt5.symbol_info(symbol)
    return info is not None
