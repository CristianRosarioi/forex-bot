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
    ) -> None:
        self._s = settings
        self._trades = trade_repo
        self._drawdowns = drawdown_repo
        self._bus = event_bus

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
        losses = self._trades.get_consecutive_losses()
        if losses >= self._s.max_consecutive_losses:
            resume_at = datetime.now(timezone.utc) + timedelta(hours=4)
            return LimitCheckResult(
                passed=False,
                check_name="consecutive_losses",
                reason=f"Max consecutive losses reached: {losses}/{self._s.max_consecutive_losses}",
                severity="WARNING",
                resume_at=resume_at,
            )
        return LimitCheckResult(passed=True, check_name="consecutive_losses")

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
              deactivate_all() queda persistido.
        """
        try:
            with get_session() as session:
                repo = RiskPauseRepository(session)
                pause = repo.get_active()
                if pause is None:
                    return False
                # C-01b: permanent pause (resume_at is None) → always paused
                if pause.resume_at is None:
                    return True
                # Expired pause → deactivate (get_session commits on exit)
                if datetime.now(timezone.utc) >= pause.resume_at:
                    repo.deactivate_all()
                    return False
                return True
        except Exception:
            # C-02: fail-closed — if we can't check, assume paused
            logger.exception("Could not check risk pause state — assuming PAUSED (fail-safe)")
            return True

    def get_active_pause(self):
        try:
            with get_session() as session:
                repo = RiskPauseRepository(session)
                return repo.get_active()
        except Exception:
            logger.exception("Could not get active pause")
            return None

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
