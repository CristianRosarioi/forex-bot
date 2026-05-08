"""Tests de integración para EventPersistenceHandler."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from bot.core.event_bus import EventBus, EventType
from bot.db.repository import BotEventRepository
from bot.infra.event_persistence import EventPersistenceHandler


def test_event_persisted_on_publish(db_session):
    """Publicar SIGNAL_GENERATED debe persistir un evento en bot_events."""
    bus = EventBus()
    handler = EventPersistenceHandler(bus)

    # Usar la db_session de prueba parcheando get_session
    from contextlib import contextmanager

    @contextmanager
    def mock_get_session():
        yield db_session

    with patch("bot.infra.event_persistence.get_session", mock_get_session):
        handler.start()
        bus.publish(EventType.SIGNAL_GENERATED, {
            "symbol": "EURUSD",
            "direction": "BUY",
            "entry_price": 1.085,
        })
        handler.stop()

    db_session.flush()
    repo = BotEventRepository(db_session)
    events = repo.get_recent(limit=10)
    assert len(events) >= 1
    signal_events = [e for e in events if e.event_type == EventType.SIGNAL_GENERATED]
    assert len(signal_events) == 1
    assert signal_events[0].severity == "INFO"


def test_bar_closed_not_persisted(db_session):
    """Publicar BAR_CLOSED NO debe persistir ningún evento (está excluido del mapa)."""
    bus = EventBus()
    handler = EventPersistenceHandler(bus)

    from contextlib import contextmanager

    @contextmanager
    def mock_get_session():
        yield db_session

    with patch("bot.infra.event_persistence.get_session", mock_get_session):
        handler.start()
        bus.publish(EventType.BAR_CLOSED, {"symbol": "EURUSD", "timeframe": "M15"})
        handler.stop()

    db_session.flush()
    repo = BotEventRepository(db_session)
    events = repo.get_recent(limit=100)
    bar_events = [e for e in events if e.event_type == EventType.BAR_CLOSED]
    assert len(bar_events) == 0


def test_db_failure_does_not_crash(db_session):
    """Si get_session lanza excepción, el handler NO debe propagar el error."""
    bus = EventBus()
    handler = EventPersistenceHandler(bus)

    from contextlib import contextmanager

    @contextmanager
    def failing_get_session():
        raise RuntimeError("DB connection lost")
        yield  # makes it a generator

    with patch("bot.infra.event_persistence.get_session", failing_get_session):
        handler.start()
        # Debe no lanzar excepción
        bus.publish(EventType.ORDER_PLACED, {"symbol": "EURUSD", "direction": "BUY"})
        handler.stop()
    # Si llegamos aquí, el test pasa


def test_severity_mapping(db_session):
    """KILL_SWITCH_TRIGGERED -> CRITICAL, MT5_DISCONNECTED -> WARNING."""
    bus = EventBus()
    handler = EventPersistenceHandler(bus)

    from contextlib import contextmanager

    @contextmanager
    def mock_get_session():
        yield db_session

    with patch("bot.infra.event_persistence.get_session", mock_get_session):
        handler.start()
        bus.publish(EventType.KILL_SWITCH_TRIGGERED, {"balance": 8000.0})
        bus.publish(EventType.MT5_DISCONNECTED, {"reason": "timeout"})
        handler.stop()

    db_session.flush()
    repo = BotEventRepository(db_session)
    events = repo.get_recent(limit=100)

    kill_events = [e for e in events if e.event_type == EventType.KILL_SWITCH_TRIGGERED]
    mt5_events = [e for e in events if e.event_type == EventType.MT5_DISCONNECTED]

    assert len(kill_events) == 1
    assert kill_events[0].severity == "CRITICAL"

    assert len(mt5_events) == 1
    assert mt5_events[0].severity == "WARNING"
