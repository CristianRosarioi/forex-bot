#!/usr/bin/env python
"""Script de inicialización de la base de datos. Crea tablas y aplica migraciones iniciales."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone
from sqlalchemy import inspect, text

from bot.db.models import Base
import bot.db.session as _db_session
from bot.db.session import init_engine, get_session
from bot.db.repository import BotEventRepository
from config.settings import settings

_OK = "[OK] "
_FAIL = "[FAIL]"


def main() -> int:
    print("\n" + "=" * 50)
    print("  FOREX BOT - Database Initialization")
    print("=" * 50)

    # 1. Init engine
    try:
        init_engine()
        print(f"{_OK} Connected to PostgreSQL {settings.db.host}:{settings.db.port}/{settings.db.name}")
    except Exception as exc:
        print(f"{_FAIL} Cannot connect to PostgreSQL: {exc}")
        return 1

    # 2. Create tables
    try:
        Base.metadata.create_all(_db_session.engine)
        inspector = inspect(_db_session.engine)
        tables = inspector.get_table_names()
        expected = ["signals", "orders", "trades", "drawdown_snapshots",
                    "bot_events", "mt5_connection_log", "economic_events", "risk_pauses"]
        for table in expected:
            if table in tables:
                print(f"{_OK} Table ready: {table}")
            else:
                print(f"{_FAIL} Table missing: {table}")
                return 1
        print(f"{_OK} All 8 tables ready")
    except Exception as exc:
        print(f"{_FAIL} Schema creation failed: {exc}")
        return 1

    # 3. Insert initial BotEvent
    try:
        with get_session() as session:
            repo = BotEventRepository(session)
            repo.log(
                event_type="BOT_STARTED",
                severity="INFO",
                module="scripts.init_db",
                message="Database schema initialized",
                context={"tables": list(expected), "db": settings.db.name},
            )
        print(f"{_OK} Initial BotEvent logged")
    except Exception as exc:
        print(f"{_FAIL} Could not log BotEvent: {exc}")
        return 1

    print(f"{_OK} Database initialization complete\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
