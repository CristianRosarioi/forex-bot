"""Validador de órdenes. PUNTO DE CONTROL OBLIGATORIO para toda orden antes de llegar a execution.

REGLA: Ninguna orden pasa a bot/execution/ sin haber sido aprobada por este módulo.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from bot.core.event_bus import EventBus, EventType
from bot.infra.logger import get_logger
from bot.risk.limits import LimitCheckResult

if TYPE_CHECKING:
    from config.settings import RiskSettings
    from bot.db.repository import TradeRepository, DrawdownRepository, SignalRepository
    from bot.risk.sizing import PositionSizer
    from bot.risk.limits import RiskLimits

logger = get_logger(__name__)


@dataclass
class OrderRequest:
    """Solicitud de orden a validar."""
    symbol: str
    direction: str          # BUY / SELL
    entry_price: float
    sl_price: float         # OBLIGATORIO — nunca None
    tp_price: float | None
    signal_id: int | None
    strategy_name: str
    bot_mode: str


@dataclass
class ValidationResult:
    """Resultado del pipeline de validación."""
    approved: bool
    rejection_reason: str | None = None
    rejection_severity: str | None = None
    calculated_lots: Decimal | None = None
    failed_checks: list[LimitCheckResult] = field(default_factory=list)


class OrderValidator:
    """Valida una solicitud de orden a través de un pipeline de 9 pasos.

    NUNCA se salta este validador. NUNCA se modifican sus reglas sin invocar
    al subagente risk-auditor.
    """

    def __init__(
        self,
        settings: "RiskSettings",
        trade_repo: "TradeRepository",
        drawdown_repo: "DrawdownRepository",
        signal_repo: "SignalRepository",
        position_sizer: "PositionSizer",
        risk_limits: "RiskLimits",
        event_bus: EventBus,
        market_calendar=None,     # MarketCalendar — opcional para tests
        economic_calendar=None,   # EconomicCalendar — opcional para tests
        symbol_config: dict | None = None,  # config de symbols.yaml
    ) -> None:
        self._settings = settings
        self._trades = trade_repo
        self._drawdowns = drawdown_repo
        self._signals = signal_repo
        self._sizer = position_sizer
        self._limits = risk_limits
        self._bus = event_bus
        self._calendar = market_calendar
        self._eco_calendar = economic_calendar
        self._symbol_config = symbol_config or {}

    def validate(self, request: OrderRequest, account_state: dict) -> ValidationResult:
        """Ejecuta el pipeline completo de validación.

        Args:
            request: Solicitud de orden.
            account_state: Dict con balance, equity, initial_balance,
                           day_start_balance, week_start_balance, month_start_balance,
                           open_positions, currency, leverage.

        Returns:
            ValidationResult con approved=True y calculated_lots si pasa todo.
        """
        logger.info("Validating order: %s %s %s entry=%s sl=%s",
                    request.strategy_name, request.symbol, request.direction,
                    request.entry_price, request.sl_price)

        # ── 1. SL OBLIGATORIO ──────────────────────────────────────────
        sl_check = self._check_sl(request)
        if sl_check is not None:
            return self._reject(sl_check.reason, sl_check.severity, [sl_check], request)

        # ── 2. SPREAD ──────────────────────────────────────────────────
        spread_check = self._check_spread(request)
        if spread_check is not None:
            return self._reject(spread_check.reason, spread_check.severity, [spread_check], request)

        # ── 3. SESIÓN DE MERCADO ───────────────────────────────────────
        if self._calendar is not None:
            session_check = self._check_session(request)
            if session_check is not None:
                return self._reject(session_check.reason, session_check.severity, [session_check], request)

        # ── 4. CALENDARIO ECONÓMICO ────────────────────────────────────
        if self._eco_calendar is not None:
            eco_check = self._check_economic_calendar(request)
            if eco_check is not None:
                return self._reject(eco_check.reason, eco_check.severity, [eco_check], request)

        # ── 5. KILL SWITCH ACTIVO ──────────────────────────────────────
        ks_check = self._check_kill_switch_active()
        if ks_check is not None:
            return self._reject(ks_check.reason, ks_check.severity, [ks_check], request)

        # ── 6. PAUSAS ACTIVAS ──────────────────────────────────────────
        pause_check = self._check_pause_active()
        if pause_check is not None:
            return self._reject(pause_check.reason, pause_check.severity, [pause_check], request)

        # ── 7. 5 CAPAS DE LÍMITES ──────────────────────────────────────
        failed = self._limits.check_all(
            symbol=request.symbol,
            direction=request.direction,
            current_equity=account_state.get("equity", account_state.get("balance", 0)),
            current_balance=account_state.get("balance", 0),
            initial_balance=account_state.get("initial_balance", account_state.get("balance", 0)),
            day_start_balance=account_state.get("day_start_balance", account_state.get("balance", 0)),
            week_start_balance=account_state.get("week_start_balance", account_state.get("balance", 0)),
            month_start_balance=account_state.get("month_start_balance", account_state.get("balance", 0)),
        )
        if failed:
            worst = max(failed, key=lambda r: ["INFO","WARNING","ERROR","CRITICAL"].index(r.severity))
            # Crear pausa si corresponde
            pause_needed = [r for r in failed if r.resume_at is not None]
            if pause_needed:
                best_pause = max(pause_needed, key=lambda r: ["INFO","WARNING","ERROR","CRITICAL"].index(r.severity))
                self._limits.pause_until(best_pause.resume_at, best_pause.reason or "", best_pause.severity)
            return self._reject(worst.reason, worst.severity, failed, request)

        # ── 8. CALCULAR SIZING ─────────────────────────────────────────
        symbol_info = self._build_symbol_info(request.symbol, account_state)
        symbol_info["direction"] = request.direction
        lots = self._sizer.calculate_lots(
            symbol=request.symbol,
            entry_price=request.entry_price,
            sl_price=request.sl_price,
            account_balance=account_state.get("balance", 0),
            symbol_info=symbol_info,
        )
        if lots == Decimal("0"):
            check = LimitCheckResult(
                passed=False,
                check_name="sizing",
                reason=f"Calculated lots = 0 for {request.symbol} (below lot_min or invalid inputs)",
                severity="WARNING",
            )
            return self._reject(check.reason, check.severity, [check], request)

        # ── 9. APROBADO ────────────────────────────────────────────────
        logger.info("Order APPROVED: %s %s %s lots=%s",
                    request.symbol, request.direction, request.strategy_name, lots)
        self._bus.publish(EventType.SIGNAL_GENERATED, {
            "symbol": request.symbol,
            "timeframe": "unknown",
            "direction": request.direction,
            "strategy_name": request.strategy_name,
            "entry_price": request.entry_price,
            "sl_price": request.sl_price,
            "tp_price": request.tp_price,
            "reason": f"Validated by OrderValidator",
        })
        return ValidationResult(approved=True, calculated_lots=lots)

    # ──────────────────────────────────────────────
    # Checks internos
    # ──────────────────────────────────────────────

    def _check_sl(self, req: OrderRequest) -> LimitCheckResult | None:
        if req.sl_price is None:
            return LimitCheckResult(passed=False, check_name="sl_required",
                                    reason="Stop loss is MANDATORY — no SL, no order",
                                    severity="CRITICAL")
        if req.sl_price == req.entry_price:
            return LimitCheckResult(passed=False, check_name="sl_required",
                                    reason="SL price equals entry price — invalid",
                                    severity="CRITICAL")
        if req.direction.upper() == "BUY" and req.sl_price >= req.entry_price:
            return LimitCheckResult(passed=False, check_name="sl_direction",
                                    reason=f"BUY order: SL {req.sl_price} must be below entry {req.entry_price}",
                                    severity="CRITICAL")
        if req.direction.upper() == "SELL" and req.sl_price <= req.entry_price:
            return LimitCheckResult(passed=False, check_name="sl_direction",
                                    reason=f"SELL order: SL {req.sl_price} must be above entry {req.entry_price}",
                                    severity="CRITICAL")
        return None

    def _check_spread(self, req: OrderRequest) -> LimitCheckResult | None:
        sym_cfg = self._symbol_config.get(req.symbol, {})
        spread_max = sym_cfg.get("spread_max")
        if spread_max is None:
            return None  # sin config, no bloqueamos
        current_spread = self._get_current_spread(req.symbol)
        if current_spread is not None and current_spread > spread_max:
            return LimitCheckResult(
                passed=False, check_name="spread_check",
                reason=f"Spread too wide for {req.symbol}: {current_spread:.1f} > max {spread_max}",
                severity="WARNING",
            )
        return None

    def _check_session(self, req: OrderRequest) -> LimitCheckResult | None:
        if self._calendar.is_weekend():
            return LimitCheckResult(passed=False, check_name="session_check",
                                    reason="Market is closed (weekend)", severity="INFO")
        if not self._calendar.symbol_allowed_in_current_session(req.symbol):
            return LimitCheckResult(passed=False, check_name="session_check",
                                    reason=f"{req.symbol} not allowed in current session",
                                    severity="INFO")
        return None

    def _check_economic_calendar(self, req: OrderRequest) -> LimitCheckResult | None:
        if self._eco_calendar.is_blocked_for_symbol(req.symbol):
            return LimitCheckResult(
                passed=False, check_name="economic_calendar",
                reason=f"Trading blocked for {req.symbol} due to high-impact economic event",
                severity="INFO",
            )
        return None

    def _check_kill_switch_active(self) -> LimitCheckResult | None:
        """Verifica si hay un evento KILL_SWITCH activo en bot_events recientes."""
        # La señal de kill switch se persiste via log_event/EventBus.
        # Aquí verificamos si hay una pausa CRITICAL activa que indique kill switch.
        pause = self._limits.get_active_pause()
        if pause and pause.severity == "CRITICAL":
            return LimitCheckResult(
                passed=False, check_name="kill_switch_active",
                reason=f"Kill switch active: {pause.reason}",
                severity="CRITICAL",
            )
        return None

    def _check_pause_active(self) -> LimitCheckResult | None:
        if self._limits.is_paused():
            pause = self._limits.get_active_pause()
            reason = pause.reason if pause else "Active risk pause"
            return LimitCheckResult(
                passed=False, check_name="pause_active",
                reason=reason, severity="ERROR",
                resume_at=pause.resume_at if pause else None,
            )
        return None

    def _get_current_spread(self, symbol: str) -> float | None:
        """Intenta obtener el spread actual de MT5. Retorna None si no disponible."""
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(symbol)
            if info is not None:
                # spread en puntos, convertir a pips
                return info.spread * info.point * 10
        except Exception:
            pass
        return None

    def _build_symbol_info(self, symbol: str, account_state: dict) -> dict:
        """Construye el dict de symbol_info para el sizer."""
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(symbol)
            if info is not None:
                return {
                    "contract_size": info.trade_contract_size,
                    "point": info.point,
                    "tick_value": info.trade_tick_value,
                    "tick_size": info.trade_tick_size,
                    "volume_min": info.volume_min,
                    "volume_max": info.volume_max,
                    "volume_step": info.volume_step,
                }
        except Exception:
            pass
        # Fallback para tests / sin MT5
        return {
            "contract_size": 100000,
            "point": 0.00001,
            "tick_value": 1.0,
            "tick_size": 0.00001,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
        }

    def _reject(self, reason: str | None, severity: str | None,
                failed: list[LimitCheckResult], request: OrderRequest) -> ValidationResult:
        logger.warning("Order REJECTED [%s]: %s | %s %s %s",
                       severity, reason, request.symbol, request.direction, request.strategy_name)
        return ValidationResult(
            approved=False,
            rejection_reason=reason,
            rejection_severity=severity,
            failed_checks=failed,
        )
