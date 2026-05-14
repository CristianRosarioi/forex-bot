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
    from bot.execution.order_manager import VirtualOrderManager
    from bot.execution.position_tracker import PositionTracker
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
        order_manager=None,
        position_tracker=None,
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
        self._order_manager = order_manager
        self._position_tracker = position_tracker
        self._stop_event = threading.Event()
        self._feed_thread: threading.Thread | None = None
        self._tracker_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the engine. Blocks until stop() is called."""
        logger.info("TradingEngine starting in %s mode", self._settings.mode.value)

        self._bus.subscribe(EventType.BAR_CLOSED, self._on_bar_closed)

        if self._eco_calendar:
            self._eco_calendar.start_refresh_loop()

        self._feed_thread = threading.Thread(
            target=self._feed.poll_loop,
            name="market-feed",
            daemon=True,
        )
        self._feed_thread.start()

        if self._position_tracker is not None:
            self._tracker_thread = threading.Thread(
                target=self._position_tracker.monitor_loop,
                name="position-tracker",
                daemon=True,
            )
            self._tracker_thread.start()
            logger.info("PositionTracker thread started")

        logger.info("Engine running in %s mode. Waiting for bars...", self._settings.mode.value)

        self._bus.publish("bot_started", {
            "mode": self._settings.mode.value,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        self._stop_event.wait()

    def stop(self) -> None:
        self._stop_event.set()
        if self._position_tracker is not None:
            self._position_tracker.stop()
        if self._eco_calendar:
            self._eco_calendar.stop_refresh_loop()
        logger.info("TradingEngine stopped")

    def _on_bar_closed(self, payload: dict) -> None:
        """Handler for BAR_CLOSED events. Called in EventBus subscriber thread."""
        symbol = payload.get("symbol", "")
        timeframe = payload.get("timeframe", "")

        if timeframe not in ("M15", "H1"):
            return

        try:
            self._process_bar(symbol, timeframe)
        except Exception:
            logger.exception("Error processing bar %s %s", symbol, timeframe)

    def _process_bar(self, symbol: str, timeframe: str) -> None:
        bars = self._buffer.get(symbol, timeframe)
        if bars is None or len(bars) < 20:
            return

        bars_h4 = self._buffer.get(symbol, "H4")
        bars_d1 = self._buffer.get(symbol, "D1")

        if bars_h4 is None or len(bars_h4) < 10:
            bars_h4 = bars
        if bars_d1 is None or len(bars_d1) < 5:
            bars_d1 = bars_h4

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

        account_state = self._get_account_state()
        if account_state is None:
            logger.warning("Cannot validate signal: account state unavailable")
            self._persist_signal(signal, rejection_reason="account_state_unavailable")
            return

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

        signal_id = self._persist_signal(
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

            if mode == "SHADOW":
                logger.info(
                    "SHADOW MODE: would have executed %s %s — signal persisted, no order sent",
                    signal.direction, signal.symbol,
                )
            elif mode in ("PAPER", "DEMO") and self._order_manager is not None:
                trade = self._order_manager.execute(
                    signal, result.calculated_lots, account_state, signal_id=signal_id
                )
                if trade is not None:
                    logger.info("[%s] Trade opened: %s %s", mode, signal.symbol, signal.direction)
                    if signal_id is not None:
                        self._mark_signal_acted_on(signal_id)

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

    def _persist_signal(self, signal: StrategySignal, rejection_reason: str | None = None) -> int | None:
        """Persists a signal to DB. Returns the signal id, or None on failure.

        Called for ALL signals regardless of validation outcome.
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
                db_signal = repo.create(signal_data)
                return db_signal.id
        except Exception:
            logger.exception("Failed to persist signal to DB")
            return None

    def _mark_signal_acted_on(self, signal_id: int) -> None:
        try:
            with get_session() as session:
                repo = SignalRepository(session)
                repo.mark_acted_on(signal_id)
        except Exception:
            logger.exception("Failed to mark signal %d as acted_on", signal_id)

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
                "initial_balance": float(info.balance),
                "day_start_balance": float(info.balance),
                "week_start_balance": float(info.balance),
                "month_start_balance": float(info.balance),
                "currency": info.currency,
                "leverage": info.leverage,
            }
        except Exception:
            logger.exception("Could not get account state from MT5")
            return None
