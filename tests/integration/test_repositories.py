"""Tests de integración para todos los repositorios de la capa de persistencia."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from bot.db.models import Signal, Order, Trade


# ─────────────────────────────── helpers ───────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _signal_data(**overrides) -> dict:
    base = {
        "generated_at": _now(),
        "symbol": "EURUSD",
        "timeframe": "H1",
        "strategy_name": "retest_strategy",
        "signal_type": "retest",
        "direction": "BUY",
        "entry_price": 1.08500,
        "sl_price": 1.08000,
        "tp_price": 1.09000,
        "reason": "Demand zone retest",
        "bot_mode": "SHADOW",
        "acted_on": False,
    }
    base.update(overrides)
    return base


def _order_data(signal_id: int | None = None, **overrides) -> dict:
    base = {
        "signal_id": signal_id,
        "created_at": _now(),
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


def _trade_data(order_id: int, pnl_currency: float | None = None, **overrides) -> dict:
    base = {
        "order_id": order_id,
        "mt5_ticket": 100000 + order_id,
        "symbol": "EURUSD",
        "direction": "BUY",
        "entry_price": 1.08500,
        "volume": 0.10,
        "sl_price": 1.08000,
        "tp_price": 1.09000,
        "opened_at": _now(),
        "strategy_name": "retest_strategy",
        "bot_mode": "SHADOW",
        "pnl_currency": pnl_currency,
    }
    base.update(overrides)
    return base


def _make_order(repositories: dict) -> Order:
    """Crea una signal + order y retorna la orden."""
    sig = repositories["signals"].create(_signal_data())
    return repositories["orders"].create(_order_data(signal_id=sig.id))


# ─────────────────────────── SignalRepository ────────────────────────────


def test_signal_create_and_get_by_id(repositories: dict) -> None:
    """Crear señal y recuperarla por id."""
    sig = repositories["signals"].create(_signal_data(symbol="GBPUSD"))
    assert sig.id is not None

    retrieved = repositories["signals"].get_by_id(sig.id)
    assert retrieved is not None
    assert retrieved.symbol == "GBPUSD"
    assert retrieved.direction == "BUY"


def test_signal_mark_acted_on(repositories: dict) -> None:
    """Marcar señal como actuada."""
    sig = repositories["signals"].create(_signal_data())
    assert sig.acted_on is False

    repositories["signals"].mark_acted_on(sig.id)

    updated = repositories["signals"].get_by_id(sig.id)
    assert updated is not None
    assert updated.acted_on is True


def test_signal_mark_rejected(repositories: dict) -> None:
    """Marcar señal como rechazada con motivo."""
    sig = repositories["signals"].create(_signal_data())
    repositories["signals"].mark_rejected(sig.id, "Risk limit exceeded")

    updated = repositories["signals"].get_by_id(sig.id)
    assert updated is not None
    assert updated.rejection_reason == "Risk limit exceeded"


def test_signal_count_today(repositories: dict) -> None:
    """Contar señales generadas hoy."""
    # Crear 3 señales con timestamp de hoy
    for _ in range(3):
        repositories["signals"].create(_signal_data())

    count = repositories["signals"].count_today()
    assert count >= 3

    # Filtrar por estrategia
    count_strategy = repositories["signals"].count_today(strategy_name="retest_strategy")
    assert count_strategy >= 3

    count_other = repositories["signals"].count_today(strategy_name="breakout_strategy")
    assert count_other == 0


# ─────────────────────────── OrderRepository ────────────────────────────


def test_order_create_and_update_status(repositories: dict) -> None:
    """Crear orden y actualizar su estado."""
    order = _make_order(repositories)
    assert order.status == "pending"
    assert order.id is not None

    repositories["orders"].update_status(order.id, "filled", mt5_ticket=987654)

    pending = repositories["orders"].get_pending()
    ids = [o.id for o in pending]
    assert order.id not in ids

    updated = repositories["orders"].get_by_mt5_ticket(987654)
    assert updated is not None
    assert updated.status == "filled"
    assert updated.mt5_ticket == 987654


def test_order_get_pending(repositories: dict) -> None:
    """get_pending retorna solo órdenes en estado 'pending'."""
    order1 = _make_order(repositories)
    order2 = _make_order(repositories)
    _make_order(repositories)  # tercera orden, también pending

    # Cambiar una a filled
    repositories["orders"].update_status(order1.id, "filled")

    pending = repositories["orders"].get_pending()
    pending_ids = [o.id for o in pending]

    assert order1.id not in pending_ids
    assert order2.id in pending_ids


# ─────────────────────────── TradeRepository ────────────────────────────


def test_trade_create_and_close(repositories: dict) -> None:
    """Crear trade, cerrarlo y verificar campos de cierre."""
    order = _make_order(repositories)
    trade = repositories["trades"].create(_trade_data(order.id, mt5_ticket=555111))

    assert trade.id is not None
    assert trade.closed_at is None

    close_time = _now()
    repositories["trades"].close(
        trade_id=trade.id,
        exit_price=1.09000,
        closed_at=close_time,
        close_reason="TP",
        pnl_pips=50.0,
        pnl_currency=50.0,
    )

    open_trades = repositories["trades"].get_open()
    assert trade.id not in [t.id for t in open_trades]

    by_ticket = repositories["trades"].get_by_mt5_ticket(555111)
    assert by_ticket is not None
    assert float(by_ticket.exit_price) == pytest.approx(1.09000, abs=1e-5)
    assert by_ticket.close_reason == "TP"


def test_trade_get_consecutive_losses(repositories: dict) -> None:
    """3 pérdidas consecutivas → get_consecutive_losses() == 3."""
    order1 = _make_order(repositories)
    order2 = _make_order(repositories)
    order3 = _make_order(repositories)

    base_time = _now() - timedelta(minutes=30)

    for i, (order, pnl) in enumerate(
        [(order1, -20.0), (order2, -15.0), (order3, -10.0)]
    ):
        trade = repositories["trades"].create(
            _trade_data(order.id, mt5_ticket=200000 + i, opened_at=base_time + timedelta(minutes=i))
        )
        repositories["trades"].close(
            trade_id=trade.id,
            exit_price=1.08000,
            closed_at=base_time + timedelta(minutes=i + 1),
            close_reason="SL",
            pnl_pips=-pnl,
            pnl_currency=pnl,
        )

    assert repositories["trades"].get_consecutive_losses() == 3


def test_trade_get_consecutive_losses_with_win(repositories: dict) -> None:
    """2 pérdidas, 1 ganancia, 1 pérdida → get_consecutive_losses() == 1."""
    base_time = _now() - timedelta(hours=2)
    scenarios = [
        (-20.0, "SL"),   # pérdida
        (-15.0, "SL"),   # pérdida
        (30.0, "TP"),    # ganancia — rompe la racha
        (-5.0, "SL"),    # pérdida (la más reciente)
    ]

    for i, (pnl, reason) in enumerate(scenarios):
        order = _make_order(repositories)
        trade = repositories["trades"].create(
            _trade_data(order.id, mt5_ticket=300000 + i, opened_at=base_time + timedelta(minutes=i))
        )
        repositories["trades"].close(
            trade_id=trade.id,
            exit_price=1.08200,
            closed_at=base_time + timedelta(minutes=i + 1),
            close_reason=reason,
            pnl_pips=abs(pnl),
            pnl_currency=pnl,
        )

    assert repositories["trades"].get_consecutive_losses() == 1


# ─────────────────────────── DrawdownRepository ─────────────────────────


def test_drawdown_create_and_get_latest(repositories: dict) -> None:
    """Crear snapshot de drawdown y recuperar el más reciente."""
    snap1 = repositories["drawdown"].create_snapshot({
        "recorded_at": _now() - timedelta(hours=1),
        "balance": 10000.00,
        "equity": 9900.00,
        "daily_pnl": -100.00,
        "daily_drawdown_pct": 1.000,
        "weekly_pnl": -150.00,
        "weekly_drawdown_pct": 1.500,
        "monthly_pnl": -200.00,
        "monthly_drawdown_pct": 2.000,
        "open_positions": 2,
        "consecutive_losses": 1,
        "bot_mode": "SHADOW",
    })

    snap2 = repositories["drawdown"].create_snapshot({
        "recorded_at": _now(),
        "balance": 9950.00,
        "equity": 9800.00,
        "daily_pnl": -200.00,
        "daily_drawdown_pct": 2.000,
        "weekly_pnl": -300.00,
        "weekly_drawdown_pct": 3.000,
        "monthly_pnl": -350.00,
        "monthly_drawdown_pct": 3.500,
        "open_positions": 1,
        "consecutive_losses": 2,
        "bot_mode": "SHADOW",
    })

    latest = repositories["drawdown"].get_latest()
    assert latest is not None
    assert latest.id == snap2.id
    assert float(latest.daily_drawdown_pct) == pytest.approx(2.000, abs=0.001)


# ─────────────────────────── BotEventRepository ─────────────────────────


def test_bot_event_log_and_get_recent(repositories: dict) -> None:
    """Registrar eventos y recuperarlos con filtro de severidad."""
    repositories["bot_events"].log(
        event_type="MT5_CONNECTED",
        severity="INFO",
        module="bot.core.connector",
        message="Connected to MT5",
        context={"server": "Pepperstone-Demo"},
    )
    repositories["bot_events"].log(
        event_type="RISK_LIMIT_HIT",
        severity="WARNING",
        module="bot.risk.limits",
        message="Daily drawdown limit reached",
    )
    repositories["bot_events"].log(
        event_type="SYSTEM_ERROR",
        severity="ERROR",
        module="bot.core.engine",
        message="Unexpected exception in main loop",
    )

    all_events = repositories["bot_events"].get_recent(limit=100)
    assert len(all_events) >= 3

    warnings = repositories["bot_events"].get_recent(severity="WARNING", limit=100)
    assert any(e.event_type == "RISK_LIMIT_HIT" for e in warnings)
    assert all(e.severity == "WARNING" for e in warnings)

    errors = repositories["bot_events"].get_recent(severity="ERROR", limit=100)
    assert any(e.event_type == "SYSTEM_ERROR" for e in errors)


# ─────────────────────────── MT5ConnectionRepository ────────────────────


def test_mt5_connection_log_event(repositories: dict) -> None:
    """Registrar eventos de conexión y contar desconexiones."""
    repositories["mt5_conn"].log_event("connected")
    repositories["mt5_conn"].log_event("disconnected")
    repositories["mt5_conn"].log_event(
        "reconnect_attempt",
        attempt=1,
        error_code=6,
        error_message="No connection",
    )
    repositories["mt5_conn"].log_event("reconnect_success")

    disconnections = repositories["mt5_conn"].get_disconnections_today()
    assert disconnections >= 1

    uptime = repositories["mt5_conn"].get_uptime_pct_last_24h()
    assert 0.0 <= uptime <= 100.0
