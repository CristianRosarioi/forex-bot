"""Clase base abstracta BaseStrategy y dataclass Signal que toda estrategia debe implementar."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from bot.analysis.swing import SwingPoint
from bot.analysis.structure import MarketStructure
from bot.analysis.levels import Level
from bot.analysis.trend import TrendBias
from bot.analysis.volume import VolumeProfile


@dataclass
class StrategyContext:
    symbol: str
    timeframe: str
    bars: pd.DataFrame          # entry TF bars (M15 or H1)
    bars_h4: pd.DataFrame
    bars_d1: pd.DataFrame
    swings: list[SwingPoint]
    structure: MarketStructure
    levels: list[Level]
    trend: TrendBias
    volume: VolumeProfile
    current_price: float
    timestamp: int              # unix seconds of last bar


@dataclass
class Signal:
    symbol: str
    timeframe: str
    strategy_name: str
    signal_type: str    # "retest" / "breakout" / "fade"
    direction: str      # "BUY" / "SELL"
    entry_price: float
    sl_price: float     # MANDATORY
    tp_price: float | None
    reason: str
    confidence: float
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract base for all strategies. MUST be stateless.

    Rules:
    - No mutable instance state
    - No DB access
    - No MT5 access
    - No EventBus access
    - Receives StrategyContext, returns Signal or None
    """
    name: str = "base"

    @abstractmethod
    def analyze(self, ctx: StrategyContext) -> Signal | None:
        pass

    def is_enabled(self) -> bool:
        return True
