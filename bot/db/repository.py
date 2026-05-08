"""Repositorios de acceso a datos. Abstrae las queries de la lógica de negocio.

Cada repositorio recibe una sesión SQLAlchemy en su constructor y usa la
API select() de SQLAlchemy 2.0. Las excepciones NO se silencian.
"""

from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from bot.db.models import (
    Signal,
    Order,
    Trade,
    DrawdownSnapshot,
    BotEvent,
    MT5ConnectionLog,
    RiskPause,
)


def _today_start() -> datetime:
    """Retorna el inicio del día actual en UTC."""
    today = datetime.now(timezone.utc).date()
    return datetime(today.year, today.month, today.day, tzinfo=timezone.utc)


def _week_start() -> datetime:
    """Retorna el inicio de la semana actual (lunes) en UTC."""
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)


def _month_start() -> datetime:
    """Retorna el inicio del mes actual en UTC."""
    today = datetime.now(timezone.utc).date()
    return datetime(today.year, today.month, 1, tzinfo=timezone.utc)


class SignalRepository:
    """Repositorio para la tabla signals."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, signal_data: dict) -> Signal:
        """Crea y persiste una nueva señal.

        Args:
            signal_data: Diccionario con los campos del modelo Signal.

        Returns:
            Instancia de Signal persistida (con id asignado tras flush).
        """
        signal = Signal(**signal_data)
        self.session.add(signal)
        self.session.flush()
        return signal

    def get_by_id(self, signal_id: int) -> Signal | None:
        """Recupera una señal por su PK."""
        stmt = select(Signal).where(Signal.id == signal_id)
        return self.session.scalar(stmt)

    def get_recent(self, symbol: str | None = None, limit: int = 50) -> list[Signal]:
        """Recupera las señales más recientes, opcionalmente filtradas por símbolo.

        Args:
            symbol: Filtrar por símbolo (ej. "EURUSD"). None = todos.
            limit: Máximo número de resultados.

        Returns:
            Lista de Signal ordenada por generated_at DESC.
        """
        stmt = select(Signal).order_by(Signal.generated_at.desc()).limit(limit)
        if symbol is not None:
            stmt = stmt.where(Signal.symbol == symbol)
        return list(self.session.scalars(stmt).all())

    def mark_acted_on(self, signal_id: int) -> None:
        """Marca la señal como actuada."""
        signal = self.get_by_id(signal_id)
        if signal is not None:
            signal.acted_on = True
            self.session.flush()

    def mark_rejected(self, signal_id: int, reason: str) -> None:
        """Marca la señal como rechazada y registra el motivo."""
        signal = self.get_by_id(signal_id)
        if signal is not None:
            signal.rejection_reason = reason
            self.session.flush()

    def count_today(self, strategy_name: str | None = None) -> int:
        """Cuenta las señales generadas hoy.

        Args:
            strategy_name: Filtrar por nombre de estrategia. None = todas.

        Returns:
            Número de señales generadas desde el inicio del día UTC.
        """
        stmt = select(func.count()).select_from(Signal).where(
            Signal.generated_at >= _today_start()
        )
        if strategy_name is not None:
            stmt = stmt.where(Signal.strategy_name == strategy_name)
        return self.session.scalar(stmt) or 0


class OrderRepository:
    """Repositorio para la tabla orders."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, order_data: dict) -> Order:
        """Crea y persiste una nueva orden.

        Args:
            order_data: Diccionario con los campos del modelo Order.

        Returns:
            Instancia de Order persistida.
        """
        order = Order(**order_data)
        self.session.add(order)
        self.session.flush()
        return order

    def update_status(
        self,
        order_id: int,
        status: str,
        mt5_ticket: int | None = None,
    ) -> None:
        """Actualiza el estado de una orden.

        Args:
            order_id: PK de la orden.
            status: Nuevo estado (pending / filled / rejected / cancelled).
            mt5_ticket: Ticket MT5 si aplica.
        """
        stmt = select(Order).where(Order.id == order_id)
        order = self.session.scalar(stmt)
        if order is not None:
            order.status = status
            if mt5_ticket is not None:
                order.mt5_ticket = mt5_ticket
            self.session.flush()

    def mark_rejected(self, order_id: int, reason: str) -> None:
        """Marca la orden como rechazada."""
        stmt = select(Order).where(Order.id == order_id)
        order = self.session.scalar(stmt)
        if order is not None:
            order.status = "rejected"
            order.rejection_reason = reason
            self.session.flush()

    def get_pending(self) -> list[Order]:
        """Retorna todas las órdenes en estado 'pending'."""
        stmt = select(Order).where(Order.status == "pending").order_by(Order.created_at.asc())
        return list(self.session.scalars(stmt).all())

    def get_by_mt5_ticket(self, ticket: int) -> Order | None:
        """Busca una orden por su ticket MT5."""
        stmt = select(Order).where(Order.mt5_ticket == ticket)
        return self.session.scalar(stmt)


