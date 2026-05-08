"""Configuración de logging estructurado (JSON). Escribe a stdout y a archivo con rotación diaria."""

import json
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# ANSI color codes para stdout en desarrollo
_COLORS = {
    "DEBUG": "\033[36m",    # cyan
    "INFO": "\033[32m",     # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",    # red
    "CRITICAL": "\033[35m", # magenta
    "RESET": "\033[0m",
}


class _JsonFormatter(logging.Formatter):
    """Formatea cada registro como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Campos de contexto extra inyectados vía logger.info("...", extra={"context": {...}})
        if hasattr(record, "context"):
            payload["context"] = record.context
        return json.dumps(payload, ensure_ascii=False)


class _ColorFormatter(logging.Formatter):
    """Formatea registros para stdout con colores y formato legible."""

    _FMT = "{color}[{level}]{reset} {time} {name}: {msg}"

    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelname, "")
        reset = _COLORS["RESET"]
        time_str = self.formatTime(record, datefmt="%H:%M:%S")
        line = self._FMT.format(
            color=color,
            reset=reset,
            level=record.levelname[0],  # D/I/W/E/C
            time=time_str,
            name=record.name,
            msg=record.getMessage(),
        )
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _ensure_logs_dir() -> None:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger configurado con handlers de archivo (JSON) y stdout (color).

    Args:
        name: Nombre del módulo, típicamente __name__.

    Returns:
        logging.Logger listo para usar.
    """
    logger = logging.getLogger(name)

    # Evitar añadir handlers duplicados si get_logger se llama varias veces
    if logger.handlers:
        return logger

    logger.setLevel(_LOG_LEVEL)

    # — Handler de archivo: JSON, rotación diaria, 30 días de retención —
    _ensure_logs_dir()
    file_handler = TimedRotatingFileHandler(
        filename=_LOGS_DIR / "bot.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(_JsonFormatter())
    file_handler.setLevel(_LOG_LEVEL)

    # — Handler de stdout: legible con colores —
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(_ColorFormatter())
    stdout_handler.setLevel(_LOG_LEVEL)

    logger.addHandler(file_handler)
    logger.addHandler(stdout_handler)
    logger.propagate = False

    return logger
