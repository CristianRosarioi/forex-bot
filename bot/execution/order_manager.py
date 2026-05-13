"""Envío y modificación de órdenes a MT5. Solo acepta órdenes pre-validadas por bot/risk/validator.py."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from bot.db.repository import OrderRepository, TradeRepository
from bot.core.event_bus import EventType
from bot.db.session import get_session
from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from bot.core.connector import MT5Connector
    from bot.core.event_bus import EventBus
    from bot.db.repository import SignalRepository
    from bot.strategy.base import Signal
    from bot.db.models import Trade

logger = get_logger(__name__)

MAX_SLIPPAGE_PIPS = 3

_JPY_PAIRS = {"USDJPY", "GBPJPY", "EURJPY", "CADJPY", "AUDJPY", "NZDJPY", "CHFJPY"}


def _pip_size(symbol: str) -> float:
    return 0.01 if symbol in _JPY_PAIRS else 0.0001


class VirtualOrderManager:
    """Simula ejecución contra precio real de MT5. No envía órdenes reales al broker."""

    def __init__(
        self,
        connector: "MT5Connector",
        event_bus: "EventBus",
        trade_repo: "TradeRepository",
        signal_repo: "SignalRepository",
    ) -> None:
        self._connector = connector
        self._bus = event_bus
        self._trade_repo = trade_repo
        self._signal_repo = signal_repo

    def execute(
        self,
        signal: "Signal",
        lots: Decimal,
        account_state: dict,
        signal_id: int | None = None,
    ) -> "Trade | None":
        """Simula fill: crea Order + Trade en DB y publica eventos. No toca MT5 para ejecutar."""
        try:
            import MetaTrader5 as mt5
            tick = mt5.symbol_info_tick(signal.symbol)
        except Exception:
            logger.exception("Cannot get tick for %s", signal.symbol)
            return None

        if tick is None:
            logger.warning("No tick data for %s — cannot execute virtual order", signal.symbol)
            return None

        fill_price = float(tick.ask) if signal.direction == "BUY" else float(tick.bid)

        pip = _pip_size(signal.symbol)
        slippage_pips = abs(fill_price - signal.entry_price) / pip
        if slippage_pips > MAX_SLIPPAGE_PIPS:
            logger.warning(
                "[PAPER] High slippage %s: %.1f pips (entry=%.5f fill=%.5f)",
                signal.symbol, slippage_pips, signal.entry_price, fill_price,
            )

        # Timestamp-based fake ticket satisfies Trade.mt5_ticket NOT NULL constraint
        fake_ticket = int(time.time() * 1000) % (2 ** 31)
        now = datetime.now(timezone.utc)
        float_lots = float(lots)

        trade: "Trade | None" = None
        with get_session() as session:
            order_repo = OrderRepository(session)
            tr_repo = TradeRepository(session)

            order = order_repo.create({
                "signal_id": signal_id,
                "created_at": now,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "requested_price": signal.entry_price,
                "requested_volume": float_lots,
                "sl_price": signal.sl_price,
                "tp_price": signal.tp_price,
                "mt5_ticket": None,
                "status": "filled",
                "rejection_reason": None,
                "bot_mode": "PAPER",
            })

            trade = tr_repo.create({
                "order_id": order.id,
                "mt5_ticket": fake_ticket,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "entry_price": fill_price,
                "exit_price": None,
                "volume": float_lots,
                "sl_price": signal.sl_price,
                "tp_price": signal.tp_price,
                "opened_at": now,
                "closed_at": None,
                "close_reason": None,
                "pnl_pips": None,
                "pnl_currency": None,
                "commission": 0.0,
                "swap": 0.0,
                "strategy_name": signal.strategy_name,
                "bot_mode": "PAPER",
            })

            # Detach so attributes remain accessible after session closes
            session.expunge(order)
            session.expunge(trade)

        logger.info(
            "[PAPER] Virtual fill: %s %s %.2f lots @ %.5f sl=%.5f tp=%s ticket=%d",
            signal.direction, signal.symbol, float_lots, fill_price,
            signal.sl_price, signal.tp_price, fake_ticket,
        )

        self._bus.publish(EventType.ORDER_PLACED, {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "lots": float_lots,
            "price": signal.entry_price,
            "sl_price": signal.sl_price,
            "tp_price": signal.tp_price,
            "bot_mode": "PAPER",
        })
        self._bus.publish(EventType.ORDER_FILLED, {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "lots": float_lots,
            "fill_price": fill_price,
            "sl_price": signal.sl_price,
            "tp_price": signal.tp_price,
            "trade_id": trade.id,
            "bot_mode": "PAPER",
        })

        return trade


class RealOrderManager:
    """Ejecuta órdenes reales en MT5. Implementado en Fase 7 (DEMO mode)."""

    def execute(self, signal, lots, account_state) -> "Trade | None":
        """Envía orden real a MT5. Implementado en Fase 7 (DEMO mode)."""
        raise NotImplementedError("RealOrderManager no implementado todavía")
