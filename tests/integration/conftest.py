"""Fixtures de pytest para los tests de integración de la capa de persistencia."""

from __future__ import annotations

import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from bot.db.models import Base
from bot.db.repository import (
    BotEventRepository,
    DrawdownRepository,
    MT5ConnectionRepository,
    OrderRepository,
    SignalRepository,
    TradeRepository,
)
from config.settings import settings

# URL de la base de datos de prueba
_TEST_DB_NAME = "forex_bot_test"
_TEST_DB_URL = (
    f"postgresql+psycopg2://{settings.db.user}:{settings.db.password}"
    f"@{settings.db.host}:{settings.db.port}/{_TEST_DB_NAME}"
)


def _ensure_test_db() -> None:
    """Crea la base de datos de prueba si no existe. Usa psycopg2 con autocommit."""
    conn = psycopg2.connect(
        host=settings.db.host,
        port=settings.db.port,
        user=settings.db.user,
        password=settings.db.password,
        database="postgres",
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB_NAME,))
    if cur.fetchone() is None:
        cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    cur.close()
    conn.close()


@pytest.fixture(scope="session")
def test_engine():
    """Engine SQLAlchemy conectado a la DB de prueba (una vez por sesión de tests)."""
    _ensure_test_db()
    engine = create_engine(_TEST_DB_URL, pool_pre_ping=True, echo=False)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine) -> Session:
    """Sesión de DB limpia para cada test. Trunca todas las tablas al finalizar."""
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    session = TestSession()
    yield session
    session.rollback()
    session.close()

    # Truncar todas las tablas en orden correcto (respetar FK)
    cleanup_session = TestSession()
    try:
        cleanup_session.execute(text("TRUNCATE TABLE trades, orders, signals, "
                                     "drawdown_snapshots, bot_events, "
                                     "mt5_connection_log, economic_events RESTART IDENTITY CASCADE"))
        cleanup_session.commit()
    finally:
        cleanup_session.close()


@pytest.fixture()
def repositories(db_session: Session) -> dict:
    """Diccionario con todos los repositorios instanciados sobre la misma sesión."""
    return {
        "signals": SignalRepository(db_session),
        "orders": OrderRepository(db_session),
        "trades": TradeRepository(db_session),
        "drawdown": DrawdownRepository(db_session),
        "bot_events": BotEventRepository(db_session),
        "mt5_conn": MT5ConnectionRepository(db_session),
    }
