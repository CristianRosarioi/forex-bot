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


def _close_loss(repositories: dict, ticket: int, closed_at: datetime,
                bot_mode: str = "DEMO", pnl: float = -10.0) -> None:
    """Crea y cierra un trade perdedor con modo y timestamp explícitos."""
    order = _make_order(repositories)
    trade = repositories["trades"].create(
        _trade_data(order.id, mt5_ticket=ticket, bot_mode=bot_mode,
                    opened_at=closed_at - timedelta(minutes=1))
    )
    repositories["trades"].close(
        trade_id=trade.id,
        exit_price=1.08000,
        closed_at=closed_at,
        close_reason="SL",
        pnl_pips=pnl,
        pnl_currency=pnl,
    )


def test_consecutive_losses_filters_by_bot_mode(repositories: dict) -> None:
    """bot_mode='DEMO' ignora las pérdidas de PAPER (no contaminan la racha)."""
    base = _now() - timedelta(hours=1)
    # 2 pérdidas DEMO y 3 PAPER intercaladas
    _close_loss(repositories, 400000, base + timedelta(minutes=1), bot_mode="DEMO")
    _close_loss(repositories, 400001, base + timedelta(minutes=2), bot_mode="PAPER")
    _close_loss(repositories, 400002, base + timedelta(minutes=3), bot_mode="PAPER")
    _close_loss(repositories, 400003, base + timedelta(minutes=4), bot_mode="DEMO")
    _close_loss(repositories, 400004, base + timedelta(minutes=5), bot_mode="PAPER")

    assert repositories["trades"].get_consecutive_losses(bot_mode="DEMO") == 2
    assert repositories["trades"].get_consecutive_losses(bot_mode="PAPER") == 3
    # Sin filtro cuenta todo (5)
    assert repositories["trades"].get_consecutive_losses() == 5


def test_consecutive_losses_since_only_counts_after(repositories: dict) -> None:
    """since=X sólo cuenta trades cerrados después de X."""
    base = _now() - timedelta(hours=2)
    _close_loss(repositories, 410000, base + timedelta(minutes=1), bot_mode="DEMO")
    _close_loss(repositories, 410001, base + timedelta(minutes=2), bot_mode="DEMO")
    cutoff = base + timedelta(minutes=3)
    _close_loss(repositories, 410002, base + timedelta(minutes=4), bot_mode="DEMO")
    _close_loss(repositories, 410003, base + timedelta(minutes=5), bot_mode="DEMO")

    # Toda la historia: 4 pérdidas
    assert repositories["trades"].get_consecutive_losses(bot_mode="DEMO") == 4
    # Sólo posteriores al cutoff: 2
    assert repositories["trades"].get_consecutive_losses(bot_mode="DEMO", since=cutoff) == 2


def test_consecutive_losses_since_resets_to_zero_after_pause(repositories: dict) -> None:
    """Tras una pausa servida (cutoff posterior al último trade), la racha
    efectiva vuelve a 0 → esto rompe el deadlock."""
    base = _now() - timedelta(hours=2)
    for i in range(6):
        _close_loss(repositories, 420000 + i, base + timedelta(minutes=i), bot_mode="DEMO")

    # La racha bruta es 6 (el límite) — el bot quedaría bloqueado para siempre.
    assert repositories["trades"].get_consecutive_losses(bot_mode="DEMO") == 6
    # Pero con un corte posterior a todos los trades, la racha efectiva es 0.
    cutoff_after_all = base + timedelta(minutes=10)
    assert repositories["trades"].get_consecutive_losses(bot_mode="DEMO", since=cutoff_after_all) == 0


# ─────────────────────────── RiskPauseRepository ─────────────────────────


