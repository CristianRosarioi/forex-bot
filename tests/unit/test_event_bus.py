"""Tests unitarios para bot.core.event_bus."""

import pytest
from bot.core.event_bus import EventBus, EventType


class TestEventBusSubscribePublish:
    def test_handler_receives_payload(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.BAR_CLOSED, lambda p: received.append(p))
        bus.publish(EventType.BAR_CLOSED, {"symbol": "EURUSD"})
        assert received == [{"symbol": "EURUSD"}]

    def test_multiple_handlers_all_called(self):
        bus = EventBus()
        calls = []
        bus.subscribe(EventType.BAR_CLOSED, lambda p: calls.append("a"))
        bus.subscribe(EventType.BAR_CLOSED, lambda p: calls.append("b"))
        bus.publish(EventType.BAR_CLOSED, {})
        assert calls == ["a", "b"]

    def test_no_handler_for_event_type(self):
        bus = EventBus()
        # No debe lanzar excepción si no hay handlers
        bus.publish(EventType.SIGNAL_GENERATED, {"x": 1})

    def test_handler_receives_correct_payload(self):
        bus = EventBus()
        payloads = []
        bus.subscribe(EventType.ORDER_PLACED, lambda p: payloads.append(p))
        bus.publish(EventType.ORDER_PLACED, {"order_id": 42, "symbol": "GBPUSD"})
        assert payloads[0]["order_id"] == 42
        assert payloads[0]["symbol"] == "GBPUSD"

    def test_different_event_types_isolated(self):
        bus = EventBus()
        bar_calls, signal_calls = [], []
        bus.subscribe(EventType.BAR_CLOSED, lambda p: bar_calls.append(p))
        bus.subscribe(EventType.SIGNAL_GENERATED, lambda p: signal_calls.append(p))
        bus.publish(EventType.BAR_CLOSED, {"tf": "M15"})
        assert len(bar_calls) == 1
        assert len(signal_calls) == 0


class TestEventBusUnsubscribe:
    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        calls = []
        handler = lambda p: calls.append(p)
        bus.subscribe(EventType.BAR_CLOSED, handler)
        bus.unsubscribe(EventType.BAR_CLOSED, handler)
        bus.publish(EventType.BAR_CLOSED, {})
        assert calls == []

    def test_unsubscribe_nonexistent_handler_is_noop(self):
        bus = EventBus()
        handler = lambda p: None
        # No debe lanzar excepción
        bus.unsubscribe(EventType.BAR_CLOSED, handler)

    def test_subscribe_same_handler_twice_calls_once(self):
        bus = EventBus()
        calls = []
        handler = lambda p: calls.append(p)
        bus.subscribe(EventType.BAR_CLOSED, handler)
        bus.subscribe(EventType.BAR_CLOSED, handler)  # duplicado
        bus.publish(EventType.BAR_CLOSED, {})
        assert len(calls) == 1


class TestEventBusExceptionIsolation:
    def test_failing_handler_does_not_block_others(self):
        bus = EventBus()
        calls = []

        def bad_handler(p):
            raise RuntimeError("boom")

        def good_handler(p):
            calls.append(p)

        bus.subscribe(EventType.BAR_CLOSED, bad_handler)
        bus.subscribe(EventType.BAR_CLOSED, good_handler)
        bus.publish(EventType.BAR_CLOSED, {"x": 1})

        assert calls == [{"x": 1}]

    def test_multiple_failing_handlers_all_others_called(self):
        bus = EventBus()
        calls = []

        bus.subscribe(EventType.BAR_CLOSED, lambda p: (_ for _ in ()).throw(ValueError("a")))
        bus.subscribe(EventType.BAR_CLOSED, lambda p: calls.append("middle"))
        bus.subscribe(EventType.BAR_CLOSED, lambda p: (_ for _ in ()).throw(ValueError("b")))
        bus.subscribe(EventType.BAR_CLOSED, lambda p: calls.append("last"))

        bus.publish(EventType.BAR_CLOSED, {})
        assert "middle" in calls
        assert "last" in calls


class TestEventTypeConstants:
    def test_all_expected_constants_exist(self):
        expected = [
            "BAR_CLOSED", "SIGNAL_GENERATED", "ORDER_PLACED", "ORDER_FILLED",
            "ORDER_REJECTED", "POSITION_CLOSED", "MT5_DISCONNECTED", "MT5_RECONNECTED",
            "DRAWDOWN_LIMIT_HIT", "KILL_SWITCH_TRIGGERED",
        ]
        for name in expected:
            assert hasattr(EventType, name), f"Missing EventType.{name}"

    def test_constants_are_strings(self):
        assert isinstance(EventType.BAR_CLOSED, str)
        assert isinstance(EventType.KILL_SWITCH_TRIGGERED, str)
