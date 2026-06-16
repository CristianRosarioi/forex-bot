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
_GOLD_SYMBOLS = {"XAUUSD"}


def _pip_size(symbol: str) -> float:
    if symbol in _GOLD_SYMBOLS:
        return 0.1
    if symbol in _JPY_PAIRS:
        return 0.01
    return 0.0001


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


MAGIC_NUMBER = 234000
MT5_RETCODE_INVALID_FILL = 10030   # IOC no soportado por el servidor — reintentar con RETURN


class RealOrderManager:
    """Envía órdenes reales a MT5. Solo para DEMO y LIVE."""

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
        """Envía orden real a MT5, persiste resultado y publica eventos."""
        import MetaTrader5 as mt5

        # 1. Verify MT5 connection
        try:
            self._connector.ensure_connected()
        except RuntimeError as exc:
            logger.error("[DEMO] Cannot execute order — MT5 not connected: %s", exc)
            return None

        # 2. Get current price
        tick = mt5.symbol_info_tick(signal.symbol)
        if tick is None:
            logger.warning("[DEMO] No tick data for %s — cannot execute", signal.symbol)
            return None

        price = float(tick.ask) if signal.direction == "BUY" else float(tick.bid)
        fill_type = mt5.ORDER_TYPE_BUY if signal.direction == "BUY" else mt5.ORDER_TYPE_SELL
        float_lots = float(lots)
        now = datetime.now(timezone.utc)

        # 3. Build request — IOC first (Pepperstone standard)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": float_lots,
            "type": fill_type,
            "price": price,
            "sl": signal.sl_price,
            "tp": signal.tp_price if signal.tp_price else 0.0,
            "deviation": 20,
            "magic": MAGIC_NUMBER,
            "comment": f"forex-bot:{signal.strategy_name}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        logger.info("[DEMO] Sending order: %s", request)
        result = mt5.order_send(request)
        logger.info(
            "[DEMO] order_send result: retcode=%s order=%s price=%s comment=%s",
            getattr(result, "retcode", None),
            getattr(result, "order", None),
            getattr(result, "price", None),
            getattr(result, "comment", None),
        )

        # 4. Retry with RETURN if IOC filling not accepted by server
        if result is not None and result.retcode == MT5_RETCODE_INVALID_FILL:
            request["type_filling"] = mt5.ORDER_FILLING_RETURN
            logger.warning("[DEMO] IOC filling rejected (10030) — retrying with RETURN")
            result = mt5.order_send(request)
            logger.info(
                "[DEMO] Retry result: retcode=%s order=%s",
                getattr(result, "retcode", None),
                getattr(result, "order", None),
            )

        # 5. Handle failure — always persist the attempt
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = getattr(result, "retcode", -1)
            comment = getattr(result, "comment", "no result")
            logger.error("[DEMO] Order rejected: retcode=%s comment=%s", retcode, comment)

            with get_session() as session:
                order_repo = OrderRepository(session)
                rejected_order = order_repo.create({
                    "signal_id": signal_id,
                    "created_at": now,
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "requested_price": signal.entry_price,
                    "requested_volume": float_lots,
                    "sl_price": signal.sl_price,
                    "tp_price": signal.tp_price,
                    "mt5_ticket": None,
                    "status": "rejected",
                    "rejection_reason": f"MT5 retcode={retcode}: {comment}",
                    "bot_mode": "DEMO",
                })
                session.expunge(rejected_order)

            self._bus.publish(EventType.ORDER_REJECTED, {
                "symbol": signal.symbol,
                "direction": signal.direction,
                "retcode": retcode,
                "comment": comment,
                "bot_mode": "DEMO",
            })
            return None

        # 6. Success — persist Order + Trade with real MT5 ticket
        real_ticket = result.order
        fill_price = result.price

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
                "mt5_ticket": real_ticket,
                "status": "filled",
                "rejection_reason": None,
                "bot_mode": "DEMO",
            })

            trade = tr_repo.create({
                "order_id": order.id,
                "mt5_ticket": real_ticket,
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
                "commission": float(getattr(result, "commission", 0.0) or 0.0),
                "swap": 0.0,
                "strategy_name": signal.strategy_name,
                "bot_mode": "DEMO",
            })

            session.expunge(order)
            session.expunge(trade)

        logger.info(
            "[DEMO] Order filled: %s %s %.2f lots @ %.5f sl=%.5f tp=%s ticket=%d",
            signal.direction, signal.symbol, float_lots, fill_price,
            signal.sl_price, signal.tp_price, real_ticket,
        )

        self._bus.publish(EventType.ORDER_PLACED, {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "lots": float_lots,
            "price": price,
            "sl_price": signal.sl_price,
            "tp_price": signal.tp_price,
            "bot_mode": "DEMO",
        })
        self._bus.publish(EventType.ORDER_FILLED, {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "lots": float_lots,
            "fill_price": fill_price,
            "sl_price": signal.sl_price,
            "tp_price": signal.tp_price,
            "trade_id": trade.id,
            "mt5_ticket": real_ticket,
            "bot_mode": "DEMO",
        })

        return trade
