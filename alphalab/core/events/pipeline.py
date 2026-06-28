"""High-level event pipeline API."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from alphalab.core.events.base import DomainEvent
from alphalab.core.events.dispatcher import EventDispatcher
from alphalab.core.events.middleware import EventMiddleware
from alphalab.core.events.priorities import EventPriority
from alphalab.core.events.queue import PriorityEventQueue
from alphalab.core.events.registry import EventRegistry

EventT = TypeVar("EventT", bound=DomainEvent)


class EventPipeline:
    """Owns event queueing, subscriptions, and deterministic dispatch."""

    def __init__(
        self,
        middleware: tuple[EventMiddleware, ...] = (),
    ) -> None:
        """Initialize the event pipeline."""

        self._queue = PriorityEventQueue()
        self._registry = EventRegistry()
        self._dispatcher = EventDispatcher(
            self._queue,
            self._registry,
            middleware,
        )

    @property
    def queue(self) -> PriorityEventQueue:
        """Return the owned priority queue."""
        return self._queue

    @property
    def registry(self) -> EventRegistry:
        """Return the event registry."""
        return self._registry

    @property
    def dispatcher(self) -> EventDispatcher:
        """Return the dispatcher."""
        return self._dispatcher

    def publish(
        self,
        event: DomainEvent,
        priority: EventPriority = EventPriority.LOGGING,
    ) -> None:
        """Publish a single event."""

        self._queue.push(event, priority)

    def publish_many(
        self,
        events: Iterable[DomainEvent],
        priority: EventPriority = EventPriority.LOGGING,
    ) -> None:
        """Publish multiple events."""

        for event in events:
            self.publish(event, priority)

    def subscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], None],
    ) -> None:
        """Register an event handler."""

        self._registry.subscribe(event_type, handler)

    def unsubscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], None],
    ) -> None:
        """Remove an event handler."""

        self._registry.unsubscribe(event_type, handler)

    def run(self) -> int:
        """Dispatch all queued events."""

        return self._dispatcher.run()

    def drain(self) -> int:
        """Alias for run()."""

        return self.run()
