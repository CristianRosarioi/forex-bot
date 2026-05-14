"""Tests for bot/analytics/reporter.py"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from bot.analytics.reporter import PerformanceReporter
from bot.core.event_bus import EventBus


def _make_trade(pnl_pips, pnl_currency=None, symbol="EURUSD", close_reason="TP",
                duration_minutes=45):
    t = MagicMock()
    t.pnl_pips = pnl_pips
    t.pnl_currency = pnl_currency if pnl_currency is not None else pnl_pips * 0.10
    t.symbol = symbol
    t.close_reason = close_reason
    now = datetime.now(timezone.utc)
    t.opened_at = now - timedelta(minutes=duration_minutes)
    t.closed_at = now
    return t


def _make_reporter(trades_today=None, trades_week=None):
    bus = EventBus()
    trade_repo = MagicMock()
    trade_repo.get_today.return_value = trades_today or []
    trade_repo.get_this_week.return_value = trades_week or []
    telegram = MagicMock()
    reporter = PerformanceReporter(
        trade_repo=trade_repo,
        telegram_notifier=telegram,
        event_bus=bus,
    )
    return reporter, telegram


class TestGenerateDailyReport:
    def test_empty_trades_returns_no_trades_message(self):
        reporter, _ = _make_reporter(trades_today=[])
        text = reporter.generate_daily_report()
        assert "Sin trades cerrados en este periodo" in text

    def test_report_with_trades_contains_key_fields(self):
        trades = [_make_trade(50), _make_trade(-20), _make_trade(30)]
        reporter, _ = _make_reporter(trades_today=trades)
        text = reporter.generate_daily_report()

        assert "Reporte Diario" in text
        assert "Trades: 3" in text
        assert "Ganados: 2" in text
        assert "Perdidos: 1" in text
        assert "Winrate:" in text
        assert "PnL:" in text
        assert "Profit Factor:" in text
        assert "Mejor:" in text
        assert "Peor:" in text

    def test_report_contains_close_reason_breakdown(self):
        trades = [
            _make_trade(50, close_reason="TP"),
            _make_trade(-20, close_reason="SL"),
        ]
        reporter, _ = _make_reporter(trades_today=trades)
        text = reporter.generate_daily_report()
        assert "SL:" in text
        assert "TP:" in text

    def test_repo_exception_returns_error_string(self):
        bus = EventBus()
        trade_repo = MagicMock()
        trade_repo.get_today.side_effect = RuntimeError("DB error")
        telegram = MagicMock()
        reporter = PerformanceReporter(
            trade_repo=trade_repo, telegram_notifier=telegram, event_bus=bus
        )
        text = reporter.generate_daily_report()
        assert "Error" in text


class TestGenerateWeeklyReport:
    def test_empty_trades_returns_no_trades_message(self):
        reporter, _ = _make_reporter(trades_week=[])
        text = reporter.generate_weekly_report()
        assert "Sin trades cerrados en este periodo" in text

    def test_weekly_title_present(self):
        trades = [_make_trade(40), _make_trade(-15)]
        # Mark as closed
        for t in trades:
            t.closed_at = datetime.now(timezone.utc)
        reporter, _ = _make_reporter(trades_week=trades)
        text = reporter.generate_weekly_report()
        assert "Reporte Semanal" in text


class TestSendReports:
    def test_send_daily_report_calls_telegram(self):
        trades = [_make_trade(30), _make_trade(-10)]
        reporter, telegram = _make_reporter(trades_today=trades)
        reporter.send_daily_report()
        telegram.send_plain.assert_called_once()
        sent_text = telegram.send_plain.call_args[0][0]
        assert "Reporte Diario" in sent_text

    def test_send_weekly_report_calls_telegram(self):
        trades = [_make_trade(20)]
        for t in trades:
            t.closed_at = datetime.now(timezone.utc)
        reporter, telegram = _make_reporter(trades_week=trades)
        reporter.send_weekly_report()
        telegram.send_plain.assert_called_once()

    def test_send_daily_no_crash_with_no_telegram(self):
        bus = EventBus()
        trade_repo = MagicMock()
        trade_repo.get_today.return_value = []
        reporter = PerformanceReporter(
            trade_repo=trade_repo,
            telegram_notifier=None,
            event_bus=bus,
        )
        # Should not raise even with no Telegram configured
        reporter.send_daily_report()

    def test_send_error_report_on_exception(self):
        bus = EventBus()
        trade_repo = MagicMock()
        trade_repo.get_today.side_effect = RuntimeError("DB gone")
        telegram = MagicMock()
        reporter = PerformanceReporter(
            trade_repo=trade_repo, telegram_notifier=telegram, event_bus=bus
        )
        reporter.send_daily_report()
        telegram.send_plain.assert_called_once()
        sent = telegram.send_plain.call_args[0][0]
        assert "Error" in sent
