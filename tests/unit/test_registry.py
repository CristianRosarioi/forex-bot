"""Tests for bot/strategy/registry.py"""

import pytest

from bot.strategy.base import BaseStrategy, StrategyContext, Signal
from bot.strategy.registry import StrategyRegistry, load_default_strategies


class EnabledStrategy(BaseStrategy):
    name = "enabled"

    def analyze(self, ctx: StrategyContext) -> Signal | None:
        return None


class DisabledStrategy(BaseStrategy):
    name = "disabled"

    def is_enabled(self) -> bool:
        return False

    def analyze(self, ctx: StrategyContext) -> Signal | None:
        return None


class TestStrategyRegistry:
    def test_register_and_get(self):
        registry = StrategyRegistry()
        strategy = EnabledStrategy()
        registry.register(strategy)
        assert registry.get("enabled") is strategy

    def test_unregister_removes_strategy(self):
        registry = StrategyRegistry()
        strategy = EnabledStrategy()
        registry.register(strategy)
        registry.unregister("enabled")
        assert registry.get("enabled") is None

    def test_unregister_nonexistent_no_error(self):
        registry = StrategyRegistry()
        # Should not raise
        registry.unregister("does_not_exist")

    def test_get_all_enabled_filters_disabled(self):
        registry = StrategyRegistry()
        registry.register(EnabledStrategy())
        registry.register(DisabledStrategy())

        enabled = registry.get_all_enabled()
        names = [s.name for s in enabled]
        assert "enabled" in names
        assert "disabled" not in names

    def test_get_returns_none_for_unknown(self):
        registry = StrategyRegistry()
        assert registry.get("nonexistent") is None


class TestLoadDefaultStrategies:
    def test_registry_contains_retest(self):
        registry = load_default_strategies()
        assert registry.get("retest") is not None

    def test_get_all_enabled_not_empty(self):
        registry = load_default_strategies()
        assert len(registry.get_all_enabled()) >= 1
