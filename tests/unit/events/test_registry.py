from dataclasses import dataclass
from datetime import UTC, datetime

from alphalab.core.events import DomainEvent, EventRegistry

NOW = datetime(2026, 2, 3, 10, 15, tzinfo=UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalEvent(DomainEvent):
    symbol: str = "AAPL"


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderEvent(DomainEvent):
    order_ref: str = "order-1"


def test_registry_subscribe_returns_matching_handler() -> None:
    registry = EventRegistry()
    calls: list[str] = []

    def handler(event: SignalEvent) -> None:
        calls.append(event.symbol)

    registry.subscribe(SignalEvent, handler)

    for registered_handler in registry.handlers_for(SignalEvent(timestamp=NOW)):
        registered_handler(SignalEvent(timestamp=NOW, symbol="MSFT"))

    assert calls == ["MSFT"]


def test_registry_unsubscribe_removes_handler() -> None:
    registry = EventRegistry()
    calls: list[str] = []

    def handler(event: SignalEvent) -> None:
        calls.append(event.symbol)

    registry.subscribe(SignalEvent, handler)
    registry.unsubscribe(SignalEvent, handler)

    assert registry.handlers_for(SignalEvent(timestamp=NOW)) == ()


def test_registry_supports_multiple_handlers_in_subscription_order() -> None:
    registry = EventRegistry()
    calls: list[str] = []

    def first(event: SignalEvent) -> None:
        calls.append(f"first:{event.symbol}")

    def second(event: SignalEvent) -> None:
        calls.append(f"second:{event.symbol}")

    registry.subscribe(SignalEvent, first)
    registry.subscribe(SignalEvent, second)

    for handler in registry.handlers_for(SignalEvent(timestamp=NOW, symbol="NVDA")):
        handler(SignalEvent(timestamp=NOW, symbol="NVDA"))

    assert calls == ["first:NVDA", "second:NVDA"]


def test_registry_matches_base_event_subscriptions() -> None:
    registry = EventRegistry()
    calls: list[str] = []

    def handler(event: DomainEvent) -> None:
        calls.append(event.id)

    event = OrderEvent(timestamp=NOW)
    registry.subscribe(DomainEvent, handler)

    for registered_handler in registry.handlers_for(event):
        registered_handler(event)

    assert calls == [event.id]