class TradeRepository:
    """Repositorio para la tabla trades."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, trade_data: dict) -> Trade:
        """Crea y persiste un nuevo trade.

        Args:
            trade_data: Diccionario con los campos del modelo Trade.

        Returns:
            Instancia de Trade persistida.
        """
        trade = Trade(**trade_data)
        self.session.add(trade)
        self.session.flush()
        return trade

    def close(
        self,
        trade_id: int,
        exit_price: float,
        closed_at: datetime,
        close_reason: str,
        pnl_pips: float,
        pnl_currency: float,
    ) -> None:
        """Cierra un trade con sus métricas finales.

        Args:
            trade_id: PK del trade.
            exit_price: Precio de cierre.
            closed_at: Timestamp de cierre (timezone-aware).
            close_reason: Razón de cierre (SL / TP / manual / time / session_end / risk_limit).
            pnl_pips: PnL en pips.
            pnl_currency: PnL en moneda de la cuenta.
        """
        stmt = select(Trade).where(Trade.id == trade_id)
        trade = self.session.scalar(stmt)
        if trade is not None:
            trade.exit_price = exit_price
            trade.closed_at = closed_at
            trade.close_reason = close_reason
            trade.pnl_pips = pnl_pips
            trade.pnl_currency = pnl_currency
            self.session.flush()

    def get_open(self) -> list[Trade]:
        """Retorna todos los trades abiertos (sin closed_at)."""
        stmt = select(Trade).where(Trade.closed_at.is_(None)).order_by(Trade.opened_at.asc())
        return list(self.session.scalars(stmt).all())

    def get_by_mt5_ticket(self, ticket: int) -> Trade | None:
        """Busca un trade por su ticket MT5."""
        stmt = select(Trade).where(Trade.mt5_ticket == ticket)
        return self.session.scalar(stmt)

    def get_today(self) -> list[Trade]:
        """Retorna los trades abiertos hoy (opened_at >= inicio del día UTC)."""
        stmt = select(Trade).where(
            Trade.opened_at >= _today_start()
        ).order_by(Trade.opened_at.asc())
        return list(self.session.scalars(stmt).all())

    def get_this_week(self) -> list[Trade]:
        """Retorna los trades abiertos esta semana."""
        stmt = select(Trade).where(
            Trade.opened_at >= _week_start()
        ).order_by(Trade.opened_at.asc())
        return list(self.session.scalars(stmt).all())

    def get_this_month(self) -> list[Trade]:
        """Retorna los trades abiertos este mes."""
        stmt = select(Trade).where(
            Trade.opened_at >= _month_start()
        ).order_by(Trade.opened_at.asc())
        return list(self.session.scalars(stmt).all())

    def get_consecutive_losses(self) -> int:
        """Cuenta las pérdidas consecutivas desde el último trade cerrado hacia atrás.

        Itera desde el trade más reciente hacia el más antiguo hasta encontrar
        un trade ganador (pnl_currency >= 0) o llegar al inicio.

        Returns:
            Número de pérdidas consecutivas al final de la serie.
        """
        stmt = (
            select(Trade)
            .where(Trade.closed_at.isnot(None))
            .order_by(Trade.closed_at.desc())
        )
        closed_trades = list(self.session.scalars(stmt).all())

        count = 0
        for trade in closed_trades:
            pnl = float(trade.pnl_currency) if trade.pnl_currency is not None else 0.0
            if pnl < 0:
                count += 1
            else:
                break
        return count


class DrawdownRepository:
    """Repositorio para la tabla drawdown_snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_snapshot(self, snapshot_data: dict) -> DrawdownSnapshot:
        """Persiste un nuevo snapshot de drawdown.

        Args:
            snapshot_data: Diccionario con los campos del modelo DrawdownSnapshot.

        Returns:
            Instancia de DrawdownSnapshot persistida.
        """
        snapshot = DrawdownSnapshot(**snapshot_data)
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def get_latest(self) -> DrawdownSnapshot | None:
        """Retorna el snapshot más reciente."""
        stmt = select(DrawdownSnapshot).order_by(DrawdownSnapshot.recorded_at.desc()).limit(1)
        return self.session.scalar(stmt)

    def get_max_daily_drawdown_today(self) -> float:
        """Máximo drawdown diario (%) registrado hoy."""
        stmt = select(func.max(DrawdownSnapshot.daily_drawdown_pct)).where(
            DrawdownSnapshot.recorded_at >= _today_start()
        )
        result = self.session.scalar(stmt)
        return float(result) if result is not None else 0.0

    def get_max_weekly_drawdown(self) -> float:
        """Máximo drawdown semanal (%) registrado esta semana."""
        stmt = select(func.max(DrawdownSnapshot.weekly_drawdown_pct)).where(
            DrawdownSnapshot.recorded_at >= _week_start()
        )
        result = self.session.scalar(stmt)
        return float(result) if result is not None else 0.0

    def get_max_monthly_drawdown(self) -> float:
        """Máximo drawdown mensual (%) registrado este mes."""
        stmt = select(func.max(DrawdownSnapshot.monthly_drawdown_pct)).where(
            DrawdownSnapshot.recorded_at >= _month_start()
        )
        result = self.session.scalar(stmt)
        return float(result) if result is not None else 0.0


