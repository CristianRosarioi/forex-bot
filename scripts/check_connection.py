"""Verifica conectividad con MT5 y PostgreSQL. Reporta estado detallado por consola."""

from __future__ import annotations

import sys
from pathlib import Path

# Asegurar que el proyecto raíz esté en el path al ejecutar directamente
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import MetaTrader5 as mt5
from sqlalchemy import text

_OK = "[OK]  "
_FAIL = "[FAIL]"
_WARN = "[WARN]"

_all_ok = True


def _ok(msg: str) -> None:
    print(f"{_OK} {msg}")


def _fail(msg: str) -> None:
    global _all_ok
    _all_ok = False
    print(f"{_FAIL} {msg}")


def _warn(msg: str) -> None:
    print(f"{_WARN} {msg}")


def _section(title: str) -> None:
    print(f"\n{'-' * 50}")
    print(f"  {title}")
    print(f"{'-' * 50}")


def check_settings() -> bool:
    """Carga settings y verifica que no falte ninguna variable obligatoria."""
    _section("1. Settings / .env")
    try:
        from config.settings import settings
        _ok(f"BOT_MODE = {settings.mode.value}")
        _ok(f"MT5 server = {settings.mt5.server}, login = {settings.mt5.login}")
        _ok(f"DB = {settings.db.host}:{settings.db.port}/{settings.db.name}")
        return True
    except Exception as exc:
        _fail(f"Settings load failed: {exc}")
        return False


def check_mt5(settings) -> bool:
    """Conecta a MT5 y reporta info de la cuenta."""
    _section("2. MetaTrader 5 Connection")
    initialized = mt5.initialize(
        path=settings.mt5.path,
        login=settings.mt5.login,
        password=settings.mt5.password,
        server=settings.mt5.server,
    )
    if not initialized:
        err = mt5.last_error()
        _fail(f"MT5 initialize failed: [{err[0]}] {err[1]}")
        return False

    info = mt5.terminal_info()
    if info is None or not info.connected:
        _fail("MT5 terminal not connected")
        mt5.shutdown()
        return False

    _ok(f"MT5 conectado - build {info.build}")

    acct = mt5.account_info()
    if acct:
        _ok(f"Account: login={acct.login}, balance={acct.balance:.2f} {acct.currency}, "
            f"leverage=1:{acct.leverage}, server={acct.server}")
    else:
        _warn("Could not retrieve account_info()")

    return True


def check_symbols(settings) -> None:
    """Verifica cada símbolo habilitado en symbols.yaml."""
    _section("3. Symbols (enabled in symbols.yaml)")
    yaml_path = Path(__file__).parent.parent / "config" / "symbols.yaml"
    with open(yaml_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    for sym, data in cfg.get("symbols", {}).items():
        if not data.get("enabled", False):
            _warn(f"{sym} disabled -skipped")
            continue

        sym_info = mt5.symbol_info(sym)
        if sym_info is None:
            _fail(f"{sym} -not found in MT5")
            continue

        # Habilitar el símbolo en Market Watch si no estaba visible
        if not sym_info.visible:
            mt5.symbol_select(sym, True)
            sym_info = mt5.symbol_info(sym)

        spread_pips = sym_info.spread * sym_info.point * 10  # convertir a pips
        _ok(f"{sym} -spread={spread_pips:.1f} pips")

        # Leer las últimas 10 velas M15
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 10)
        if rates is None or len(rates) == 0:
            _warn(f"  {sym} M15: no bars returned")
        else:
            import pandas as pd
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            print(f"       Last M15 close: {df.iloc[-1]['close']:.5f}  "
                  f"({df.iloc[-1]['time'].strftime('%Y-%m-%d %H:%M')} UTC)")


def check_postgres(settings) -> None:
    """Intenta conectar a PostgreSQL y ejecuta un SELECT 1."""
    _section("4. PostgreSQL Connection")
    try:
        from sqlalchemy import create_engine
        engine = create_engine(settings.db.url, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
        _ok(f"PostgreSQL OK -{version}")
    except Exception as exc:
        _fail(f"PostgreSQL connection failed: {exc}")


def check_schema(settings) -> None:
    """Verifica que el schema de la DB tenga todas las tablas esperadas y reporta filas."""
    _section("5. Database Schema")
    try:
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(settings.db.url, connect_args={"connect_timeout": 5})
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        expected_tables = [
            "signals",
            "orders",
            "trades",
            "drawdown_snapshots",
            "bot_events",
            "mt5_connection_log",
            "economic_events",
        ]

        Session = sessionmaker(bind=engine)
        session = Session()

        try:
            for table in expected_tables:
                if table not in existing_tables:
                    _fail(f"Table missing: {table}")
                    continue
                result = session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                row_count = result.scalar()
                _ok(f"Table '{table}': {row_count} rows")

            # Mostrar el último BotEvent registrado
            if "bot_events" in existing_tables:
                last_event = session.execute(
                    text(
                        "SELECT event_type, severity, occurred_at, message "
                        "FROM bot_events ORDER BY occurred_at DESC LIMIT 1"
                    )
                ).fetchone()
                if last_event:
                    print(
                        f"\n  Last BotEvent:\n"
                        f"    event_type : {last_event[0]}\n"
                        f"    severity   : {last_event[1]}\n"
                        f"    occurred_at: {last_event[2]}\n"
                        f"    message    : {last_event[3]}"
                    )
                else:
                    _warn("No BotEvents registered yet")
        finally:
            session.close()
            engine.dispose()

    except Exception as exc:
        _fail(f"Schema check failed: {exc}")


def check_telegram(settings) -> None:
    """Envía un mensaje de test al chat de Telegram configurado."""
    _section("6. Telegram")
    try:
        import asyncio
        import telegram
        bot = telegram.Bot(token=settings.telegram.bot_token)

        async def _send():
            await bot.send_message(
                chat_id=settings.telegram.chat_id,
                text="🤖 check\\_connection\\.py \\- test",  # MarkdownV2 escapado
                parse_mode="MarkdownV2",
            )

        asyncio.run(_send())
        _ok("Telegram OK - mensaje de test enviado")
    except Exception as exc:
        _warn(f"Telegram no disponible: {exc}")


def main() -> int:
    from config.settings import settings

    print("\n" + "=" * 50)
    print("  FOREX BOT - Connection Health Check")
    print("=" * 50)

    if not check_settings():
        print("\n[ABORT] Fix .env before continuing.\n")
        return 1

    mt5_ok = check_mt5(settings)

    if mt5_ok:
        check_symbols(settings)
        mt5.shutdown()
    else:
        _warn("Skipping symbol checks (MT5 not connected)")

    check_postgres(settings)

    check_schema(settings)

    check_telegram(settings)

    print(f"\n{'=' * 50}")
    if _all_ok:
        print("  RESULT: ALL CHECKS PASSED")
    else:
        print("  RESULT: SOME CHECKS FAILED - review output above")
    print("=" * 50 + "\n")

    return 0 if _all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
