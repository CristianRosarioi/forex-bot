"""Cálculo de tamaño de posición. Fixed fractional sobre balance disponible, ajustado por correlación."""

from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
from typing import TYPE_CHECKING

from bot.infra.logger import get_logger

if TYPE_CHECKING:
    from config.settings import RiskSettings
    from bot.db.repository import TradeRepository

logger = get_logger(__name__)

# Grupos de correlación direccional
CORRELATED_GROUPS: dict[str, list[str]] = {
    "EUR_USD_GROUP": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],
    "USD_PAIRS_INVERSE": ["USDJPY", "USDCAD", "USDCHF"],
    "INDICES": ["US500", "NAS100"],
    "JPY_CROSSES": ["GBPJPY", "EURJPY"],
}

# Número de decimales de pip por símbolo (pips normales = 4 para forex, 2 para JPY)
_JPY_PAIRS = {"USDJPY", "GBPJPY", "EURJPY", "CADJPY", "AUDJPY", "NZDJPY", "CHFJPY"}


def _pip_digits(symbol: str) -> int:
    """Retorna cuántos decimales tiene 1 pip para el símbolo."""
    return 2 if symbol.upper() in _JPY_PAIRS else 4


def _get_correlated_group(symbol: str) -> list[str] | None:
    """Devuelve el grupo de correlación del símbolo, o None si no pertenece a ninguno."""
    sym = symbol.upper()
    for group in CORRELATED_GROUPS.values():
        if sym in group:
            return group
    return None


class PositionSizer:
    """Calcula el tamaño de posición usando Fixed Fractional con ajuste por correlación.

    Usa Decimal para precisión. Nunca float para cálculos de dinero.
    """

    def __init__(self, settings: "RiskSettings", trade_repo: "TradeRepository") -> None:
        self._settings = settings
        self._trade_repo = trade_repo

    def calculate_lots(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        account_balance: float,
        symbol_info: dict,
    ) -> Decimal:
        """Calcula los lotes a operar.

        Args:
            symbol: Ej. "EURUSD"
            entry_price: Precio de entrada
            sl_price: Precio del stop loss
            account_balance: Balance de la cuenta en USD
            symbol_info: Dict con keys: contract_size, point, tick_value,
                         tick_size, lot_min, lot_max, lot_step, volume_min,
                         volume_max, volume_step, currency_profit

        Returns:
            Decimal con los lotes redondeados al lot_step. Decimal("0") si < lot_min.
        """
        sym = symbol.upper()
        balance = Decimal(str(account_balance))
        entry = Decimal(str(entry_price))
        sl = Decimal(str(sl_price))

        # 1. Pips de riesgo
        price_diff = abs(entry - sl)
        pip_size = Decimal("0.01") if sym in _JPY_PAIRS else Decimal("0.0001")
        pips_at_risk = price_diff / pip_size

        logger.debug("Sizing %s: entry=%s sl=%s price_diff=%s pips_at_risk=%s",
                     sym, entry, sl, price_diff, pips_at_risk)

        # 2. Monto a arriesgar
        risk_pct = Decimal(str(self._settings.risk_pct_per_trade)) / Decimal("100")
        risk_amount = balance * risk_pct
        logger.debug("risk_amount=%s (%.2f%% of %s)", risk_amount, float(risk_pct)*100, balance)

        # 3. Pip value en USD por lote estándar
        # Para pares con USD como quote (EURUSD, GBPUSD): pip_value = pip_size * contract_size
        # Para pares con USD como base (USDJPY): pip_value = pip_size * contract_size / price
        # Para índices: usar tick_value / tick_size
        contract_size = Decimal(str(symbol_info.get("contract_size", 100000)))
        tick_value = symbol_info.get("tick_value")
        tick_size = symbol_info.get("tick_size")

        if tick_value and tick_size and tick_size > 0:
            # Índices u otros instrumentos con tick_value explícito
            pip_value_per_lot = Decimal(str(tick_value)) / Decimal(str(tick_size)) * pip_size
        elif sym.endswith("USD"):
            # Quote currency es USD → pip_value directo
            pip_value_per_lot = pip_size * contract_size
        elif sym.startswith("USD"):
            # Base currency es USD → pip_value inversamente proporcional al precio
            pip_value_per_lot = pip_size * contract_size / entry
        else:
            # M-02: Cross pair without MT5 tick data — cannot size accurately
            logger.warning(
                "Cross pair %s has no MT5 tick_value — cannot calculate pip value accurately. "
                "Returning 0 lots to prevent over-sizing.",
                sym
            )
            pip_value_per_lot = Decimal("0")

        logger.debug("pip_value_per_lot=%s", pip_value_per_lot)

        # 4. Lotes base
        if pips_at_risk == 0 or pip_value_per_lot == 0:
            logger.warning("Cannot size %s: pips_at_risk=%s pip_value=%s", sym, pips_at_risk, pip_value_per_lot)
            return Decimal("0")

        lots_base = risk_amount / (pips_at_risk * pip_value_per_lot)
        logger.debug("lots_base=%s", lots_base)

        # 5. Ajuste por correlación
        correlation_factor = self._correlation_factor(sym, symbol_info.get("direction", "BUY"))
        lots_adjusted = lots_base * correlation_factor
        logger.debug("correlation_factor=%s lots_adjusted=%s", correlation_factor, lots_adjusted)

        # 6. Redondear hacia abajo al lot_step
        lot_step = Decimal(str(symbol_info.get("volume_step", symbol_info.get("lot_step", "0.01"))))
        if lot_step <= 0:
            lot_step = Decimal("0.01")
        lots_rounded = (lots_adjusted / lot_step).to_integral_value(rounding=ROUND_DOWN) * lot_step

        # 7. Validar min/max
        lot_min = Decimal(str(symbol_info.get("volume_min", symbol_info.get("lot_min", "0.01"))))
        lot_max = Decimal(str(symbol_info.get("volume_max", symbol_info.get("lot_max", "100"))))

        if lots_rounded < lot_min:
            logger.warning("Calculated lots %s < lot_min %s for %s — returning 0", lots_rounded, lot_min, sym)
            return Decimal("0")

        lots_final = min(lots_rounded, lot_max)
        logger.info("Position size for %s: %s lots (risk=%s pips=%s)", sym, lots_final, risk_amount, pips_at_risk)
        return lots_final

    def _correlation_factor(self, symbol: str, direction: str) -> Decimal:
        """M-01: Escala el factor de correlación con el número de posiciones correlacionadas.

        - 0 correlated → return 1.0 (full size)
        - 1 correlated → return 0.5 (half size)
        - 2+ correlated → return 0.25 (quarter size)
        """
        try:
            open_trades = self._trade_repo.get_open()
        except Exception:
            logger.exception("Could not fetch open trades for correlation check")
            return Decimal("1")

        group = _get_correlated_group(symbol)
        if group is None or not open_trades:
            return Decimal("1")

        count = sum(
            1 for t in open_trades
            if str(t.symbol).upper() in group
            and str(t.symbol).upper() != symbol
            and str(t.direction).upper() == direction.upper()
        )
        if count == 0:
            return Decimal("1")
        if count == 1:
            logger.info("Correlation detected: 1 correlated trade → sizing 50%% for %s %s", symbol, direction)
            return Decimal("0.5")
        logger.info("Correlation detected: %d correlated trades → sizing 25%% for %s %s", count, symbol, direction)
        return Decimal("0.25")
