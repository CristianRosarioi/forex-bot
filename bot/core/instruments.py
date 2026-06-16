"""Definiciones de instrumentos compartidas (pip size, clasificación de símbolos).

Fuente ÚNICA de verdad para el tamaño de pip por símbolo. Importado por
bot/risk/validator.py, bot/risk/sizing.py, bot/execution/order_manager.py y
bot/execution/position_tracker.py para evitar copias divergentes del cálculo.

Módulo hoja: NO importa nada del proyecto (evita ciclos de importación).
"""

from __future__ import annotations

# Metales (oro). Pip de oro = 0.1 (un pip = $0.10 de movimiento).
GOLD_SYMBOLS: frozenset[str] = frozenset({"XAUUSD"})

# Pares con JPY como quote: pip = 0.01.
JPY_PAIRS: frozenset[str] = frozenset(
    {"USDJPY", "GBPJPY", "EURJPY", "CADJPY", "AUDJPY", "NZDJPY", "CHFJPY"}
)


def pip_size(symbol: str) -> float:
    """Tamaño de 1 pip para el símbolo.

    - Oro (XAUUSD): 0.1
    - Pares JPY: 0.01
    - Resto de forex: 0.0001
    """
    sym = symbol.upper()
    if sym in GOLD_SYMBOLS:
        return 0.1
    if sym in JPY_PAIRS:
        return 0.01
    return 0.0001


def pip_digits(symbol: str) -> int:
    """Número de decimales que representa 1 pip para el símbolo."""
    sym = symbol.upper()
    if sym in GOLD_SYMBOLS:
        return 1
    if sym in JPY_PAIRS:
        return 2
    return 4
