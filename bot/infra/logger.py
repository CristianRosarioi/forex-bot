"""Configuración de logging estructurado (JSON). Escribe a stdout y a archivo con rotación por tamaño."""

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
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

    # — Handler de archivo: JSON, rotación por tamaño (evita PermissionError en Windows) —
    _ensure_logs_dir()
    file_handler = RotatingFileHandler(
        filename=_LOGS_DIR / "bot.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB por archivo
        backupCount=30,
        encoding="utf-8",
        delay=True,
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


import threading

_db_log_lock = threading.Lock()


def log_event(
    event_type: str,
    severity: str,
    module: str,
    message: str,
    context: dict | None = None,
) -> None:
    """Loggea un evento importante: stdout/archivo Y persiste en bot_events de PostgreSQL.

    Si la DB no está disponible, loggea el error pero NO propaga la excepción (fail-safe).
    Thread-safe.

    Args:
        event_type: Tipo de evento (e.g. "MT5_DISCONNECTED"). Mismo vocabulario que EventType.
        severity: "INFO" | "WARNING" | "ERROR" | "CRITICAL"
        module: Nombre del módulo de origen (e.g. "bot.core.connector")
        message: Mensaje legible por humanos.
        context: Datos adicionales como dict, opcional.
    """
    _log = get_logger(module)
    level_map = {
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    level = level_map.get(severity.upper(), logging.INFO)
    _log.log(level, f"[{event_type}] {message}", extra={"context": context or {}})

    with _db_log_lock:
        try:
            from bot.db.session import get_session, SessionLocal
            if SessionLocal is None:
                return  # DB no inicializada, skip silencioso
            from bot.db.repository import BotEventRepository
            with get_session() as session:
                repo = BotEventRepository(session)
                repo.log(
                    event_type=event_type,
                    severity=severity.upper(),
                    module=module,
                    message=message,
                    context=context,
                )
        except Exception as exc:
            _err_log = get_logger(__name__)
            _err_log.error(f"log_event: failed to persist to DB: {exc}")
