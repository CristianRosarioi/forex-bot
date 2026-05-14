"""Registro central de estrategias. Permite activar/desactivar plugins en caliente."""

from __future__ import annotations

from bot.strategy.base import BaseStrategy
from bot.infra.logger import get_logger

logger = get_logger(__name__)


class StrategyRegistry:
    def __init__(self):
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        self._strategies[strategy.name] = strategy
        logger.info("Strategy registered: %s", strategy.name)

    def unregister(self, name: str) -> None:
        self._strategies.pop(name, None)

    def get(self, name: str) -> BaseStrategy | None:
        return self._strategies.get(name)

    def get_all_enabled(self) -> list[BaseStrategy]:
        return [s for s in self._strategies.values() if s.is_enabled()]


def load_default_strategies() -> StrategyRegistry:
    registry = StrategyRegistry()
    from bot.strategy.retest import RetestStrategy
    from bot.strategy.breakout import BreakoutStrategy
    from bot.strategy.fade_failure import FadeFailureStrategy
    registry.register(RetestStrategy())
    registry.register(BreakoutStrategy())
    registry.register(FadeFailureStrategy())
    return registry
