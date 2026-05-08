"""Tests unitarios para TelegramNotifier (sin llamadas reales a la API)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from bot.core.event_bus import EventBus, EventType
from bot.infra.telegram import _escape_md, TelegramNotifier, _SUBSCRIBE_EVENTS


# ---------------------------------------------------------------------------
# Tests para _escape_md
# ---------------------------------------------------------------------------

def test_escape_md_special_chars():
    """_escape_md debe escapar todos los caracteres especiales de MarkdownV2."""
    special_chars = r'_*[]()~`>#+\-=|{}.!\\'
    result = _escape_md(special_chars)
    # Cada carácter especial debe ir precedido de '\'
    for ch in r'_*[]()~`>#+\-=|{}.!':
        assert f"\\{ch}" in result or ch not in special_chars


def test_escape_md_underscore():
    """Los guiones bajos deben escaparse correctamente."""
    result = _escape_md("hello_world")
    assert result == r"hello\_world"


def test_escape_md_asterisk():
    """Los asteriscos deben escaparse."""
    result = _escape_md("1.5*2")
    assert result == r"1\.5\*2"


def test_escape_md_dot():
    """Los puntos deben escaparse."""
    result = _escape_md("1.085")
    assert result == r"1\.085"


def test_escape_md_dash():
    """Los guiones deben escaparse."""
    result = _escape_md("test-disconnection")
    assert result == r"test\-disconnection"


def test_escape_md_plain_text():
    """Texto sin caracteres especiales no debe cambiar."""
    result = _escape_md("EURUSD BUY 1085")
    assert result == "EURUSD BUY 1085"


def test_escape_md_empty_string():
    """Cadena vacía debe devolver cadena vacía."""
    result = _escape_md("")
    assert result == ""


# ---------------------------------------------------------------------------
# Tests sobre suscripción de eventos
# ---------------------------------------------------------------------------

def test_bar_closed_not_notified():
    """BAR_CLOSED no debe estar en la lista de eventos suscritos."""
    assert EventType.BAR_CLOSED not in _SUBSCRIBE_EVENTS


def test_critical_events_subscribed():
    """KILL_SWITCH_TRIGGERED y DRAWDOWN_LIMIT_HIT deben estar suscritos."""
    assert EventType.KILL_SWITCH_TRIGGERED in _SUBSCRIBE_EVENTS
    assert EventType.DRAWDOWN_LIMIT_HIT in _SUBSCRIBE_EVENTS


# ---------------------------------------------------------------------------
# Tests con mocks del bot de Telegram
# ---------------------------------------------------------------------------

def _make_settings(token="fake_token", chat_id="12345"):
    settings = MagicMock()
    settings.bot_token = token
    settings.chat_id = chat_id
    return settings


def test_signal_format_contains_symbol():
    """El mensaje de SIGNAL_GENERATED debe contener el símbolo."""
    bus = EventBus()
    settings = _make_settings()

    sent_messages = []

    with patch("bot.infra.telegram.telegram.Bot") as MockBot:
        mock_bot_instance = MagicMock()
        MockBot.return_value = mock_bot_instance

        notifier = TelegramNotifier(bus, settings)

        # Parchear send_message para capturar llamadas
        notifier.send_message = lambda text: sent_messages.append(text)

        notifier.start()
        # Limpiar el mensaje de inicio
        sent_messages.clear()

        bus.publish(EventType.SIGNAL_GENERATED, {
            "symbol": "GBPUSD",
            "timeframe": "H1",
            "direction": "SELL",
            "strategy_name": "breakout",
            "entry_price": 1.27500,
            "sl_price": 1.27700,
            "tp_price": 1.27000,
            "reason": "Break of support",
        })

        notifier.stop()

    assert len(sent_messages) >= 1
    # Verificar que algún mensaje contiene el símbolo (posiblemente escapado)
    combined = " ".join(sent_messages)
    assert "GBPUSD" in combined


def test_send_message_retries_on_failure():
    """send_message debe reintentar hasta 3 veces si _send_sync falla."""
    bus = EventBus()
    settings = _make_settings()

    attempt_count = []

    with patch("bot.infra.telegram.telegram.Bot") as MockBot:
        mock_bot_instance = MagicMock()
        MockBot.return_value = mock_bot_instance

        notifier = TelegramNotifier(bus, settings)
        notifier._bot = mock_bot_instance

        original_send_sync = notifier._send_sync

        def failing_send_sync(text):
            attempt_count.append(1)
            raise ConnectionError("Network error")

        notifier._send_sync = failing_send_sync

        # Parchear time.sleep para no esperar en tests
        with patch("bot.infra.telegram.time.sleep"):
            notifier.send_message("test message")

    # Debe haber intentado 3 veces (len(delays) = 3)
    assert len(attempt_count) == 3


def test_no_message_sent_for_unformatted_event():
    """Eventos sin formatter no deben causar envíos."""
    bus = EventBus()
    settings = _make_settings()

    sent_messages = []

    with patch("bot.infra.telegram.telegram.Bot") as MockBot:
        MockBot.return_value = MagicMock()

        notifier = TelegramNotifier(bus, settings)
        notifier.send_message = lambda text: sent_messages.append(text)

        # Suscribir manualmente un evento sin formatter
        notifier.start()
        sent_messages.clear()

        # BAR_CLOSED no está suscrito, así que no se procesa
        bus.publish(EventType.BAR_CLOSED, {"symbol": "EURUSD"})

        notifier.stop()
        # El único mensaje enviado debe ser el de stop
        bar_messages = [m for m in sent_messages if "EURUSD" in m]
        assert len(bar_messages) == 0
