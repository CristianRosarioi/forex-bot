"""Buffer circular de velas por símbolo y timeframe. Mantiene la ventana de datos en memoria."""

from __future__ import annotations

import threading
from collections import deque
from typing import Optional

import pandas as pd

from bot.infra.logger import get_logger

logger = get_logger(__name__)

_MAX_BARS = 500


class TimeframeBuffer:
    """Cache en memoria de velas OHLCV por (símbolo, timeframe).

    Thread-safe. Cada slot guarda hasta _MAX_BARS velas en un deque.
    No persiste en disco; se reconstruye al arrancar el feed.
    """

    def __init__(self, max_bars: int = _MAX_BARS) -> None:
        self._max_bars = max_bars
        self._data: dict[tuple[str, str], deque[dict]] = {}
        self._lock = threading.Lock()

    def update(self, symbol: str, timeframe: str, bars: pd.DataFrame) -> None:
        """Reemplaza o extiende el buffer con nuevas velas.

        Las velas se añaden en orden; el deque descarta las más antiguas
        cuando supera max_bars.

        Args:
            symbol: Símbolo del instrumento, e.g. "EURUSD".
            timeframe: Timeframe como string, e.g. "M15".
            bars: DataFrame con columnas time, open, high, low, close, tick_volume.
        """
        key = (symbol, timeframe)
        records = bars.to_dict("records")

        with self._lock:
            if key not in self._data:
                self._data[key] = deque(maxlen=self._max_bars)
            buf = self._data[key]

            if not buf:
                buf.extend(records)
            else:
                # Solo añade velas cuyo timestamp sea posterior al último almacenado
                last_ts = buf[-1]["time"]
                new_records = [r for r in records if r["time"] > last_ts]
                buf.extend(new_records)

        logger.debug(
            "Buffer updated",
            extra={"context": {"symbol": symbol, "timeframe": timeframe, "new_bars": len(records)}},
        )

    def get(self, symbol: str, timeframe: str, count: Optional[int] = None) -> pd.DataFrame:
        """Devuelve las últimas `count` velas del buffer como DataFrame.

        Args:
            symbol: Símbolo del instrumento.
            timeframe: Timeframe como string.
            count: Número de velas a devolver. None devuelve todas.

        Returns:
            DataFrame con las velas, vacío si no hay datos.
        """
        key = (symbol, timeframe)
        with self._lock:
            buf = self._data.get(key)
            if not buf:
                return pd.DataFrame()
            records = list(buf) if count is None else list(buf)[-count:]

        return pd.DataFrame(records)

    def get_latest(self, symbol: str, timeframe: str) -> Optional[dict]:
        """Devuelve la vela más reciente como dict, o None si no hay datos.

        Args:
            symbol: Símbolo del instrumento.
            timeframe: Timeframe como string.

        Returns:
            Dict con los campos OHLCV de la última vela, o None.
        """
        key = (symbol, timeframe)
        with self._lock:
            buf = self._data.get(key)
            if not buf:
                return None
            return dict(buf[-1])

    def has_data(self, symbol: str, timeframe: str) -> bool:
        """Indica si el buffer tiene al menos una vela para el par (symbol, timeframe).

        Args:
            symbol: Símbolo del instrumento.
            timeframe: Timeframe como string.

        Returns:
            True si hay datos, False en caso contrario.
        """
        key = (symbol, timeframe)
        with self._lock:
            buf = self._data.get(key)
            return bool(buf)
