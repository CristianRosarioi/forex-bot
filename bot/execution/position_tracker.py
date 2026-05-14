"""Seguimiento de posiciones abiertas: breakeven inteligente, cierre por SL/TP/tiempo/sesión."""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from bot.db.repository import TradeRepository, DrawdownRepository
from bot.db.session import get_session
from bot.core.event_bus import EventType
from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from bot.core.connector import MT5Connector
    from bot.core.event_bus import EventBus
    from bot.db.models import Trade
    from config.settings import RiskSettings

logger = get_logger(__name__)

_JPY_PAIRS = {"USDJPY", "GBPJPY", "EURJPY", "CADJPY", "AUDJPY", "NZDJPY", "CHFJPY"}

BREAKEVEN_TRIGGER_R = 1.0
BREAKEVEN_BUFFER_PIPS = 3
BREAKEVEN_MIN_TIME_SECONDS = 3600  # 4 × M15 bars = 60 minutes minimum before moving SL

MAX_TRADE_DURATION_HOURS = 24
SESSION_CLOSE_MIN_LIFE_SECONDS = 1800  # trade must be at least 30 min old before session close


def _pip_size(symbol: str) -> float:
    return 0.01 if symbol in _JPY_PAIRS else 0.0001


class PositionTracker:
    """Monitorea posiciones abiertas cada 30 segundos y aplica gestión inteligente de SL/cierre."""

    def __init__(
        self,
        connector: "MT5Connector",
        event_bus: "EventBus",
        trade_repo: "TradeRepository",
        drawdown_repo: "DrawdownRepository",
        settings: "RiskSettings",
        bot_mode: str = "PAPER",
    ) -> None:
        self._connector = connector
        self._bus = event_bus
        self._trade_repo = trade_repo
        self._drawdown_repo = drawdown_repo
        self._settings = settings
        self._bot_mode = bot_mode.upper()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def monitor_loop(self) -> None:
        """Loop principal que verifica posiciones abiertas cada 30 segundos."""
        logger.info("PositionTracker started")
        while not self._stop_event.is_set():
            try:
                self._check_positions()
            except Exception:
                logger.exception("Error in position monitor loop")
            self._stop_event.wait(timeout=30.0)
        logger.info("PositionTracker stopped")

    def _check_positions(self) -> None:
        """Inspecciona cada posición abierta y aplica la acción correspondiente."""
        # For DEMO/LIVE: first sync any positions MT5 closed server-side (SL/TP hits)
        if self._bot_mode in ("DEMO", "LIVE"):
            self._sync_closed_positions()

        events_to_publish: list[tuple[str, dict]] = []

        with get_session() as session:
            trade_repo = TradeRepository(session)
            drawdown_repo = DrawdownRepository(session)

            if self._bot_mode in ("DEMO", "LIVE"):
                trades = trade_repo.get_open_live()
            else:
                trades = trade_repo.get_open_paper()

            if not trades:
                return

            for trade in trades:
                current_price = self._get_current_price(trade.symbol, trade.direction)
                if current_price is None:
                    continue

                if self._bot_mode in ("DEMO", "LIVE"):
                    # MT5 server manages SL/TP execution; we only track breakeven in DB.
                    # TODO: add mt5.order_modify() call here to also update SL in MT5.
                    new_sl = self._calc_breakeven_sl(trade, current_price)
                    if new_sl is not None:
                        trade_repo.update_sl(trade.id, new_sl)
                        logger.info(
                            "[%s] Breakeven SL moved: %s %s sl %.5f -> %.5f",
                            self._bot_mode, trade.symbol, trade.direction,
                            float(trade.sl_price), new_sl,
                        )
                else:
                    close_reason = self._check_close_conditions(trade, current_price)
                    if close_reason:
                        pnl_pips, pnl_currency = self._calc_pnl(trade, current_price)
                        closed_at = datetime.now(timezone.utc)
                        trade_repo.close(
                            trade_id=trade.id,
                            exit_price=current_price,
                            closed_at=closed_at,
                            close_reason=close_reason,
                            pnl_pips=pnl_pips,
                            pnl_currency=pnl_currency,
                        )
                        logger.info(
                            "[PAPER] Closed %s %s @ %.5f reason=%s pnl=%.2f pips / %.2f",
                            trade.symbol, trade.direction, current_price,
                            close_reason, pnl_pips, pnl_currency,
                        )
                        self._take_snapshot(trade, pnl_currency, trade_repo, drawdown_repo)
                        events_to_publish.append((EventType.POSITION_CLOSED, {
                            "trade_id": trade.id,
                            "symbol": trade.symbol,
                            "direction": trade.direction,
                            "entry_price": float(trade.entry_price),
                            "exit_price": current_price,
                            "close_reason": close_reason,
                            "pnl_pips": pnl_pips,
                            "pnl_currency": pnl_currency,
                            "volume": float(trade.volume),
                            "strategy_name": trade.strategy_name,
                            "bot_mode": trade.bot_mode,
                        }))
                    else:
                        new_sl = self._calc_breakeven_sl(trade, current_price)
                        if new_sl is not None:
                            trade_repo.update_sl(trade.id, new_sl)
                            logger.info(
                                "[PAPER] Breakeven SL moved: %s %s sl %.5f -> %.5f",
                                trade.symbol, trade.direction,
                                float(trade.sl_price), new_sl,
                            )

        for event_type, payload in events_to_publish:
            self._bus.publish(event_type, payload)

    def _sync_closed_positions(self) -> None:
        """Detecta posiciones cerradas por MT5 server-side (SL/TP) y actualiza la DB."""
        import MetaTrader5 as mt5

        events_to_publish: list[tuple[str, dict]] = []

        with get_session() as session:
            trade_repo = TradeRepository(session)
            drawdown_repo = DrawdownRepository(session)

            trades = trade_repo.get_open_live()
            for trade in trades:
                positions = mt5.positions_get(ticket=int(trade.mt5_ticket))
                if positions is not None and len(positions) > 0:
                    continue  # Still open in MT5

                # Position was closed server-side — get exit info from deal history
                exit_price, close_reason = self._get_mt5_close_info(int(trade.mt5_ticket))
                if exit_price is None:
                    exit_price = self._get_current_price(trade.symbol, trade.direction)
                    if exit_price is None:
                        logger.warning(
                            "[%s] Cannot determine exit price for trade %d ticket=%d — skipping",
                            self._bot_mode, trade.id, trade.mt5_ticket,
                        )
                        continue

                pnl_pips, pnl_currency = self._calc_pnl(trade, exit_price)
                closed_at = datetime.now(timezone.utc)

                trade_repo.close(
                    trade_id=trade.id,
                    exit_price=exit_price,
                    closed_at=closed_at,
                    close_reason=close_reason,
                    pnl_pips=pnl_pips,
                    pnl_currency=pnl_currency,
                )
                logger.info(
                    "[%s] MT5-closed position synced: %s %s @ %.5f reason=%s pnl=%.2f pips",
                    self._bot_mode, trade.symbol, trade.direction,
                    exit_price, close_reason, pnl_pips,
                )
                self._take_snapshot(trade, pnl_currency, trade_repo, drawdown_repo)
                events_to_publish.append((EventType.POSITION_CLOSED, {
                    "trade_id": trade.id,
                    "symbol": trade.symbol,
                    "direction": trade.direction,
                    "entry_price": float(trade.entry_price),
                    "exit_price": exit_price,
                    "close_reason": close_reason,
                    "pnl_pips": pnl_pips,
                    "pnl_currency": pnl_currency,
                    "volume": float(trade.volume),
                    "strategy_name": trade.strategy_name,
                    "bot_mode": trade.bot_mode,
                }))

        for event_type, payload in events_to_publish:
            self._bus.publish(event_type, payload)

    def _get_mt5_close_reason(self, ticket: int) -> str:
        """Mapea el deal de cierre de MT5 a close_reason: 'SL', 'TP' o 'unknown'."""
        import MetaTrader5 as mt5
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return "unknown"
        for deal in reversed(deals):
            if deal.entry == mt5.DEAL_ENTRY_OUT:
                return "SL" if deal.profit <= 0 else "TP"
        return "unknown"

    def _get_mt5_close_info(self, ticket: int) -> tuple[float | None, str]:
        """Retorna (exit_price, close_reason) del historial de deals de MT5."""
        import MetaTrader5 as mt5
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return None, "unknown"
        for deal in reversed(deals):
            if deal.entry == mt5.DEAL_ENTRY_OUT:
                reason = "SL" if deal.profit <= 0 else "TP"
                return float(deal.price), reason
        return None, "unknown"

    def _check_close_conditions(self, trade: "Trade", current_price: float) -> str | None:
        """Retorna la razón de cierre si aplica, o None para continuar."""
        entry = float(trade.entry_price)
        sl = float(trade.sl_price)
        tp = trade.tp_price
        now = datetime.now(timezone.utc)
        opened_at = trade.opened_at
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)

        if trade.direction == "BUY":
            if current_price <= sl:
                return "SL"
            if tp is not None and current_price >= float(tp):
                return "TP"
        else:  # SELL
            if current_price >= sl:
                return "SL"
            if tp is not None and current_price <= float(tp):
                return "TP"

        elapsed_hours = (now - opened_at).total_seconds() / 3600
        if elapsed_hours >= MAX_TRADE_DURATION_HOURS:
            return "time"

        elapsed_seconds = (now - opened_at).total_seconds()
        if elapsed_seconds >= SESSION_CLOSE_MIN_LIFE_SECONDS:
            if self._is_session_ended(trade.symbol):
                return "session_end"

        return None

    def _calc_breakeven_sl(self, trade: "Trade", current_price: float) -> float | None:
        """Calcula el nuevo SL para breakeven si las condiciones se cumplen. Retorna None si no aplica."""
        entry = float(trade.entry_price)
        sl = float(trade.sl_price)
        pip = _pip_size(trade.symbol)
        buffer = BREAKEVEN_BUFFER_PIPS * pip

        # Calculate current R
        if trade.direction == "BUY":
            risk = entry - sl
            if risk <= 0:
                return None
            r_current = (current_price - entry) / risk
        else:
            risk = sl - entry
            if risk <= 0:
                return None
            r_current = (entry - current_price) / risk

        if r_current < BREAKEVEN_TRIGGER_R:
            return None

        opened_at = trade.opened_at
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - opened_at).total_seconds()
        if elapsed < BREAKEVEN_MIN_TIME_SECONDS:
            return None

        if trade.direction == "BUY":
            new_sl = entry + buffer
            if new_sl > sl:
                return new_sl
        else:
            new_sl = entry - buffer
            if new_sl < sl:
                return new_sl

        return None

    def _calc_pnl(
        self,
        trade: "Trade",
        exit_price: float,
        pip_value_per_lot: float | None = None,
    ) -> tuple[float, float]:
        """Retorna (pnl_pips, pnl_currency)."""
        entry = float(trade.entry_price)
        volume = float(trade.volume)
        pip = _pip_size(trade.symbol)
        direction_sign = 1.0 if trade.direction == "BUY" else -1.0
        pnl_pips = (exit_price - entry) / pip * direction_sign

        if pip_value_per_lot is None:
            pip_value_per_lot = self._get_pip_value(trade.symbol)

        pnl_currency = pnl_pips * pip_value_per_lot * volume
        return round(pnl_pips, 2), round(pnl_currency, 2)

    def _get_pip_value(self, symbol: str) -> float:
        """Retorna el valor monetario de 1 pip por lote estándar en la moneda de cuenta."""
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(symbol)
            if info is not None:
                # trade_tick_value = account-currency value of 1 point for 1 lot
                # 1 pip = 10 points for 5-digit brokers
                return float(info.trade_tick_value) * 10
        except Exception:
            pass
        # Fallback: ~$10 per pip for USD-denominated accounts, 1 standard lot
        pip = _pip_size(symbol)
        return pip * 1_000_000  # pip × contract_size = 0.0001 × 100,000 = $10

    def _get_current_price(self, symbol: str, direction: str) -> float | None:
        """Obtiene el precio actual desde MT5 (bid para BUY abierto, ask para SELL abierto)."""
        try:
            import MetaTrader5 as mt5
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return None
            # For an open BUY, we close at bid; for an open SELL, we close at ask
            return float(tick.bid) if direction == "BUY" else float(tick.ask)
        except Exception:
            logger.exception("Cannot get price for %s", symbol)
            return None

    def _is_session_ended(self, symbol: str) -> bool:
        """Retorna True si el mercado no está activo para el símbolo."""
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(symbol)
            if info is None:
                return False
            return int(info.trade_mode) == 0
        except Exception:
            return False

    def _take_snapshot(
        self,
        trade: "Trade",
        pnl_currency: float,
        trade_repo: TradeRepository,
        drawdown_repo: DrawdownRepository,
    ) -> None:
        """Persiste un snapshot de drawdown tras cierre de posición."""
        try:
            import MetaTrader5 as mt5
            info = mt5.account_info()
            if info is None:
                return
            balance = float(info.balance)
            equity = float(info.equity)
        except Exception:
            logger.exception("Could not get account info for drawdown snapshot")
            return

        try:
            today_trades = [t for t in trade_repo.get_today() if t.bot_mode == self._bot_mode]
            daily_pnl = sum(float(t.pnl_currency or 0) for t in today_trades)

            week_trades = [
                t for t in trade_repo.get_this_week()
                if t.bot_mode == self._bot_mode and t.closed_at is not None
            ]
            weekly_pnl = sum(float(t.pnl_currency or 0) for t in week_trades)

            month_trades = [
                t for t in trade_repo.get_this_month()
                if t.bot_mode == self._bot_mode and t.closed_at is not None
            ]
            monthly_pnl = sum(float(t.pnl_currency or 0) for t in month_trades)

            open_positions = sum(1 for t in trade_repo.get_open() if t.bot_mode == self._bot_mode)
            consecutive_losses = trade_repo.get_consecutive_losses()

            daily_dd = max(0.0, -daily_pnl / balance * 100) if balance > 0 else 0.0
            weekly_dd = max(0.0, -weekly_pnl / balance * 100) if balance > 0 else 0.0
            monthly_dd = max(0.0, -monthly_pnl / balance * 100) if balance > 0 else 0.0

            drawdown_repo.create_snapshot({
                "recorded_at": datetime.now(timezone.utc),
                "balance": balance,
                "equity": equity,
                "daily_pnl": daily_pnl,
                "daily_drawdown_pct": daily_dd,
                "weekly_pnl": weekly_pnl,
                "weekly_drawdown_pct": weekly_dd,
                "monthly_pnl": monthly_pnl,
                "monthly_drawdown_pct": monthly_dd,
                "open_positions": open_positions,
                "consecutive_losses": consecutive_losses,
                "bot_mode": trade.bot_mode,
            })
        except Exception:
            logger.exception("Failed to create drawdown snapshot after trade close")
