"""Tests for bot/analytics/metrics.py"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from bot.analytics.metrics import PerformanceMetrics, _base_metrics


def _make_trade(pnl_pips, pnl_currency=None, symbol="EURUSD", close_reason="SL",
                duration_minutes=60):
    t = MagicMock()
    t.pnl_pips = pnl_pips
    t.pnl_currency = pnl_currency if pnl_currency is not None else pnl_pips * 0.10
    t.symbol = symbol
    t.close_reason = close_reason
    now = datetime.now(timezone.utc)
    t.opened_at = now - timedelta(minutes=duration_minutes)
    t.closed_at = now
    return t


class TestBaseMetrics:
    def test_empty_list_returns_zeros(self):
        result = _base_metrics([])
        assert result["total_trades"] == 0
        assert result["winrate_pct"] == 0.0
        assert result["profit_factor"] == 0.0
        assert result["max_drawdown_pips"] == 0.0

    def test_winrate_3_wins_2_losses(self):
        trades = [
            _make_trade(20),
            _make_trade(30),
            _make_trade(15),
            _make_trade(-10),
            _make_trade(-20),
        ]
        result = _base_metrics(trades)
        assert result["total_trades"] == 5
        assert result["winning_trades"] == 3
        assert result["losing_trades"] == 2
        assert result["winrate_pct"] == pytest.approx(60.0)

    def test_profit_factor(self):
        trades = [
            _make_trade(100),  # +100
            _make_trade(50),   # +50
            _make_trade(-75),  # -75
        ]
        result = _base_metrics(trades)
        # gross_profit = 150, gross_loss = 75 → PF = 2.0
        assert result["profit_factor"] == pytest.approx(2.0)

    def test_profit_factor_no_losses_is_inf(self):
        trades = [_make_trade(50), _make_trade(30)]
        result = _base_metrics(trades)
        assert result["profit_factor"] == float("inf")

    def test_profit_factor_no_winners_is_zero(self):
        trades = [_make_trade(-20), _make_trade(-10)]
        result = _base_metrics(trades)
        assert result["profit_factor"] == 0.0

    def test_total_pnl_pips(self):
        trades = [_make_trade(50), _make_trade(-20), _make_trade(10)]
        result = _base_metrics(trades)
        assert result["total_pnl_pips"] == pytest.approx(40.0)

    def test_max_drawdown_single_streak(self):
        # Losing streak: -10, -20 = -30 total
        trades = [_make_trade(-10), _make_trade(-20), _make_trade(50), _make_trade(-5)]
        result = _base_metrics(trades)
        assert result["max_drawdown_pips"] == pytest.approx(30.0)

    def test_max_drawdown_multiple_streaks(self):
        # Streak 1: -10, -20 = -30; win; Streak 2: -15, -30, -5 = -50
        trades = [
            _make_trade(-10),
            _make_trade(-20),
            _make_trade(5),
            _make_trade(-15),
            _make_trade(-30),
            _make_trade(-5),
        ]
        result = _base_metrics(trades)
        assert result["max_drawdown_pips"] == pytest.approx(50.0)

    def test_max_drawdown_no_losses(self):
        trades = [_make_trade(20), _make_trade(10)]
        result = _base_metrics(trades)
        assert result["max_drawdown_pips"] == 0.0

    def test_best_and_worst_trade(self):
        trades = [_make_trade(80), _make_trade(-40), _make_trade(20)]
        result = _base_metrics(trades)
        assert result["best_trade_pips"] == pytest.approx(80.0)
        assert result["worst_trade_pips"] == pytest.approx(-40.0)

    def test_avg_trade_duration(self):
        trades = [
            _make_trade(10, duration_minutes=30),
            _make_trade(20, duration_minutes=90),
        ]
        result = _base_metrics(trades)
        assert result["avg_trade_duration_minutes"] == pytest.approx(60.0)

    def test_breakeven_trades_counted(self):
        trades = [_make_trade(10), _make_trade(0), _make_trade(-5)]
        result = _base_metrics(trades)
        assert result["breakeven_trades"] == 1
        assert result["winning_trades"] == 1
        assert result["losing_trades"] == 1


class TestPerformanceMetrics:
    def test_calculate_includes_by_symbol(self):
        trades = [
            _make_trade(20, symbol="EURUSD"),
            _make_trade(-10, symbol="EURUSD"),
            _make_trade(30, symbol="GBPUSD"),
        ]
        result = PerformanceMetrics.calculate(trades)
        assert "by_symbol" in result
        assert "EURUSD" in result["by_symbol"]
        assert "GBPUSD" in result["by_symbol"]
        assert result["by_symbol"]["EURUSD"]["total_trades"] == 2
        assert result["by_symbol"]["GBPUSD"]["total_trades"] == 1

    def test_calculate_includes_by_close_reason(self):
        trades = [
            _make_trade(20, close_reason="TP"),
            _make_trade(-10, close_reason="SL"),
            _make_trade(-5, close_reason="SL"),
            _make_trade(5, close_reason="time"),
        ]
        result = PerformanceMetrics.calculate(trades)
        assert result["by_close_reason"]["SL"] == 2
        assert result["by_close_reason"]["TP"] == 1
        assert result["by_close_reason"]["time"] == 1

    def test_calculate_empty_list(self):
        result = PerformanceMetrics.calculate([])
        assert result["total_trades"] == 0
        assert result["by_symbol"] == {}
        assert result["by_close_reason"] == {}

    def test_avg_win_avg_loss(self):
        trades = [_make_trade(40), _make_trade(60), _make_trade(-30), _make_trade(-10)]
        result = PerformanceMetrics.calculate(trades)
        assert result["avg_win_pips"] == pytest.approx(50.0)
        assert result["avg_loss_pips"] == pytest.approx(-20.0)
