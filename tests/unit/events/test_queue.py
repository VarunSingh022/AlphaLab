from datetime import UTC, datetime, timedelta

import pytest

from alphalab.core.events import DomainEvent, EventPriority, PriorityEventQueue

NOW = datetime(2026, 2, 3, 10, 15, tzinfo=UTC)


def event_at(offset_seconds: int) -> DomainEvent:
    return DomainEvent(timestamp=NOW + timedelta(seconds=offset_seconds))


def test_priority_queue_preserves_fifo_order_for_equal_priority() -> None:
    queue = PriorityEventQueue()
    first = event_at(1)
    second = event_at(2)
    third = event_at(3)

    queue.push(first, EventPriority.ORDER)
    queue.push(second, EventPriority.ORDER)
    queue.push(third, EventPriority.ORDER)

    assert [queue.pop(), queue.pop(), queue.pop()] == [first, second, third]


def test_priority_queue_pops_lower_priority_value_first() -> None:
    queue = PriorityEventQueue()
    low = event_at(1)
    high = event_at(2)
    middle = event_at(3)

    queue.push(low, EventPriority.LOGGING)
    queue.push(high, EventPriority.SYSTEM)
    queue.push(middle, EventPriority.SIGNAL)

    assert [queue.pop(), queue.pop(), queue.pop()] == [high, middle, low]


def test_priority_queue_peek_does_not_remove_event() -> None:
    queue = PriorityEventQueue()
    event = event_at(1)

    queue.push(event, EventPriority.MARKET)

    assert queue.peek() == event
    assert len(queue) == 1
    assert queue.pop() == event


def test_priority_queue_clear_removes_all_events() -> None:
    queue = PriorityEventQueue()
    queue.push(event_at(1), EventPriority.MARKET)
    queue.push(event_at(2), EventPriority.SYSTEM)

    queue.clear()

    assert queue.empty()
    assert len(queue) == 0


def test_priority_queue_raises_for_empty_pop_and_peek() -> None:
    queue = PriorityEventQueue()

    with pytest.raises(IndexError):
        queue.pop()
    with pytest.raises(IndexError):
        queue.peek()
