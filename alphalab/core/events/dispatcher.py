"""Deterministic single-threaded event dispatcher."""

from alphalab.core.events.base import DomainEvent
from alphalab.core.events.middleware import EventMiddleware, EventNextHandler
from alphalab.core.events.queue import PriorityEventQueue
from alphalab.core.events.registry import EventHandler, EventRegistry


class EventDispatcher:
    """Dispatch queued events to registered handlers in deterministic order."""

    def __init__(
        self,
        queue: PriorityEventQueue,
        registry: EventRegistry,
        middleware: tuple[EventMiddleware, ...] = (),
    ) -> None:
        """Initialize a dispatcher.

        Args:
            queue: Queue consumed by the dispatcher.
            registry: Handler registry used for event lookup.
            middleware: Middleware applied around every handler call.
        """

        self._queue = queue
        self._registry = registry
        self._middleware = middleware
        self._running = False

    def run(self) -> int:
        """Process queued events until the queue is empty.

        Returns:
            Number of events removed from the queue.

        Raises:
            RuntimeError: If dispatch is already running.
        """

        if self._running:
            raise RuntimeError("nested dispatch is not allowed")
        self._running = True
        processed = 0
        try:
            while not self._queue.empty():
                self._dispatch_event(self._queue.pop())
                processed += 1
        finally:
            self._running = False
        return processed

    def _dispatch_event(self, event: DomainEvent) -> None:
        handlers = self._registry.handlers_for(event)
        for handler in handlers:
            self._invoke_handler(event, handler)

    def _invoke_handler(self, event: DomainEvent, handler: EventHandler) -> None:
        next_handler: EventNextHandler = handler
        for middleware in reversed(self._middleware):
            next_handler = self._wrap_middleware(middleware, next_handler)
        next_handler(event)

    def _wrap_middleware(
        self,
        middleware: EventMiddleware,
        downstream: EventNextHandler,
    ) -> EventNextHandler:
        def wrapped(event: DomainEvent) -> None:
            middleware.process(event, downstream)

        return wrapped
