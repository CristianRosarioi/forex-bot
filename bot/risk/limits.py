"""Estado y evaluación de los límites de riesgo: drawdown diario/semanal/mensual, trades/día, pérdidas consecutivas."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from bot.core.event_bus import EventBus, EventType
from bot.db.session import get_session
from bot.db.repository import RiskPauseRepository
from bot.infra.logger import get_logger, log_event

if TYPE_CHECKING:
    from config.settings import RiskSettings
    from bot.db.repository import TradeRepository, DrawdownRepository

logger = get_logger(__name__)

# Prefijo del `reason` de las pausas por pérdidas consecutivas. Se usa tanto para
# construir el mensaje como para localizar la última pausa expirada de este tipo
# y derivar el corte temporal (`since`) que rompe el deadlock.
CONSECUTIVE_LOSSES_REASON_PREFIX = "Max consecutive losses reached"

# Escalada (M-1): si el breaker de pérdidas consecutivas se dispara este número
# de veces (o más) dentro de la ventana, la pausa deja de ser de 4h y se endurece
# a un bloqueo hasta la próxima medianoche UTC. Evita que el breaker se vuelva un
# throttle infinito de "6 pérdidas / 4h" en rachas 100% perdedoras.
CONSECUTIVE_LOSSES_ESCALATION_COUNT = 3
CONSECUTIVE_LOSSES_ESCALATION_WINDOW_HOURS = 24
CONSECUTIVE_LOSSES_PAUSE_HOURS = 4


@dataclass
class LimitCheckResult:
    passed: bool
    check_name: str
    reason: str | None = None
    severity: str = "INFO"  # INFO / WARNING / ERROR / CRITICAL
    resume_at: datetime | None = None


class RiskLimits:
    """Evalúa las 5 capas de límites de riesgo antes de permitir una operación."""

    def __init__(
        self,
        settings: "RiskSettings",
        trade_repo: "TradeRepository",
        drawdown_repo: "DrawdownRepository",
        event_bus: EventBus,
        bot_mode: str | None = None,
    ) -> None:
        self._s = settings
        self._trades = trade_repo
        self._drawdowns = drawdown_repo
        self._bus = event_bus
        # bot_mode permite que la racha de pérdidas se cuente sólo dentro del modo
        # operativo activo (no mezclar DEMO con PAPER/LIVE).
        self._bot_mode = bot_mode

    # ──────────────────────────────────────────────
    # Checks individuales
    # ──────────────────────────────────────────────

    def check_open_positions_limit(self) -> LimitCheckResult:
        open_trades = self._trades.get_open()
        count = len(open_trades)
        if count >= self._s.max_open_positions:
            return LimitCheckResult(
                passed=False,
                check_name="open_positions_limit",
                reason=f"Max open positions reached: {count}/{self._s.max_open_positions}",
                severity="WARNING",
            )
        return LimitCheckResult(passed=True, check_name="open_positions_limit")

    def check_daily_trades_limit(self) -> LimitCheckResult:
        today_trades = self._trades.get_today()
        count = len(today_trades)
        if count >= self._s.max_daily_trades:
            return LimitCheckResult(
                passed=False,
                check_name="daily_trades_limit",
                reason=f"Max daily trades reached: {count}/{self._s.max_daily_trades}",
                severity="WARNING",
                resume_at=_next_midnight_utc(),
            )
        return LimitCheckResult(passed=True, check_name="daily_trades_limit")

    def check_consecutive_losses(self) -> LimitCheckResult:
        # Corte temporal: si ya se sirvió (y expiró) una pausa por consecutive
        # losses, los trades anteriores a ella NO cuentan. Así, tras la pausa, la
        # racha efectiva arranca de cero y el bot puede volver a operar — esto es
        # lo que rompe el deadlock de re-pausas infinitas.
        since = self._last_consecutive_pause_cutoff()
        losses = self._trades.get_consecutive_losses(bot_mode=self._bot_mode, since=since)
        if losses >= self._s.max_consecutive_losses:
            resume_at, escalated = self._consecutive_losses_resume_at()
            reason = f"{CONSECUTIVE_LOSSES_REASON_PREFIX}: {losses}/{self._s.max_consecutive_losses}"
            if escalated:
                reason += " — escalated to daily block (recurrent breaker)"
            return LimitCheckResult(
                passed=False,
                check_name="consecutive_losses",
                reason=reason,
                severity="WARNING",
                resume_at=resume_at,
            )
        return LimitCheckResult(passed=True, check_name="consecutive_losses")

    def _consecutive_losses_resume_at(self) -> tuple[datetime, bool]:
        """Calcula el resume_at de la pausa por pérdidas consecutivas.

        Escalada (M-1): si en la ventana ya hubo (incluyendo la que se está por
        crear) >= CONSECUTIVE_LOSSES_ESCALATION_COUNT pausas de este tipo, se
        endurece a un bloqueo hasta la próxima medianoche UTC. Si no, 4h.

        Returns:
            (resume_at, escalated)
        """
        now = datetime.now(timezone.utc)
        base_resume = now + timedelta(hours=CONSECUTIVE_LOSSES_PAUSE_HOURS)
        prior = self._count_recent_consecutive_pauses(CONSECUTIVE_LOSSES_ESCALATION_WINDOW_HOURS)
        if prior + 1 >= CONSECUTIVE_LOSSES_ESCALATION_COUNT:
            # Clamp inferior: la escalada NUNCA debe bloquear menos que la pausa
            # base. Cerca de medianoche UTC, _next_midnight_utc() podría estar a
            # minutos; en ese caso usamos la base de 4h (max).
            resume_at = max(_next_midnight_utc(), base_resume)
            logger.warning(
                "Consecutive-losses breaker recurrente (%d pausas en %dh) — "
                "escalando a bloqueo diario hasta %s",
                prior + 1, CONSECUTIVE_LOSSES_ESCALATION_WINDOW_HOURS, resume_at,
            )
            return resume_at, True
        return base_resume, False

    def _count_recent_consecutive_pauses(self, window_hours: int) -> int:
        """Número de pausas por consecutive losses creadas en las últimas
        `window_hours`. Fail-safe: ante error de DB devolvemos 0 (no escala; la
        orden ya está siendo rechazada por el límite, así que no abre riesgo).
        """
        try:
            with get_session() as session:
                repo = RiskPauseRepository(session)
                since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
                return repo.count_pauses_by_prefix_since(CONSECUTIVE_LOSSES_REASON_PREFIX, since)
        except Exception:
            logger.exception("Could not count recent consecutive-losses pauses")
            return 0

    def _last_consecutive_pause_cutoff(self) -> datetime | None:
        """Devuelve el `paused_at` de la última pausa por consecutive losses que
        ya expiró, o None si no hay ninguna.

        Fail-SAFE (conservador) ante error de DB: si no se puede consultar el
        corte, devolvemos None → get_consecutive_losses cuenta TODA la racha →
        tiende a BLOQUEAR. Nunca debe cambiarse a un comportamiento que oculte
        pérdidas ante un fallo de DB.
        """
        try:
            with get_session() as session:
                repo = RiskPauseRepository(session)
                pause = repo.get_last_expired_pause_by_prefix(CONSECUTIVE_LOSSES_REASON_PREFIX)
                return pause.paused_at if pause is not None else None
        except Exception:
            logger.exception("Could not fetch last consecutive-losses pause cutoff")
            return None

    def check_daily_drawdown(self, current_equity: float, day_start_balance: float) -> LimitCheckResult:
        if day_start_balance <= 0:
            return LimitCheckResult(passed=True, check_name="daily_drawdown")
        loss_pct = (day_start_balance - current_equity) / day_start_balance * 100
        if loss_pct >= self._s.max_daily_drawdown_pct:
            resume_at = _next_midnight_utc()
            self._emit_drawdown_event("daily", loss_pct, resume_at)
            return LimitCheckResult(
                passed=False,
                check_name="daily_drawdown",
                reason=f"Daily drawdown limit hit: {loss_pct:.2f}% >= {self._s.max_daily_drawdown_pct}%",
                severity="ERROR",
                resume_at=resume_at,
            )
        return LimitCheckResult(passed=True, check_name="daily_drawdown")

    def check_weekly_drawdown(self, current_equity: float, week_start_balance: float) -> LimitCheckResult:
        if week_start_balance <= 0:
            return LimitCheckResult(passed=True, check_name="weekly_drawdown")
        loss_pct = (week_start_balance - current_equity) / week_start_balance * 100
        if loss_pct >= self._s.max_weekly_drawdown_pct:
            resume_at = _next_monday_utc()
            self._emit_drawdown_event("weekly", loss_pct, resume_at)
            return LimitCheckResult(
                passed=False,
                check_name="weekly_drawdown",
                reason=f"Weekly drawdown limit hit: {loss_pct:.2f}% >= {self._s.max_weekly_drawdown_pct}%",
                severity="ERROR",
                resume_at=resume_at,
            )
        return LimitCheckResult(passed=True, check_name="weekly_drawdown")

    def check_monthly_drawdown(self, current_equity: float, month_start_balance: float) -> LimitCheckResult:
        if month_start_balance <= 0:
            return LimitCheckResult(passed=True, check_name="monthly_drawdown")
        loss_pct = (month_start_balance - current_equity) / month_start_balance * 100
        if loss_pct >= self._s.max_monthly_drawdown_pct:
            resume_at = _next_month_start_utc()
            self._emit_drawdown_event("monthly", loss_pct, resume_at)
            return LimitCheckResult(
                passed=False,
                check_name="monthly_drawdown",
                reason=f"Monthly drawdown limit hit: {loss_pct:.2f}% >= {self._s.max_monthly_drawdown_pct}%",
                severity="ERROR",
                resume_at=resume_at,
            )
        return LimitCheckResult(passed=True, check_name="monthly_drawdown")

    def check_kill_switch(self, current_equity: float, initial_balance: float) -> LimitCheckResult:
        """Verifica si el equity ha caído por debajo del umbral de kill switch.

        Args:
            current_equity: Equity actual de la cuenta (incluye pérdidas no realizadas).
            initial_balance: Balance inicial de referencia.
        """
        if initial_balance <= 0:
            return LimitCheckResult(passed=True, check_name="kill_switch")
        balance_pct = current_equity / initial_balance * 100
        threshold = self._s.kill_switch_balance_pct
        if balance_pct < threshold:
            msg = f"Kill switch triggered: equity {balance_pct:.1f}% of initial (threshold {threshold}%)"
            logger.critical(msg)
            log_event(
                event_type=EventType.KILL_SWITCH_TRIGGERED,
                severity="CRITICAL",
                module=__name__,
                message=msg,
                context={"current_equity": current_equity, "initial_balance": initial_balance,
                         "balance_pct": balance_pct, "threshold": threshold},
            )
            self._bus.publish(EventType.KILL_SWITCH_TRIGGERED, {
                "balance": current_equity,
                "initial_balance": initial_balance,
                "balance_pct": balance_pct,
            })
            # C-01: persist a CRITICAL permanent pause so is_paused() and _check_kill_switch_active() block future orders
            self.pause_until(resume_at=None, reason=msg, severity="CRITICAL")
            return LimitCheckResult(
                passed=False,
                check_name="kill_switch",
                reason=msg,
                severity="CRITICAL",
            )
        return LimitCheckResult(passed=True, check_name="kill_switch")

    def check_correlation_limit(self, symbol: str, direction: str) -> LimitCheckResult:
        """Máximo 2 posiciones correlacionadas en la misma dirección."""
        from bot.risk.sizing import _get_correlated_group
        group = _get_correlated_group(symbol)
        if group is None:
            return LimitCheckResult(passed=True, check_name="correlation_limit")
        open_trades = self._trades.get_open()
        count = sum(
            1 for t in open_trades
            if str(t.symbol).upper() in group
            and str(t.direction).upper() == direction.upper()
            and str(t.symbol).upper() != symbol.upper()
        )
        if count >= 2:
            return LimitCheckResult(
                passed=False,
                check_name="correlation_limit",
                reason=f"Too many correlated positions for {symbol} {direction}: {count} already open",
                severity="WARNING",
            )
        return LimitCheckResult(passed=True, check_name="correlation_limit")

    def check_all(
        self,
        symbol: str,
        direction: str,
        current_equity: float,
        current_balance: float,
        initial_balance: float,
        day_start_balance: float,
        week_start_balance: float,
        month_start_balance: float,
    ) -> list[LimitCheckResult]:
        """Ejecuta todos los checks y devuelve los que fallaron."""
        results = [
            self.check_open_positions_limit(),
            self.check_daily_trades_limit(),
            self.check_consecutive_losses(),
            self.check_daily_drawdown(current_equity, day_start_balance),
            self.check_weekly_drawdown(current_equity, week_start_balance),
            self.check_monthly_drawdown(current_equity, month_start_balance),
            # M-03: pass current_equity (not current_balance) to kill switch
            self.check_kill_switch(current_equity, initial_balance),
            self.check_correlation_limit(symbol, direction),
        ]
        return [r for r in results if not r.passed]

    # ──────────────────────────────────────────────
    # Pausas
    # ──────────────────────────────────────────────

    def is_paused(self) -> bool:
        """Verifica si hay una pausa de riesgo activa.

        C-01b: resume_at=None significa pausa permanente (siempre True).
        C-02: En caso de error de DB, retorna True (fail-closed).
        M-06: get_session() hace commit automático al salir, por lo que
              deactivate_expired() queda persistido.

        Usa el MISMO camino de limpieza que get_active_pause(): primero
        deactivate_expired() (desactiva pausas vencidas, preserva permanentes),
        luego get_active(). Tras la limpieza sólo quedan activas las pausas
        permanentes o las aún vigentes.
        """
        try:
            with get_session() as session:
                repo = RiskPauseRepository(session)
                repo.deactivate_expired()
                return repo.get_active() is not None
        except Exception:
            # C-02: fail-closed — if we can't check, assume paused
            logger.exception("Could not check risk pause state — assuming PAUSED (fail-safe)")
            return True

    def get_active_pause(self):
        """Retorna la pausa activa actual, o None si no hay ninguna.

        C-03: NO captura excepciones — las deja propagar hacia el caller
        (validate() en OrderValidator) que las maneja de forma fail-closed
        rechazando la orden con severidad CRITICAL.

        Comparte el camino de limpieza con is_paused(): deactivate_expired()
        antes de get_active(), de modo que nunca devuelve una pausa vencida.
        """
        with get_session() as session:
            repo = RiskPauseRepository(session)
            repo.deactivate_expired()
            return repo.get_active()

    def pause_until(self, resume_at: datetime | None, reason: str, severity: str) -> None:
        try:
            with get_session() as session:
                repo = RiskPauseRepository(session)
                repo.create_pause(
                    paused_at=datetime.now(timezone.utc),
                    resume_at=resume_at,
                    reason=reason,
                    severity=severity,
                )
            logger.warning("Trading paused until %s: %s", resume_at, reason)
        except Exception:
            logger.exception("Could not create risk pause")

    def resume(self) -> None:
        try:
            with get_session() as session:
                repo = RiskPauseRepository(session)
                repo.deactivate_all()
            logger.info("Risk pause lifted — trading resumed")
        except Exception:
            logger.exception("Could not deactivate risk pauses")

    # ──────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────

    def _emit_drawdown_event(self, period: str, pct: float, resume_at: datetime) -> None:
        msg = f"Drawdown limit hit: {period} {pct:.2f}%"
        log_event(
            event_type=EventType.DRAWDOWN_LIMIT_HIT,
            severity="ERROR",
            module=__name__,
            message=msg,
            context={"period": period, "drawdown_pct": pct, "resume_at": str(resume_at)},
        )
        self._bus.publish(EventType.DRAWDOWN_LIMIT_HIT, {
            "period": period, "drawdown_pct": pct, "resume_at": str(resume_at),
        })


# ──────────────────────────────────────────────
# Utilidades de tiempo UTC
# ──────────────────────────────────────────────

def _next_midnight_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

def _next_monday_utc() -> datetime:
    """H-02: from Monday, next Monday is exactly 7 days ahead (not 0)."""
    now = datetime.now(timezone.utc)
    days_ahead = (7 - now.weekday()) % 7 or 7
    return (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)

def _next_month_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return now.replace(year=now.year+1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month+1, day=1, hour=0, minute=0, second=0, microsecond=0)
