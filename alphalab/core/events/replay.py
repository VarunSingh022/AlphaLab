"""Replay support for stored domain events."""

from collections.abc import Iterable

from alphalab.core.events.base import DomainEvent
from alphalab.core.events.pipeline import EventPipeline
from alphalab.core.events.priorities import EventPriority


class ReplayEngine:
    """Replays stored events through an event pipeline."""

    def __init__(self, pipeline: EventPipeline) -> None:
        """Initialize a replay engine.

        Args:
            pipeline: Pipeline used to publish and dispatch replayed events.
        """

        self._pipeline = pipeline

    def replay(
        self,
        events: Iterable[DomainEvent],
        priority: EventPriority = EventPriority.LOGGING,
    ) -> int:
        """Replay stored events through the pipeline.

        Args:
            events: Stored events to replay in iterable order.
            priority: Priority used for replayed events.

        Returns:
            Number of events dispatched.
        """

        self._pipeline.publish_many(events, priority)
        return self._pipeline.run()
