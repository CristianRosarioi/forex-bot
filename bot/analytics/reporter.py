"""Generación de reportes diarios/semanales en texto plano y envío por Telegram."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bot.analytics.metrics import PerformanceMetrics
from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from bot.db.repository import TradeRepository
    from bot.core.event_bus import EventBus

logger = get_logger(__name__)


def _fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


class PerformanceReporter:
    """Genera y envía reportes de rendimiento por Telegram."""

    def __init__(self, trade_repo: "TradeRepository", telegram_notifier, event_bus: "EventBus") -> None:
        self._trade_repo = trade_repo
        self._telegram = telegram_notifier
        self._bus = event_bus

    def generate_daily_report(self) -> str:
        """Genera el texto del reporte diario con trades cerrados hoy."""
        try:
            trades = self._trade_repo.get_today()
        except Exception:
            logger.exception("Error fetching today's trades for daily report")
            return "Error generando reporte"

        if not trades:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            return f"Reporte Diario - {now}\n\nSin trades cerrados en este periodo."

        return self._format_report(trades, title="Reporte Diario")

    def generate_weekly_report(self) -> str:
        """Genera el texto del reporte semanal con trades de esta semana."""
        try:
            trades = self._trade_repo.get_this_week()
            trades = [t for t in trades if t.closed_at is not None]
        except Exception:
            logger.exception("Error fetching weekly trades for weekly report")
            return "Error generando reporte"

        if not trades:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            return f"Reporte Semanal - {now}\n\nSin trades cerrados en este periodo."

        return self._format_report(trades, title="Reporte Semanal")

    def _format_report(self, trades: list, title: str) -> str:
        m = PerformanceMetrics.calculate(trades)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        reasons = m["by_close_reason"]

        lines = [
            f"📊 {title}",
            f"Fecha: {now}",
            "",
            f"Trades: {m['total_trades']} | Ganados: {m['winning_trades']} | Perdidos: {m['losing_trades']}",
            f"Winrate: {m['winrate_pct']}%",
            f"PnL: {m['total_pnl_pips']} pips / ${m['total_pnl_currency']}",
            f"Profit Factor: {_fmt_pf(m['profit_factor'])}",
            "",
            f"Mejor: {m['best_trade_pips']} pips",
            f"Peor: {m['worst_trade_pips']} pips",
            f"Max Drawdown: {m['max_drawdown_pips']} pips",
            f"Duracion media: {m['avg_trade_duration_minutes']} min",
            "",
            "Por razon de cierre:",
            f"SL: {reasons.get('SL', 0)} | TP: {reasons.get('TP', 0)} | Tiempo: {reasons.get('time', 0)} | Sesion: {reasons.get('session_end', 0)}",
        ]
        return "\n".join(lines)

    def send_daily_report(self) -> None:
        """Genera y envía el reporte diario por Telegram."""
        try:
            text = self.generate_daily_report()
            self._send(text)
            logger.info("Daily report sent")
        except Exception:
            logger.exception("Failed to send daily report")
            self._send("Error generando reporte")

    def send_weekly_report(self) -> None:
        """Genera y envía el reporte semanal por Telegram."""
        try:
            text = self.generate_weekly_report()
            self._send(text)
            logger.info("Weekly report sent")
        except Exception:
            logger.exception("Failed to send weekly report")
            self._send("Error generando reporte")

    def _send(self, text: str) -> None:
        if self._telegram is None:
            logger.info("Reporter: no Telegram notifier configured, skipping send")
            return
        try:
            self._telegram.send_plain(text)
        except Exception:
            logger.exception("Reporter: failed to send Telegram message")