def test_deactivate_expired_preserves_active_and_permanent(repositories: dict) -> None:
    """deactivate_expired() sólo desactiva pausas vencidas; preserva las vigentes
    y las permanentes (resume_at IS NULL, p.ej. kill switch).

    Insertamos las filas directamente (sin create_pause) para aislar el método:
    create_pause ya invoca deactivate_expired internamente.
    """
    from bot.db.models import RiskPause
    from sqlalchemy import select
    session = repositories["risk_pauses"].session
    now = _now()
    session.add_all([
        # Vencida (debe desactivarse)
        RiskPause(paused_at=now - timedelta(hours=5), resume_at=now - timedelta(hours=1),
                  reason="Max consecutive losses reached: 6/6", severity="WARNING", active=True),
        # Vigente (se preserva)
        RiskPause(paused_at=now - timedelta(minutes=5), resume_at=now + timedelta(hours=1),
                  reason="Max consecutive losses reached: 6/6", severity="WARNING", active=True),
        # Permanente / kill switch (se preserva)
        RiskPause(paused_at=now - timedelta(hours=2), resume_at=None,
                  reason="Kill switch triggered", severity="CRITICAL", active=True),
    ])
    session.flush()

    deactivated = repositories["risk_pauses"].deactivate_expired()
    assert deactivated == 1  # sólo la vencida

    still_active = list(session.scalars(
        select(RiskPause).where(RiskPause.active == True)).all())
    assert len(still_active) == 2  # vigente + permanente
    assert all(p.resume_at is None or p.resume_at > now for p in still_active)


def test_create_pause_deactivates_prior_expired(repositories: dict) -> None:
    """Al crear una pausa nueva no se acumulan pausas activas vencidas."""
    repo = repositories["risk_pauses"]
    now = _now()
    # Tres pausas vencidas (simula la fuga histórica)
    for i in range(3):
        repo.create_pause(paused_at=now - timedelta(hours=5 - i),
                          resume_at=now - timedelta(hours=4 - i),
                          reason="Max consecutive losses reached: 6/6", severity="WARNING")
    # Una pausa nueva vigente
    repo.create_pause(paused_at=now, resume_at=now + timedelta(hours=4),
                      reason="Max consecutive losses reached: 6/6", severity="WARNING")

    from bot.db.models import RiskPause
    from sqlalchemy import select
    active = list(repo.session.scalars(select(RiskPause).where(RiskPause.active == True)).all())
    # Sólo la vigente debe quedar activa
    assert len(active) == 1
    assert active[0].resume_at > now


def test_get_last_expired_pause_by_prefix(repositories: dict) -> None:
    """Devuelve la pausa por consecutive losses más reciente que ya expiró."""
    repo = repositories["risk_pauses"]
    now = _now()
    prefix = "Max consecutive losses reached"
    repo.create_pause(paused_at=now - timedelta(hours=10),
                      resume_at=now - timedelta(hours=9),
                      reason=f"{prefix}: 6/6", severity="WARNING")
    repo.create_pause(paused_at=now - timedelta(hours=5),
                      resume_at=now - timedelta(hours=4),
                      reason=f"{prefix}: 6/6", severity="WARNING")
    # Una pausa de OTRO tipo (no debe seleccionarse)
    repo.create_pause(paused_at=now - timedelta(hours=1),
                      resume_at=now - timedelta(minutes=30),
                      reason="Daily drawdown limit hit", severity="ERROR")
    # Una pausa de consecutive losses aún vigente (no debe seleccionarse: no ha expirado)
    repo.create_pause(paused_at=now, resume_at=now + timedelta(hours=1),
                      reason=f"{prefix}: 6/6", severity="WARNING")

    result = repo.get_last_expired_pause_by_prefix(prefix)
    assert result is not None
    # La más reciente expirada de ese prefijo es la de hace 5 horas
    assert abs((result.paused_at - (now - timedelta(hours=5))).total_seconds()) < 2


def test_count_pauses_by_prefix_since(repositories: dict) -> None:
    """Cuenta pausas por prefijo dentro de la ventana, activas e inactivas."""
    repo = repositories["risk_pauses"]
    now = _now()
    prefix = "Max consecutive losses reached"
    # 2 dentro de la ventana de 24h
    repo.create_pause(paused_at=now - timedelta(hours=3), resume_at=now - timedelta(hours=2),
                      reason=f"{prefix}: 6/6", severity="WARNING")
    repo.create_pause(paused_at=now - timedelta(hours=1), resume_at=now + timedelta(hours=3),
                      reason=f"{prefix}: 6/6", severity="WARNING")
    # 1 fuera de la ventana (hace 30h)
    repo.create_pause(paused_at=now - timedelta(hours=30), resume_at=now - timedelta(hours=29),
                      reason=f"{prefix}: 6/6", severity="WARNING")
    # 1 de otro tipo dentro de la ventana (no debe contar)
    repo.create_pause(paused_at=now - timedelta(hours=2), resume_at=now - timedelta(hours=1),
                      reason="Daily drawdown limit hit", severity="ERROR")

    since = now - timedelta(hours=24)
    assert repo.count_pauses_by_prefix_since(prefix, since) == 2


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
