"""Integración: ciclo de vida de pausas de RiskLimits contra la DB de TEST.

Reemplaza al antiguo `test_is_paused_with_real_db` (vivía en
tests/unit/test_limits.py), que llamaba `init_engine()` y por tanto operaba
sobre la DB de PRODUCCIÓN (`forex_bot`), mutándola al correr pytest.

Aquí parcheamos `bot.risk.limits.get_session` para que ceda la sesión de la DB
de test (`db_session`, conectada a `forex_bot_test`), siguiendo el mismo patrón
que el resto de la suite de integración (cf. test_event_persistence.py). El
fixture `db_session` trunca `risk_pauses` al finalizar, dejando la DB limpia.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from bot.core.event_bus import EventBus
from bot.risk.limits import RiskLimits
from config.settings import RiskSettings


def _make_limits() -> RiskLimits:
    settings = RiskSettings(
        risk_pct_per_trade=0.5,
        max_daily_drawdown_pct=3.0,
        max_weekly_drawdown_pct=6.0,
        max_monthly_drawdown_pct=10.0,
        max_open_positions=3,
        max_daily_trades=10,
        max_consecutive_losses=3,
        kill_switch_balance_pct=80.0,
    )
    # is_paused/pause_until/resume no usan los repos; basta con mocks.
    return RiskLimits(
        settings=settings,
        trade_repo=MagicMock(),
        drawdown_repo=MagicMock(),
        event_bus=EventBus(),
        bot_mode="DEMO",
    )


def test_is_paused_lifecycle_uses_test_db(db_session) -> None:
    """is_paused / pause_until / resume operan SOLO contra la DB de test."""
    @contextmanager
    def fake_get_session():
        # Cede la sesión de test; el flush hace visibles los cambios dentro de
        # la misma sesión entre llamadas sucesivas a get_session().
        yield db_session

    limits = _make_limits()
    with patch("bot.risk.limits.get_session", fake_get_session):
        assert limits.is_paused() is False

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        limits.pause_until(future, "test pause", "WARNING")
        assert limits.is_paused() is True

        limits.resume()
        assert limits.is_paused() is False


def test_expired_pause_is_deactivated_against_test_db(db_session) -> None:
    """Una pausa ya vencida no bloquea: is_paused() la desactiva (camino de
    limpieza compartido) y devuelve False."""
    @contextmanager
    def fake_get_session():
        yield db_session

    limits = _make_limits()
    with patch("bot.risk.limits.get_session", fake_get_session):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        limits.pause_until(past, "Max consecutive losses reached: 3/3", "WARNING")
        # Vencida → no debe bloquear, y debe quedar desactivada.
        assert limits.is_paused() is False
        assert limits.get_active_pause() is None
