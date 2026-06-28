from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from alphalab.core.events import (
    DomainEvent,
    EventMiddleware,
    EventNextHandler,
    EventPipeline,
    EventPriority,
    ReplayEngine,
    TimingMiddleware,
)

NOW = datetime(2026, 2, 3, 10, 15, tzinfo=UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineEvent(DomainEvent):
    name: str = "event"


@dataclass(frozen=True, slots=True, kw_only=True)
class OtherEvent(DomainEvent):
    name: str = "other"


class RecordingMiddleware:
    def __init__(self, name: str, calls: list[str]) -> None:
        self._name = name
        self._calls = calls

    def process(self, event: DomainEvent, next_handler: EventNextHandler) -> None:
        self._calls.append(f"{self._name}:before:{event.id}")
        next_handler(event)
        self._calls.append(f"{self._name}:after:{event.id}")


def test_dispatcher_processes_all_published_events() -> None:
    pipeline = EventPipeline()
    handled: list[str] = []

    def handler(event: PipelineEvent) -> None:
        handled.append(event.name)

    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish_many(
        [
            PipelineEvent(timestamp=NOW, name="first"),
            PipelineEvent(timestamp=NOW, name="second"),
            PipelineEvent(timestamp=NOW, name="third"),
        ],
        EventPriority.SIGNAL,
    )

    processed = pipeline.run()

    assert processed == 3
    assert handled == ["first", "second", "third"]
    assert pipeline.queue.empty()


def test_pipeline_dispatches_by_priority_then_fifo() -> None:
    pipeline = EventPipeline()
    handled: list[str] = []

    def handler(event: PipelineEvent) -> None:
        handled.append(event.name)

    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish(PipelineEvent(timestamp=NOW, name="low"), EventPriority.LOGGING)
    pipeline.publish(PipelineEvent(timestamp=NOW, name="high-1"), EventPriority.SYSTEM)
    pipeline.publish(PipelineEvent(timestamp=NOW, name="high-2"), EventPriority.SYSTEM)
    pipeline.publish(PipelineEvent(timestamp=NOW, name="mid"), EventPriority.ORDER)

    assert pipeline.run() == 4
    assert handled == ["high-1", "high-2", "mid", "low"]


def test_pipeline_handlers_ignore_unmatched_event_types() -> None:
    pipeline = EventPipeline()
    handled: list[str] = []

    def handler(event: PipelineEvent) -> None:
        handled.append(event.name)

    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish(OtherEvent(timestamp=NOW, name="ignored"), EventPriority.SYSTEM)

    assert pipeline.run() == 1
    assert handled == []


def test_pipeline_drain_dispatches_events() -> None:
    pipeline = EventPipeline()
    handled: list[str] = []

    def handler(event: PipelineEvent) -> None:
        handled.append(event.name)

    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish(PipelineEvent(timestamp=NOW, name="drained"), EventPriority.MARKET)

    assert pipeline.drain() == 1
    assert handled == ["drained"]


def test_middleware_execution_order_wraps_handler() -> None:
    calls: list[str] = []
    first: EventMiddleware = RecordingMiddleware("first", calls)
    second: EventMiddleware = RecordingMiddleware("second", calls)
    pipeline = EventPipeline(middleware=(first, second))

    def handler(event: PipelineEvent) -> None:
        calls.append(f"handler:{event.id}")

    event = PipelineEvent(timestamp=NOW)
    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish(event, EventPriority.SYSTEM)
    pipeline.run()

    assert calls == [
        f"first:before:{event.id}",
        f"second:before:{event.id}",
        f"handler:{event.id}",
        f"second:after:{event.id}",
        f"first:after:{event.id}",
    ]


def test_timing_middleware_notifies_observer() -> None:
    observed: list[str] = []

    def observer(event: DomainEvent, elapsed_seconds: float) -> None:
        assert elapsed_seconds >= 0
        observed.append(event.id)

    pipeline = EventPipeline(middleware=(TimingMiddleware(observer=observer),))

    def handler(event: PipelineEvent) -> None:
        observed.append(f"handled:{event.id}")

    event = PipelineEvent(timestamp=NOW)
    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish(event, EventPriority.SYSTEM)
    pipeline.run()

    assert observed == [f"handled:{event.id}", event.id]


def test_nested_dispatch_is_rejected() -> None:
    pipeline = EventPipeline()

    def handler(event: PipelineEvent) -> None:
        pipeline.run()

    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish(PipelineEvent(timestamp=NOW), EventPriority.SYSTEM)

    with pytest.raises(RuntimeError, match="nested dispatch"):
        pipeline.run()


def test_handler_can_publish_follow_up_event_without_nested_dispatch() -> None:
    pipeline = EventPipeline()
    handled: list[str] = []

    def handler(event: PipelineEvent) -> None:
        handled.append(event.name)
        if event.name == "first":
            pipeline.publish(PipelineEvent(timestamp=NOW, name="second"), EventPriority.SYSTEM)

    pipeline.subscribe(PipelineEvent, handler)
    pipeline.publish(PipelineEvent(timestamp=NOW, name="first"), EventPriority.SYSTEM)

    assert pipeline.run() == 2
    assert handled == ["first", "second"]


def test_replay_engine_replays_events_through_pipeline() -> None:
    pipeline = EventPipeline()
    replay = ReplayEngine(pipeline)
    handled: list[str] = []

    def handler(event: PipelineEvent) -> None:
        handled.append(event.name)

    pipeline.subscribe(PipelineEvent, handler)
    events = [
        PipelineEvent(timestamp=NOW, name="stored-1"),
        PipelineEvent(timestamp=NOW, name="stored-2"),
    ]

    assert replay.replay(events, EventPriority.MARKET) == 2
    assert handled == ["stored-1", "stored-2"]
