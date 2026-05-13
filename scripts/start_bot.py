#!/usr/bin/env python3
"""Punto de entrada principal del bot."""
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings, BotMode
from bot.core.event_bus import EventBus
from bot.core.connector import MT5Connector
from bot.core.feed import MarketDataFeed
from bot.core.timeframe_buffer import TimeframeBuffer
from bot.core.scheduler import MarketCalendar
from bot.infra.logger import get_logger
from bot.infra.event_persistence import EventPersistenceHandler
from bot.infra.telegram import TelegramNotifier
from bot.infra.economic_calendar import EconomicCalendar
from bot.risk.validator import OrderValidator
from bot.risk.limits import RiskLimits
from bot.risk.sizing import PositionSizer
from bot.analysis.levels import LevelManager
from bot.strategy.registry import load_default_strategies
from bot.core.engine import TradingEngine
import bot.db.session as _db_session
from bot.db.session import init_engine
from bot.db.repository import (
    TradeRepository, DrawdownRepository, SignalRepository
)

logger = get_logger(__name__)

_MODE_LABELS = {
    "SHADOW": "SHADOW MODE - señales solo",
    "PAPER":  "PAPER MODE - ejecución virtual",
    "DEMO":   "DEMO MODE - cuenta demo MT5",
    "LIVE":   "LIVE MODE - cuenta real",
}

BANNER = """\
+----------------------------------------------+
|      FOREX BOT - {mode:<28}|
+----------------------------------------------+
|  Mode:       {mode:<32}|
|  Symbols:    {symbols:<32}|
|  Strategies: {strategies:<32}|
|  Telegram:   {telegram:<32}|
|  Database:   {database:<32}|
+----------------------------------------------+"""


def build_engine() -> TradingEngine:
    init_engine()

    bus = EventBus()
    connector = MT5Connector(settings.mt5, bus)
    buffer = TimeframeBuffer()

    feed = MarketDataFeed(
        connector=connector,
        event_bus=bus,
        buffer=buffer,
    )

    market_calendar = MarketCalendar(connector=connector)
    eco_calendar = EconomicCalendar(event_bus=bus)

    _repo_session = _db_session.SessionLocal()
    trade_repo = TradeRepository(_repo_session)
    drawdown_repo = DrawdownRepository(_repo_session)

    sizer = PositionSizer(settings=settings.risk, trade_repo=trade_repo)
    limits = RiskLimits(
        settings=settings.risk,
        trade_repo=trade_repo,
        drawdown_repo=drawdown_repo,
        event_bus=bus,
    )
    validator = OrderValidator(
        settings=settings.risk,
        trade_repo=trade_repo,
        drawdown_repo=drawdown_repo,
        signal_repo=None,
        position_sizer=sizer,
        risk_limits=limits,
        event_bus=bus,
        market_calendar=market_calendar,
        economic_calendar=eco_calendar,
        bot_mode=settings.mode.value,
    )

    EventPersistenceHandler(bus)
    telegram = None
    if settings.telegram.bot_token and settings.telegram.chat_id:
        telegram = TelegramNotifier(bus, settings.telegram)
        telegram.start()

    registry = load_default_strategies()
    level_manager = LevelManager()

    order_manager = None
    position_tracker = None

    if settings.mode == BotMode.PAPER:
        from bot.execution.order_manager import VirtualOrderManager
        from bot.execution.position_tracker import PositionTracker

        _exec_session = _db_session.SessionLocal()
        exec_trade_repo = TradeRepository(_exec_session)
        exec_signal_repo = SignalRepository(_exec_session)
        exec_drawdown_repo = DrawdownRepository(_exec_session)

        order_manager = VirtualOrderManager(
            connector=connector,
            event_bus=bus,
            trade_repo=exec_trade_repo,
            signal_repo=exec_signal_repo,
        )
        position_tracker = PositionTracker(
            connector=connector,
            event_bus=bus,
            trade_repo=exec_trade_repo,
            drawdown_repo=exec_drawdown_repo,
            settings=settings.risk,
        )
    elif settings.mode not in (BotMode.SHADOW,):
        raise ValueError(
            f"Mode {settings.mode.value} not implemented yet. "
            "Use SHADOW or PAPER."
        )

    engine = TradingEngine(
        settings=settings,
        connector=connector,
        feed=feed,
        buffer=buffer,
        event_bus=bus,
        validator=validator,
        registry=registry,
        level_manager=level_manager,
        signal_repo=None,
        economic_calendar=eco_calendar,
        market_calendar=market_calendar,
        telegram=telegram,
        order_manager=order_manager,
        position_tracker=position_tracker,
    )
    return engine


def main():
    mode = settings.mode.value
    mode_label = _MODE_LABELS.get(mode, mode)

    registry = load_default_strategies()
    strategy_names = ", ".join(s.name for s in registry.get_all_enabled()) or "none"

    print(BANNER.format(
        mode=mode_label[:32],
        symbols="EURUSD, GBPUSD, ...",
        strategies=strategy_names[:32],
        telegram="Y" if (settings.telegram.bot_token and settings.telegram.chat_id) else "N",
        database="Y",
    ))

    engine = build_engine()

    def _shutdown(signum, frame):
        print("\n[INFO] Shutting down...")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    print("[INFO] Connecting to MT5...")
    print("[INFO] Starting market data feed...")
    print("[INFO] Starting strategy engine...")
    print("[INFO] Bot is running. Press Ctrl+C to stop.")

    engine.start()


if __name__ == "__main__":
    main()
