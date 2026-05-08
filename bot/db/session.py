"""Fábrica de sesiones SQLAlchemy. Gestiona el pool de conexiones a PostgreSQL."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from config.settings import settings
from bot.infra.logger import get_logger

logger = get_logger(__name__)

engine = None
SessionLocal = None


def init_engine() -> None:
    """Inicializa el engine SQLAlchemy y el sessionmaker.

    Debe llamarse una vez al arrancar la aplicación, antes de usar get_session().
    Configura pool con pre-ping para detectar conexiones caídas.
    """
    global engine, SessionLocal
    engine = create_engine(
        settings.db.url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    logger.info("Database engine initialized")


def dispose_engine() -> None:
    """Cierra todas las conexiones del pool y libera recursos."""
    global engine
    if engine:
        engine.dispose()
        logger.info("Database engine disposed")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager que provee una sesión SQLAlchemy con commit/rollback automático.

    Yields:
        Session: Sesión activa, lista para ejecutar queries.

    Raises:
        RuntimeError: Si init_engine() no fue llamado antes.
        Exception: Cualquier excepción de la capa de datos (no silenciada).

    Example::

        with get_session() as session:
            repo = SignalRepository(session)
            signal = repo.get_by_id(42)
    """
    if SessionLocal is None:
        raise RuntimeError("Engine not initialized. Call init_engine() first.")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
