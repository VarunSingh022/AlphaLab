"""Event dispatcher logic preserving chronological order and generating telemetry."""

from dataclasses import replace
from typing import Any

from alphalab.common.ids import new_id
from alphalab.runtime.events import DispatchCompleted, DispatchFailed
from alphalab.runtime.state import RuntimeState
from alphalab.runtime.validation import validate_dispatch


class EventDispatcher:
    """Stateless router capturing dispatch metrics without modifying payloads."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def dispatch(
        state: RuntimeState, event: Any, processing_time: float, timestamp: float
    ) -> RuntimeState:
        """
        Records an event dispatch successfully processing through the pipeline.
        In the real system, the actual event is passed to Strategy, Risk, OMS, etc.
        Here we orchestrate the metrics and telemetry.
        """
        validate_dispatch(state)

        event_type = type(event).__name__
        dispatch_evt = DispatchCompleted(
            event_id=EventDispatcher._create_id(),
            timestamp=timestamp,
            event_type=event_type,
            processing_time=processing_time,
        )

        new_metrics = replace(
            state.metrics,
            events_processed=state.metrics.events_processed + 1,
            total_dispatch_latency=state.metrics.total_dispatch_latency + processing_time,
            uptime_seconds=state.metrics.uptime_seconds + processing_time,
        )

        return replace(
            state,
            metrics=new_metrics,
            events=(*state.events, dispatch_evt),
        )

    @staticmethod
    def record_failure(
        state: RuntimeState, event: Any, reason: str, timestamp: float
    ) -> RuntimeState:
        """Records an event failing during the pipeline dispatch sequence."""
        event_type = type(event).__name__
        fail_evt = DispatchFailed(
            event_id=EventDispatcher._create_id(),
            timestamp=timestamp,
            event_type=event_type,
            reason=reason,
        )

        new_metrics = replace(
            state.metrics,
            error_count=state.metrics.error_count + 1,
        )

        return replace(
            state,
            metrics=new_metrics,
            events=(*state.events, fail_evt),
        )