class BotEventRepository:
    """Repositorio para la tabla bot_events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def log(
        self,
        event_type: str,
        severity: str,
        module: str,
        message: str,
        context: dict | None = None,
    ) -> BotEvent:
        """Registra un evento del bot en la base de datos.

        Args:
            event_type: Tipo de evento (ej. "MT5_DISCONNECTED").
            severity: Nivel de severidad: INFO / WARNING / ERROR / CRITICAL.
            module: Módulo de origen (ej. "bot.core.connector").
            message: Mensaje legible por humanos.
            context: Datos adicionales como dict, opcional.

        Returns:
            Instancia de BotEvent persistida.
        """
        event = BotEvent(
            occurred_at=datetime.now(timezone.utc),
            event_type=event_type,
            severity=severity.upper(),
            module=module,
            message=message,
            context=context,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def get_recent(self, severity: str | None = None, limit: int = 100) -> list[BotEvent]:
        """Retorna los eventos más recientes.

        Args:
            severity: Filtrar por nivel (INFO / WARNING / ERROR / CRITICAL). None = todos.
            limit: Máximo de resultados.

        Returns:
            Lista de BotEvent ordenada por occurred_at DESC.
        """
        stmt = select(BotEvent).order_by(BotEvent.occurred_at.desc()).limit(limit)
        if severity is not None:
            stmt = stmt.where(BotEvent.severity == severity.upper())
        return list(self.session.scalars(stmt).all())

    def get_critical_today(self) -> list[BotEvent]:
        """Retorna los eventos CRITICAL registrados hoy."""
        stmt = (
            select(BotEvent)
            .where(
                and_(
                    BotEvent.severity == "CRITICAL",
                    BotEvent.occurred_at >= _today_start(),
                )
            )
            .order_by(BotEvent.occurred_at.desc())
        )
        return list(self.session.scalars(stmt).all())


class MT5ConnectionRepository:
    """Repositorio para la tabla mt5_connection_log."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def log_event(
        self,
        event: str,
        attempt: int | None = None,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Registra un evento de conexión MT5.

        Args:
            event: Tipo de evento: connected / disconnected / reconnect_attempt /
                   reconnect_success / reconnect_failed.
            attempt: Número de intento de reconexión (si aplica).
            error_code: Código de error MT5 (si aplica).
            error_message: Mensaje de error (si aplica).
        """
        log_entry = MT5ConnectionLog(
            recorded_at=datetime.now(timezone.utc),
            event=event,
            attempt_number=attempt,
            error_code=error_code,
            error_message=error_message,
        )
        self.session.add(log_entry)
        self.session.flush()

    def get_disconnections_today(self) -> int:
        """Cuenta los eventos 'disconnected' registrados hoy."""
        stmt = select(func.count()).select_from(MT5ConnectionLog).where(
            and_(
                MT5ConnectionLog.event == "disconnected",
                MT5ConnectionLog.recorded_at >= _today_start(),
            )
        )
        return self.session.scalar(stmt) or 0

    def get_uptime_pct_last_24h(self) -> float:
        """Estima el porcentaje de tiempo conectado en las últimas 24 horas.

        Estrategia: cuenta eventos 'connected' vs 'disconnected' en las últimas 24h.
        Si hay más conexiones que desconexiones, la proporción de tiempo conectado
        es mayor. Retorna el ratio connected / (connected + disconnected) * 100.

        Returns:
            Porcentaje de uptime (0.0 a 100.0). 100.0 si no hay eventos.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        stmt = (
            select(MT5ConnectionLog.event, func.count().label("cnt"))
            .where(MT5ConnectionLog.recorded_at >= since)
            .group_by(MT5ConnectionLog.event)
        )
        rows = self.session.execute(stmt).all()

        counts: dict[str, int] = {row.event: row.cnt for row in rows}
        connected = counts.get("connected", 0)
        disconnected = counts.get("disconnected", 0)
        total = connected + disconnected

        if total == 0:
            return 100.0  # Sin eventos asumimos que estuvo conectado

        return round(connected / total * 100, 2)


class RiskPauseRepository:
    """Repositorio para risk_pauses."""
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_pause(self, paused_at: datetime, resume_at: datetime | None,
                     reason: str, severity: str) -> RiskPause:
        pause = RiskPause(paused_at=paused_at, resume_at=resume_at,
                          reason=reason, severity=severity, active=True)
        self.session.add(pause)
        self.session.flush()
        return pause

    def get_active(self) -> RiskPause | None:
        stmt = select(RiskPause).where(RiskPause.active == True).order_by(RiskPause.paused_at.desc())
        return self.session.scalar(stmt)

    def deactivate_all(self) -> None:
        stmt = select(RiskPause).where(RiskPause.active == True)
        pauses = list(self.session.scalars(stmt).all())
        for p in pauses:
            p.active = False
        self.session.flush()

    def get_history(self, limit: int = 50) -> list[RiskPause]:
        stmt = select(RiskPause).order_by(RiskPause.paused_at.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())
