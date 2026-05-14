"""Cálculo de métricas de rendimiento: win rate, profit factor, Sharpe ratio, max drawdown."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.db.models import Trade


def _base_metrics(trades: list) -> dict:
    """Calcula métricas sobre una lista de trades cerrados."""
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "winrate_pct": 0.0,
            "total_pnl_pips": 0.0,
            "total_pnl_currency": 0.0,
            "avg_win_pips": 0.0,
            "avg_loss_pips": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pips": 0.0,
            "avg_trade_duration_minutes": 0.0,
            "best_trade_pips": 0.0,
            "worst_trade_pips": 0.0,
        }

    pips_list = [float(t.pnl_pips or 0) for t in trades]
    currency_list = [float(t.pnl_currency or 0) for t in trades]

    winners = [p for p in pips_list if p > 0]
    losers = [p for p in pips_list if p < 0]
    breakevens = [p for p in pips_list if p == 0]

    total = len(trades)
    winrate = len(winners) / total * 100 if total > 0 else 0.0

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    avg_win = sum(winners) / len(winners) if winners else 0.0
    avg_loss = sum(losers) / len(losers) if losers else 0.0

    # Max drawdown: magnitude of the worst consecutive losing streak in pips
    max_dd = 0.0
    current_dd = 0.0
    for p in pips_list:
        if p < 0:
            current_dd += p
            if current_dd < -max_dd:
                max_dd = abs(current_dd)
        else:
            current_dd = 0.0

    # Avg duration
    durations = []
    for t in trades:
        if t.opened_at and t.closed_at:
            opened = t.opened_at
            closed = t.closed_at
            import datetime
            if opened.tzinfo is None:
                import datetime as dt
                opened = opened.replace(tzinfo=dt.timezone.utc)
            if closed.tzinfo is None:
                import datetime as dt
                closed = closed.replace(tzinfo=dt.timezone.utc)
            durations.append((closed - opened).total_seconds() / 60)
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    return {
        "total_trades": total,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "breakeven_trades": len(breakevens),
        "winrate_pct": round(winrate, 2),
        "total_pnl_pips": round(sum(pips_list), 2),
        "total_pnl_currency": round(sum(currency_list), 2),
        "avg_win_pips": round(avg_win, 2),
        "avg_loss_pips": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else float("inf"),
        "max_drawdown_pips": round(max_dd, 2),
        "avg_trade_duration_minutes": round(avg_duration, 1),
        "best_trade_pips": round(max(pips_list), 2),
        "worst_trade_pips": round(min(pips_list), 2),
    }


class PerformanceMetrics:
    """Calcula métricas de rendimiento desde una lista de trades cerrados."""

    @staticmethod
    def calculate(trades: list) -> dict:
        """Retorna dict con métricas globales + by_symbol + by_close_reason.

        Args:
            trades: Lista de Trade cerrados (closed_at no nulo).

        Returns:
            Dict con todas las métricas de rendimiento.
        """
        result = _base_metrics(trades)

        # By symbol
        symbols: dict[str, list] = {}
        for t in trades:
            symbols.setdefault(t.symbol, []).append(t)
        result["by_symbol"] = {sym: _base_metrics(sym_trades) for sym, sym_trades in symbols.items()}

        # By close reason
        reasons: dict[str, int] = {}
        for t in trades:
            reason = str(t.close_reason or "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
        result["by_close_reason"] = reasons

        return result
