#!/usr/bin/env python3
"""Punto de entrada principal del bot."""
import signal
import sys
from pathlib import Path

# Ensure project root is in path
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
from bot.db.session import init_engine, get_session
from bot.db.repository import (
    TradeRepository, DrawdownRepository, SignalRepository
)

logger = get_logger(__name__)

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
    # Init DB
    init_engine()

    bus = EventBus()
    connector = MT5Connector(settings.mt5, bus)
    buffer = TimeframeBuffer()

    symbols_list = list(settings.symbols.keys()) if hasattr(settings, "symbols") else []
    timeframes = ["M1", "M5", "M15", "H1", "H4", "D1"]

    feed = MarketDataFeed(
        connector=connector,
        buffer=buffer,
        event_bus=bus,
        symbols=symbols_list,
        timeframes=timeframes,
    )

    market_calendar = MarketCalendar(connector=connector)
    eco_calendar = EconomicCalendar(event_bus=bus)

    # Build validator with fresh session (validator stores repos internally)
    with get_session() as session:
        trade_repo = TradeRepository(session)
        drawdown_repo = DrawdownRepository(session)
        signal_repo_inner = SignalRepository(session)

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
            signal_repo=signal_repo_inner,
            position_sizer=sizer,
            risk_limits=limits,
            event_bus=bus,
            market_calendar=market_calendar,
            economic_calendar=eco_calendar,
            bot_mode=settings.mode.value,
        )

    # Persistence and notifications
    EventPersistenceHandler(bus)
    telegram = None
    if settings.telegram.bot_token and settings.telegram.chat_id:
        telegram = TelegramNotifier(settings.telegram, bus)

    registry = load_default_strategies()
    level_manager = LevelManager()

    engine = TradingEngine(
        settings=settings,
        connector=connector,
        feed=feed,
        buffer=buffer,
        event_bus=bus,
        validator=validator,
        registry=registry,
        level_manager=level_manager,
        signal_repo=None,  # engine creates its own sessions per signal
        economic_calendar=eco_calendar,
        market_calendar=market_calendar,
        telegram=telegram,
    )
    return engine


def main():
    mode = settings.mode.value

    if settings.mode != BotMode.SHADOW:
        logger.warning(
            "ATENCION: bot en modo %s pero la ejecucion solo esta implementada hasta SHADOW",
            mode,
        )

    # Print banner
    registry = load_default_strategies()
    strategy_names = ", ".join(s.name for s in registry.get_all_enabled()) or "none"

    print(BANNER.format(
        mode=mode,
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
