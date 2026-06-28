"""Middleware interfaces and built-ins for event dispatch."""

from collections.abc import Callable
from logging import Logger, getLogger
from time import perf_counter
from typing import Protocol

from alphalab.core.events.base import DomainEvent

EventNextHandler = Callable[[DomainEvent], None]
TimingObserver = Callable[[DomainEvent, float], None]


class EventMiddleware(Protocol):
    """Middleware interface for wrapping event handler execution."""

    def process(self, event: DomainEvent, next_handler: EventNextHandler) -> None:
        """Process an event and call the next handler when appropriate.

        Args:
            event: Event currently being dispatched.
            next_handler: Next callable in the middleware chain.
        """


class LoggingMiddleware:
    """Middleware that emits debug logs around event processing."""

    def __init__(self, logger: Logger | None = None) -> None:
        """Initialize logging middleware.

        Args:
            logger: Optional logger to receive debug messages.
        """

        self._logger = logger if logger is not None else getLogger("alphalab.core.events")

    def process(self, event: DomainEvent, next_handler: EventNextHandler) -> None:
        """Log event processing and forward to the next handler.

        Args:
            event: Event currently being dispatched.
            next_handler: Next callable in the middleware chain.
        """

        self._logger.debug("Processing event %s", event.id)
        next_handler(event)
        self._logger.debug("Processed event %s", event.id)


class TimingMiddleware:
    """Middleware that measures downstream event processing duration."""

    def __init__(
        self,
        observer: TimingObserver | None = None,
        logger: Logger | None = None,
    ) -> None:
        """Initialize timing middleware.

        Args:
            observer: Optional callback receiving event and elapsed seconds.
            logger: Optional logger to receive timing debug messages.
        """

        self._observer = observer
        self._logger = logger if logger is not None else getLogger("alphalab.core.events")

    def process(self, event: DomainEvent, next_handler: EventNextHandler) -> None:
        """Measure processing time and forward to the next handler.

        Args:
            event: Event currently being dispatched.
            next_handler: Next callable in the middleware chain.
        """

        start = perf_counter()
        try:
            next_handler(event)
        finally:
            elapsed = perf_counter() - start
            if self._observer is not None:
                self._observer(event, elapsed)
            self._logger.debug("Processed event %s in %.9f seconds", event.id, elapsed)
