"""Heap-backed priority queue for domain events."""

from dataclasses import dataclass, field
from heapq import heappop, heappush

from alphalab.core.events.base import DomainEvent
from alphalab.core.events.priorities import EventPriority


@dataclass(frozen=True, order=True, slots=True)
class _QueuedEvent:
    priority: int
    sequence: int
    event: DomainEvent = field(compare=False)


class PriorityEventQueue:
    """Deterministic priority queue with FIFO ordering for priority ties."""

    def __init__(self) -> None:
        """Initialize an empty event queue."""

        self._heap: list[_QueuedEvent] = []
        self._next_sequence = 0

    def push(self, event: DomainEvent, priority: EventPriority) -> None:
        """Add an event to the queue.

        Args:
            event: Event to enqueue.
            priority: Priority assigned to the event.
        """

        if not isinstance(priority, EventPriority):
            raise TypeError("priority must be an EventPriority")
        heappush(
            self._heap,
            _QueuedEvent(priority=int(priority), sequence=self._next_sequence, event=event),
        )
        self._next_sequence += 1

    def pop(self) -> DomainEvent:
        """Remove and return the next event.

        Returns:
            The highest-priority event, preserving FIFO order for ties.

        Raises:
            IndexError: If the queue is empty.
        """

        if self.empty():
            raise IndexError("pop from empty event queue")
        return heappop(self._heap).event

    def peek(self) -> DomainEvent:
        """Return the next event without removing it.

        Returns:
            The event currently at the front of the queue.

        Raises:
            IndexError: If the queue is empty.
        """

        if self.empty():
            raise IndexError("peek from empty event queue")
        return self._heap[0].event

    def empty(self) -> bool:
        """Return whether the queue has no events."""

        return not self._heap

    def clear(self) -> None:
        """Remove all queued events and reset FIFO tie ordering."""

        self._heap.clear()
        self._next_sequence = 0

    def __len__(self) -> int:
        """Return the number of queued events."""

        return len(self._heap)
