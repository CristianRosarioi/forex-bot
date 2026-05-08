"""Motor principal del bot. Orquesta el ciclo de vida: arranque, bucle de tick, shutdown."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bot.core.event_bus import EventBus, EventType
from bot.analysis.swing import detect_swings
from bot.analysis.structure import analyze_structure
from bot.analysis.levels import LevelManager
from bot.analysis.trend import analyze_trend
from bot.analysis.volume import analyze_volume
from bot.strategy.base import StrategyContext, Signal as StrategySignal
from bot.risk.validator import OrderRequest, OrderValidator
from bot.infra.logger import get_logger
from bot.db.session import get_session
from bot.db.repository import SignalRepository

if TYPE_CHECKING:
    from bot.core.connector import MT5Connector
    from bot.core.feed import MarketDataFeed
    from bot.core.timeframe_buffer import TimeframeBuffer
    from bot.strategy.registry import StrategyRegistry
    from bot.db.repository import SignalRepository
    from config.settings import Settings

logger = get_logger(__name__)


class TradingEngine:
    def __init__(
        self,
        settings,
        connector,
        feed,
        buffer,
        event_bus: EventBus,
        validator: OrderValidator,
        registry,
        level_manager: LevelManager,
        signal_repo,
        economic_calendar=None,
        market_calendar=None,
        telegram=None,
    ):
        self._settings = settings
        self._connector = connector
        self._feed = feed
        self._buffer = buffer
        self._bus = event_bus
        self._validator = validator
        self._registry = registry
        self._level_manager = level_manager
        self._signal_repo = signal_repo
        self._eco_calendar = economic_calendar
        self._market_calendar = market_calendar
        self._telegram = telegram
        self._stop_event = threading.Event()
        self._feed_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the engine. Blocks until stop() is called."""
        logger.info("TradingEngine starting in %s mode", self._settings.mode.value)

        # Subscribe to BAR_CLOSED
        self._bus.subscribe(EventType.BAR_CLOSED, self._on_bar_closed)

        # Start economic calendar refresh
        if self._eco_calendar:
            self._eco_calendar.start_refresh_loop()

        # Start feed in background thread
        self._feed_thread = threading.Thread(
            target=self._feed.poll_loop,
            name="market-feed",
            daemon=True,
        )
        self._feed_thread.start()

        logger.info("Engine running in %s mode. Waiting for bars...", self._settings.mode.value)

        # Notify subscribers (e.g. Telegram) that the bot has started
        self._bus.publish("bot_started", {
            "mode": self._settings.mode.value,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        self._stop_event.wait()  # block until stop()

    def stop(self) -> None:
        self._stop_event.set()
        if self._eco_calendar:
            self._eco_calendar.stop_refresh_loop()
        logger.info("TradingEngine stopped")

    def _on_bar_closed(self, payload: dict) -> None:
        """Handler for BAR_CLOSED events. Called in EventBus subscriber thread."""
        symbol = payload.get("symbol", "")
        timeframe = payload.get("timeframe", "")

        # Only process M15 and H1 entry timeframes
        if timeframe not in ("M15", "H1"):
            return

        try:
            self._process_bar(symbol, timeframe)
        except Exception:
            logger.exception("Error processing bar %s %s", symbol, timeframe)

    def _process_bar(self, symbol: str, timeframe: str) -> None:
        # Get bars from buffer
        bars = self._buffer.get(symbol, timeframe)
        if bars is None or len(bars) < 20:
            return

        bars_h4 = self._buffer.get(symbol, "H4")
        bars_d1 = self._buffer.get(symbol, "D1")

        if bars_h4 is None or len(bars_h4) < 10:
            bars_h4 = bars  # fallback
        if bars_d1 is None or len(bars_d1) < 5:
            bars_d1 = bars_h4  # fallback

        # Build analysis context
        swings = detect_swings(bars, lookback=3)
        structure = analyze_structure(bars, swings)
        self._level_manager.detect_levels(bars, swings, symbol, timeframe)
        self._level_manager.update_levels(bars, symbol, timeframe)
        levels = self._level_manager.get_active_levels(symbol, timeframe)
        trend = analyze_trend(bars_h4, bars_d1)
        volume = analyze_volume(bars)

        last_bar = bars.iloc[-1]
        current_price = float(last_bar["close"])
        timestamp = int(last_bar["time"]) if "time" in last_bar else 0

        ctx = StrategyContext(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            bars_h4=bars_h4,
            bars_d1=bars_d1,
            swings=swings,
            structure=structure,
            levels=levels,
            trend=trend,
            volume=volume,
            current_price=current_price,
            timestamp=timestamp,
        )

        # Run each strategy
        for strategy in self._registry.get_all_enabled():
            try:
                self._run_strategy(strategy, ctx)
            except Exception:
                logger.exception("Strategy %s raised exception", strategy.name)

    def _run_strategy(self, strategy, ctx: StrategyContext) -> None:
        signal = strategy.analyze(ctx)
        if signal is None:
            return

        logger.info(
            "Strategy %s generated signal: %s %s @ %s (sl=%s)",
            strategy.name, signal.direction, signal.symbol,
            signal.entry_price, signal.sl_price,
        )

        # Get account state from MT5
        account_state = self._get_account_state()
        if account_state is None:
            logger.warning("Cannot validate signal: account state unavailable")
            # F-04: persist before discarding — audit trail must be complete for ALL signals
            self._persist_signal(signal, rejection_reason="account_state_unavailable")
            return

        # Build OrderRequest and validate
        request = OrderRequest(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price,
            signal_id=None,
            strategy_name=signal.strategy_name,
            bot_mode=self._settings.mode.value,
        )

        try:
            result = self._validator.validate(request, account_state)
        except ValueError as e:
            logger.error("Validation error (bad account state): %s", e)
            self._persist_signal(signal, rejection_reason=f"validation_error: {e}")
            return

        # F-04: persist ALL signals (approved AND rejected) before any publish/log action
        self._persist_signal(
            signal,
            rejection_reason=None if result.approved else result.rejection_reason,
        )

        if result.approved:
            mode = self._settings.mode.value
            logger.info(
                "[%s] Signal APPROVED: %s %s entry=%s sl=%s lots=%s",
                mode, signal.symbol, signal.direction,
                signal.entry_price, signal.sl_price, result.calculated_lots,
            )
            # SHADOW: log only, never execute
            if mode == "SHADOW":
                logger.info(
                    "SHADOW MODE: would have executed %s %s — signal persisted, no order sent",
                    signal.direction, signal.symbol,
                )

            # Publish to bus (Telegram notifier picks this up)
            self._bus.publish(EventType.SIGNAL_GENERATED, {
                "symbol": signal.symbol,
                "timeframe": signal.timeframe,
                "direction": signal.direction,
                "strategy_name": signal.strategy_name,
                "entry_price": signal.entry_price,
                "sl_price": signal.sl_price,
                "tp_price": signal.tp_price,
                "reason": f"[{mode}] {signal.reason}",
                "confidence": signal.confidence,
            })
        else:
            logger.info(
                "Signal REJECTED [%s]: %s",
                result.rejection_severity, result.rejection_reason,
            )

    def _persist_signal(self, signal: StrategySignal, rejection_reason: str | None = None) -> None:
        """Persists a signal to DB with acted_on=False.

        Called for ALL signals regardless of validation outcome — every signal
        must have an audit record.
        """
        signal_data = {
            "generated_at": datetime.now(timezone.utc),
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "strategy_name": signal.strategy_name,
            "signal_type": signal.signal_type,
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "sl_price": signal.sl_price,
            "tp_price": signal.tp_price,
            "reason": signal.reason,
            "bot_mode": self._settings.mode.value,
            "acted_on": False,
            "rejection_reason": rejection_reason,
        }
        try:
            with get_session() as session:
                repo = SignalRepository(session)
                repo.create(signal_data)
        except Exception:
            logger.exception("Failed to persist signal to DB")

    def _get_account_state(self) -> dict | None:
        """Gets account state from MT5. Returns None if unavailable."""
        try:
            import MetaTrader5 as mt5
            info = mt5.account_info()
            if info is None:
                return None
            return {
                "balance": float(info.balance),
                "equity": float(info.equity),
                "initial_balance": float(info.balance),   # approximate
                "day_start_balance": float(info.balance),
                "week_start_balance": float(info.balance),
                "month_start_balance": float(info.balance),
                "currency": info.currency,
                "leverage": info.leverage,
            }
        except Exception:
            logger.exception("Could not get account state from MT5")
            return None
