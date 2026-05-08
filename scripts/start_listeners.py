#!/usr/bin/env python
"""Script de prueba de los handlers de eventos (persistencia + Telegram)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.core.event_bus import EventBus, EventType
from bot.db.session import init_engine, get_session
from bot.db.repository import BotEventRepository
from bot.infra.event_persistence import EventPersistenceHandler
from bot.infra.telegram import TelegramNotifier
from config.settings import settings

_OK = "[OK] "
_FAIL = "[FAIL]"


def main() -> int:
    print("\n" + "=" * 55)
    print("  FOREX BOT - Event Listeners Test")
    print("=" * 55)

    # 1. Init DB
    init_engine()

    # 2. EventBus
    bus = EventBus()
    print(f"{_OK} EventBus creado")

    # 3. EventPersistenceHandler
    persistence = EventPersistenceHandler(bus)
    persistence.start()
    print(f"{_OK} EventPersistenceHandler iniciado")

    # 4. TelegramNotifier
    notifier = TelegramNotifier(bus, settings.telegram)
    notifier.start()
    print(f"{_OK} TelegramNotifier iniciado")

    # 5. Contar eventos en DB antes
    with get_session() as session:
        repo = BotEventRepository(session)
        before_count = repo.get_recent(limit=1000)
    before = len(before_count)

    # 6. Publicar eventos de prueba
    test_events = [
        (EventType.SIGNAL_GENERATED, {
            "symbol": "EURUSD", "timeframe": "M15", "direction": "BUY",
            "strategy_name": "retest", "entry_price": 1.08500,
            "sl_price": 1.08300, "tp_price": 1.08900,
            "reason": "Retest of broken resistance",
        }),
        (EventType.ORDER_PLACED, {
            "symbol": "EURUSD", "direction": "BUY", "volume": 0.01,
            "price": 1.08500, "sl_price": 1.08300, "tp_price": 1.08900,
        }),
        (EventType.ORDER_FILLED, {"symbol": "EURUSD", "mt5_ticket": 123456}),
        (EventType.MT5_DISCONNECTED, {"reason": "test_disconnection"}),
        (EventType.MT5_RECONNECTED, {}),
        (EventType.POSITION_CLOSED, {
            "symbol": "EURUSD", "mt5_ticket": 123456, "direction": "BUY",
            "close_reason": "TP", "pnl_pips": 40.0, "pnl_currency": 4.00,
        }),
        (EventType.DRAWDOWN_LIMIT_HIT, {
            "period": "daily", "drawdown_pct": 3.05,
        }),
    ]

    published = 0
    for event_type, payload in test_events:
        bus.publish(event_type, payload)
        print(f"{_OK} Evento {event_type} publicado")
        published += 1
        time.sleep(2)  # espera entre mensajes Telegram

    # 7. Verificar persistencia en DB
    time.sleep(1)
    with get_session() as session:
        repo = BotEventRepository(session)
        after_count = repo.get_recent(limit=1000)
    after = len(after_count)
    persisted = after - before

    # BAR_CLOSED no se persiste, así que esperamos <= published eventos
    if persisted > 0:
        print(f"{_OK} Verificacion final: {persisted} eventos persistidos en DB")
    else:
        print(f"{_FAIL} No se persistio ningun evento")

    # 8. Stop
    notifier.stop()
    persistence.stop()
    print(f"{_OK} Handlers detenidos limpiamente")

    print("\n" + "=" * 55 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
