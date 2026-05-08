"""Tests de integración para los modelos SQLAlchemy (creación de tablas, FKs, constraints)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bot.db.models import Order, Signal, Trade


def _signal_data(**overrides) -> dict:
    """Datos mínimos válidos para crear una Signal."""
    base = {
        "generated_at": datetime.now(timezone.utc),
        "symbol": "EURUSD",
        "timeframe": "H1",
        "strategy_name": "retest_strategy",
        "signal_type": "retest",
        "direction": "BUY",
        "entry_price": 1.08500,
        "sl_price": 1.08000,
        "tp_price": 1.09000,
        "reason": "Retest of demand zone",
        "bot_mode": "SHADOW",
        "acted_on": False,
    }
    base.update(overrides)
    return base


def _order_data(signal_id: int | None = None, **overrides) -> dict:
    """Datos mínimos válidos para crear una Order."""
    base = {
        "signal_id": signal_id,
        "created_at": datetime.now(timezone.utc),
        "symbol": "EURUSD",
        "direction": "BUY",
        "requested_price": 1.08500,
        "requested_volume": 0.10,
        "sl_price": 1.08000,
        "tp_price": 1.09000,
        "status": "pending",
        "bot_mode": "SHADOW",
    }
    base.update(overrides)
    return base


def _trade_data(order_id: int, **overrides) -> dict:
    """Datos mínimos válidos para crear un Trade."""
    base = {
        "order_id": order_id,
        "mt5_ticket": 123456789,
        "symbol": "EURUSD",
        "direction": "BUY",
        "entry_price": 1.08500,
        "volume": 0.10,
        "sl_price": 1.08000,
        "tp_price": 1.09000,
        "opened_at": datetime.now(timezone.utc),
        "strategy_name": "retest_strategy",
        "bot_mode": "SHADOW",
    }
    base.update(overrides)
    return base


def test_create_signal(db_session: Session) -> None:
    """Insertar una Signal y recuperarla por id verifica todos los campos básicos."""
    signal = Signal(**_signal_data())
    db_session.add(signal)
    db_session.flush()

    assert signal.id is not None

    db_session.expire(signal)
    retrieved = db_session.get(Signal, signal.id)

    assert retrieved is not None
    assert retrieved.symbol == "EURUSD"
    assert retrieved.direction == "BUY"
    assert retrieved.strategy_name == "retest_strategy"
    assert retrieved.signal_type == "retest"
    assert retrieved.bot_mode == "SHADOW"
    assert retrieved.acted_on is False
    assert retrieved.rejection_reason is None
    assert float(retrieved.entry_price) == pytest.approx(1.08500, abs=1e-5)
    assert float(retrieved.sl_price) == pytest.approx(1.08000, abs=1e-5)


def test_signal_order_relationship(db_session: Session) -> None:
    """Crear Signal y Order con signal_id verifica que la FK funciona."""
    signal = Signal(**_signal_data())
    db_session.add(signal)
    db_session.flush()

    order = Order(**_order_data(signal_id=signal.id))
    db_session.add(order)
    db_session.flush()

    assert order.id is not None
    assert order.signal_id == signal.id

    db_session.expire(order)
    retrieved = db_session.get(Order, order.id)
    assert retrieved is not None
    assert retrieved.signal_id == signal.id


def test_order_sl_not_null(db_session: Session) -> None:
    """Crear Order sin sl_price debe fallar con IntegrityError."""
    order_data = _order_data(sl_price=None)
    # Forzar NULL en sl_price para el test de constraint
    order = Order(**order_data)
    order.sl_price = None  # type: ignore[assignment]
    db_session.add(order)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()


def test_full_chain(db_session: Session) -> None:
    """Cadena completa Signal -> Order -> Trade se persiste y enlaza correctamente."""
    # Signal
    signal = Signal(**_signal_data())
    db_session.add(signal)
    db_session.flush()

    # Order
    order = Order(**_order_data(signal_id=signal.id, status="filled", mt5_ticket=99001))
    db_session.add(order)
    db_session.flush()

    # Trade
    trade = Trade(**_trade_data(order_id=order.id))
    db_session.add(trade)
    db_session.flush()

    assert trade.id is not None
    assert trade.order_id == order.id

    db_session.expire_all()

    retrieved_trade = db_session.get(Trade, trade.id)
    assert retrieved_trade is not None
    assert retrieved_trade.order_id == order.id
    assert float(retrieved_trade.entry_price) == pytest.approx(1.08500, abs=1e-5)
    assert retrieved_trade.symbol == "EURUSD"
    assert retrieved_trade.strategy_name == "retest_strategy"
