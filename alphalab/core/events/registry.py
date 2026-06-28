from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from alphalab.core.events.base import DomainEvent

EventT = TypeVar("EventT", bound=DomainEvent)

EventHandler = Callable[[Any], None]


@dataclass(frozen=True, slots=True)
class _Subscription:
    event_type: type[DomainEvent]
    handler: EventHandler


class EventRegistry:
    """Registry of event handler subscriptions."""

    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []

    def subscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], None],
    ) -> None:
        subscription = _Subscription(
            event_type=event_type,
            handler=cast(EventHandler, handler),
        )

        if subscription not in self._subscriptions:
            self._subscriptions.append(subscription)

    def unsubscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], None],
    ) -> None:
        erased = cast(EventHandler, handler)

        self._subscriptions = [
            s
            for s in self._subscriptions
            if not (s.event_type is event_type and s.handler == erased)
        ]

    def handlers_for(
        self,
        event: DomainEvent,
    ) -> tuple[EventHandler, ...]:
        return tuple(s.handler for s in self._subscriptions if isinstance(event, s.event_type))

    def clear(self) -> None:
        self._subscriptions.clear()

    def __len__(self) -> int:
        return len(self._subscriptions)
