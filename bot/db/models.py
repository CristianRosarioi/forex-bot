"""Modelos SQLAlchemy 2.0 para el dominio de trading.

Tablas: signals, orders, trades, drawdown_snapshots, bot_events,
        mt5_connection_log, economic_events, risk_pauses.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos."""
    pass


class Signal(Base):
    """Señal de trading generada por una estrategia."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    signal_type: Mapped[str] = mapped_column(String(20), nullable=False)  # retest / breakout / fade
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY / SELL
    entry_price: Mapped[float] = mapped_column(Numeric(12, 5), nullable=False)
    sl_price: Mapped[float] = mapped_column(Numeric(12, 5), nullable=False)
    tp_price: Mapped[float | None] = mapped_column(Numeric(12, 5), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    bot_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    acted_on: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Signal id={self.id} symbol={self.symbol} direction={self.direction} "
            f"strategy={self.strategy_name} acted_on={self.acted_on}>"
        )


class Order(Base):
    """Orden de trading enviada (o intentada) al broker."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("signals.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    requested_price: Mapped[float] = mapped_column(Numeric(12, 5), nullable=False)
    requested_volume: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    sl_price: Mapped[float] = mapped_column(Numeric(12, 5), nullable=False)  # NOT NULL — regla absoluta
    tp_price: Mapped[float | None] = mapped_column(Numeric(12, 5), nullable=True)
    mt5_ticket: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # pending / filled / rejected / cancelled
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_mode: Mapped[str] = mapped_column(String(10), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} symbol={self.symbol} direction={self.direction} "
            f"status={self.status} ticket={self.mt5_ticket}>"
        )


class Trade(Base):
    """Trade ejecutado y rastreado en MT5."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), nullable=False)
    mt5_ticket: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(12, 5), nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric(12, 5), nullable=True)
    volume: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    sl_price: Mapped[float] = mapped_column(Numeric(12, 5), nullable=False)
    tp_price: Mapped[float | None] = mapped_column(Numeric(12, 5), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    close_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)  # SL / TP / manual / time / session_end / risk_limit
    pnl_pips: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    pnl_currency: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    commission: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    swap: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    bot_mode: Mapped[str] = mapped_column(String(10), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Trade id={self.id} symbol={self.symbol} direction={self.direction} "
            f"ticket={self.mt5_ticket} pnl={self.pnl_currency}>"
        )


class DrawdownSnapshot(Base):
    """Snapshot periódico de drawdown y métricas de riesgo."""

    __tablename__ = "drawdown_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    equity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    daily_pnl: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    daily_drawdown_pct: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    weekly_pnl: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    weekly_drawdown_pct: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    monthly_pnl: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    monthly_drawdown_pct: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False)
    bot_mode: Mapped[str] = mapped_column(String(10), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DrawdownSnapshot id={self.id} recorded_at={self.recorded_at} "
            f"daily_dd={self.daily_drawdown_pct}% equity={self.equity}>"
        )


class BotEvent(Base):
    """Registro de eventos importantes del bot."""

    __tablename__ = "bot_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # INFO / WARNING / ERROR / CRITICAL
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<BotEvent id={self.id} event_type={self.event_type} "
            f"severity={self.severity} occurred_at={self.occurred_at}>"
        )


class MT5ConnectionLog(Base):
    """Log de eventos de conexión/desconexión con MT5."""

    __tablename__ = "mt5_connection_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    event: Mapped[str] = mapped_column(String(20), nullable=False)  # connected / disconnected / reconnect_attempt / reconnect_success / reconnect_failed
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MT5ConnectionLog id={self.id} event={self.event} "
            f"recorded_at={self.recorded_at}>"
        )


class EconomicEvent(Base):
    """Evento económico del calendario financiero."""

    __tablename__ = "economic_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(5), nullable=False)
    impact: Mapped[str] = mapped_column(String(10), nullable=False)  # high / medium / low
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    forecast: Mapped[str | None] = mapped_column(String(50), nullable=True)
    previous: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actual: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<EconomicEvent id={self.id} title={self.title!r} "
            f"currency={self.currency} impact={self.impact} event_time={self.event_time}>"
        )


class RiskPause(Base):
    """Pausa de trading activa por límite de riesgo."""
    __tablename__ = "risk_pauses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # WARNING/ERROR/CRITICAL
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<RiskPause id={self.id} reason={self.reason!r} active={self.active} resume_at={self.resume_at}>"
