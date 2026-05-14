"""Cliente de Telegram para notificaciones: señales, trades ejecutados, alertas de riesgo, reportes."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Callable

import telegram

from bot.core.event_bus import EventBus, EventType
from bot.infra.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# MarkdownV2 escaping
# ---------------------------------------------------------------------------

_MD_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def _escape_md(text: str) -> str:
    """Escapa caracteres especiales para Telegram MarkdownV2."""
    return _MD_SPECIAL.sub(r'\\\1', str(text))


# ---------------------------------------------------------------------------
# Emojis helpers
# ---------------------------------------------------------------------------

def _dir_emoji(direction: str) -> str:
    direction = str(direction).upper()
    return "🟢" if direction == "BUY" else "🔴"


def _pnl_emoji(pnl) -> str:
    try:
        return "🟢" if float(pnl) >= 0 else "🔴"
    except (TypeError, ValueError):
        return "⚪"


# ---------------------------------------------------------------------------
# Message formatters per event type
# ---------------------------------------------------------------------------

def _fmt_bot_started(payload: dict) -> str:
    mode = _escape_md(payload.get("mode", "unknown"))
    ts = _escape_md(payload.get("timestamp", ""))
    return f"🟢 *Bot iniciado*\nModo: `{mode}`\nHora: `{ts}`"


def _fmt_bot_stopped(payload: dict) -> str:
    reason = _escape_md(payload.get("reason", "unknown"))
    return f"🔴 *Bot detenido*\nRazón: `{reason}`"


def _fmt_mt5_disconnected(payload: dict) -> str:
    reason = _escape_md(payload.get("reason", "unknown"))
    return f"⚠️ *MT5 desconectado*\nRazón: `{reason}`\nReintentando reconexión\.\.\."


def _fmt_mt5_reconnected(payload: dict) -> str:
    return "✅ *MT5 reconectado*"


def _fmt_signal_generated(payload: dict) -> str:
    strategy = _escape_md(payload.get("strategy_name", ""))
    symbol = _escape_md(payload.get("symbol", ""))
    timeframe = _escape_md(payload.get("timeframe", ""))
    direction = _escape_md(payload.get("direction", ""))
    entry = _escape_md(payload.get("entry_price", ""))
    sl = _escape_md(payload.get("sl_price", ""))
    tp = _escape_md(payload.get("tp_price", ""))
    reason = _escape_md(payload.get("reason", ""))
    dir_em = _dir_emoji(payload.get("direction", ""))
    return (
        f"📊 *Señal generada* `{strategy}`\n"
        f"Símbolo: `{symbol}` \\({timeframe}\\)\n"
        f"Dirección: {dir_em} `{direction}`\n"
        f"Entry: `{entry}`\n"
        f"SL: `{sl}`\n"
        f"TP: `{tp}`\n"
        f"Razón: {reason}"
    )


def _fmt_order_placed(payload: dict) -> str:
    symbol = _escape_md(payload.get("symbol", ""))
    direction = _escape_md(payload.get("direction", ""))
    volume = _escape_md(payload.get("volume", ""))
    price = _escape_md(payload.get("price", ""))
    sl = _escape_md(payload.get("sl_price", ""))
    tp = _escape_md(payload.get("tp_price", ""))
    dir_em = _dir_emoji(payload.get("direction", ""))
    return (
        f"📤 *Orden enviada*\n"
        f"`{symbol}` {dir_em} `{volume}` lots @ `{price}`\n"
        f"SL: `{sl}` \\| TP: `{tp}`"
    )


def _fmt_order_filled(payload: dict) -> str:
    symbol = _escape_md(payload.get("symbol", ""))
    ticket = _escape_md(payload.get("mt5_ticket", ""))
    return f"✅ *Orden ejecutada*\n`{symbol}` ticket `\\#{ticket}`"


def _fmt_order_rejected(payload: dict) -> str:
    symbol = _escape_md(payload.get("symbol", ""))
    reason = _escape_md(payload.get("reason", "unknown"))
    return f"❌ *Orden rechazada*\n`{symbol}` razón: {reason}"


def _fmt_position_closed(payload: dict) -> str:
    symbol = _escape_md(payload.get("symbol", ""))
    ticket = _escape_md(payload.get("mt5_ticket", ""))
    close_reason = _escape_md(payload.get("close_reason", ""))
    pnl_pips = _escape_md(payload.get("pnl_pips", ""))
    pnl_currency = _escape_md(payload.get("pnl_currency", ""))
    pnl_em = _pnl_emoji(payload.get("pnl_currency", 0))
    return (
        f"🏁 *Posición cerrada*\n"
        f"`{symbol}` ticket `\\#{ticket}`\n"
        f"Razón: `{close_reason}`\n"
        f"PnL: {pnl_em} `{pnl_pips}` pips / `\\${pnl_currency}`"
    )


def _fmt_drawdown_limit_hit(payload: dict) -> str:
    period = _escape_md(payload.get("period", ""))
    pct = _escape_md(payload.get("drawdown_pct", ""))
    return (
        f"🚨 *Límite de drawdown alcanzado*\n"
        f"Tipo: `{period}`\n"
        f"Drawdown: `{pct}%`\n"
        f"Bot suspendido para este período\\."
    )


def _fmt_kill_switch(payload: dict) -> str:
    balance = _escape_md(payload.get("balance", ""))
    return (
        f"🆘 *KILL SWITCH ACTIVADO*\n"
        f"Balance: `\\${balance}`\n"
        f"BOT DETENIDO\\. Requiere reactivación manual\\."
    )


def _fmt_paused_for_news(payload: dict) -> str:
    event_title = _escape_md(payload.get("event_title", ""))
    return f"📰 *Trading pausado por noticia*\nEvento: `{event_title}`"


def _fmt_resumed(payload: dict) -> str:
    return "▶️ *Trading reanudado*"


# Map event_type -> formatter function
_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "bot_started":              _fmt_bot_started,
    "bot_stopped":              _fmt_bot_stopped,
    EventType.MT5_DISCONNECTED: _fmt_mt5_disconnected,
    EventType.MT5_RECONNECTED:  _fmt_mt5_reconnected,
    EventType.SIGNAL_GENERATED: _fmt_signal_generated,
    EventType.ORDER_PLACED:     _fmt_order_placed,
    EventType.ORDER_FILLED:     _fmt_order_filled,
    EventType.ORDER_REJECTED:   _fmt_order_rejected,
    EventType.POSITION_CLOSED:  _fmt_position_closed,
    EventType.DRAWDOWN_LIMIT_HIT:    _fmt_drawdown_limit_hit,
    EventType.KILL_SWITCH_TRIGGERED: _fmt_kill_switch,
    "paused_for_news":          _fmt_paused_for_news,
    "resumed":                  _fmt_resumed,
}

# Events to subscribe to (only those that exist in EventType or we want to handle)
_SUBSCRIBE_EVENTS = [
    "bot_started",
    "bot_stopped",
    EventType.MT5_DISCONNECTED,
    EventType.MT5_RECONNECTED,
    EventType.KILL_SWITCH_TRIGGERED,
    EventType.DRAWDOWN_LIMIT_HIT,
    EventType.ORDER_PLACED,
    EventType.ORDER_FILLED,
    EventType.ORDER_REJECTED,
    EventType.POSITION_CLOSED,
    EventType.SIGNAL_GENERATED,
    "paused_for_news",
    "resumed",
]


class TelegramNotifier:
    """Suscriptor del EventBus que envía notificaciones formateadas a Telegram.

    Usa un event loop dedicado en un hilo daemon para enviar mensajes async
    de forma segura desde código síncrono.

    Uso::

        notifier = TelegramNotifier(event_bus, settings.telegram)
        notifier.start()
        # ... bot corriendo ...
        notifier.stop()
    """

    def __init__(self, event_bus: EventBus, settings) -> None:
        self._bot_token = settings.bot_token
        self._chat_id = settings.chat_id
        self._bus = event_bus
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="telegram-loop",
        )
        self._thread.start()
        self._bot: telegram.Bot | None = None
        self._handlers: dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # Internal send helpers
    # ------------------------------------------------------------------

    def _send_sync(self, text: str) -> None:
        """Envía mensaje de forma síncrona usando el loop dedicado."""
        future = asyncio.run_coroutine_threadsafe(
            self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="MarkdownV2",
            ),
            self._loop,
        )
        future.result(timeout=30)

    def send_message(self, text: str) -> None:
        """Envía un mensaje con reintentos (3 intentos, backoff 2s/5s/10s)."""
        delays = [2, 5, 10]
        for attempt, delay in enumerate(delays, 1):
            try:
                self._send_sync(text)
                return
            except Exception as exc:
                logger.warning("Telegram send attempt %d failed: %s", attempt, exc)
                if attempt < len(delays):
                    time.sleep(delay)
        logger.error("Telegram: all send attempts failed")

    def send_plain(self, text: str) -> None:
        """Envía texto plano sin parse_mode (para reportes con pips/porcentajes)."""
        delays = [2, 5, 10]
        for attempt, delay in enumerate(delays, 1):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._bot.send_message(chat_id=self._chat_id, text=text),
                    self._loop,
                )
                future.result(timeout=30)
                return
            except Exception as exc:
                logger.warning("Telegram plain send attempt %d failed: %s", attempt, exc)
                if attempt < len(delays):
                    time.sleep(delay)
        logger.error("Telegram: all plain send attempts failed")

    # ------------------------------------------------------------------
    # Handler factory
    # ------------------------------------------------------------------

    def _make_handler(self, event_type: str) -> Callable:
        """Crea un closure que captura el event_type y formatea el mensaje."""

        def handler(payload: dict) -> None:
            formatter = _FORMATTERS.get(event_type)
            if formatter is None:
                logger.debug("TelegramNotifier: no formatter for event %s", event_type)
                return
            try:
                message = formatter(payload)
            except Exception:
                logger.exception(
                    "TelegramNotifier: error formatting event %s", event_type
                )
                return
            try:
                self.send_message(message)
            except Exception:
                logger.exception(
                    "TelegramNotifier: error sending message for event %s", event_type
                )

        return handler

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicializa el bot, suscribe al bus y envía mensaje de inicio."""
        self._bot = telegram.Bot(token=self._bot_token)

        self._handlers = {}
        for event_type in _SUBSCRIBE_EVENTS:
            h = self._make_handler(event_type)
            self._handlers[event_type] = h
            self._bus.subscribe(event_type, h)

        logger.info(
            "TelegramNotifier started — subscribed to %d event types",
            len(self._handlers),
        )

        try:
            self.send_message("🤖 Telegram notifier conectado")
        except Exception:
            logger.exception("TelegramNotifier: failed to send startup message")

    def stop(self) -> None:
        """Desuscribe del bus, envía mensaje de cierre y detiene el loop."""
        for event_type, h in self._handlers.items():
            self._bus.unsubscribe(event_type, h)
        self._handlers = {}

        try:
            self.send_message("🔴 Bot desconectado")
        except Exception:
            logger.exception("TelegramNotifier: failed to send shutdown message")

        self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("TelegramNotifier stopped")
