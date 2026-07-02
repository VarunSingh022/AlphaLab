"""Comprehensive tests validating strict replay lifecycle rules and determinism."""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from alphalab.replay import (
    InvalidReplayStateError,
    ReplayEngine,
    ReplaySession,
    ReplayStatus,
    ReplayValidationError,
    completion_ratio,
    current_throughput,
    current_timestamp,
    elapsed_real_time,
    processed_events,
    remaining_events,
)


@dataclass(frozen=True, slots=True)
class MockHistoricalEvent:
    event_id: str
    timestamp: float


@pytest.fixture
def default_session() -> ReplaySession:
    return ReplaySession("SESS-1", 1000.0, 2000.0, speed_multiplier=1.0)


@pytest.fixture
def base_events() -> tuple[MockHistoricalEvent, ...]:
    return (
        MockHistoricalEvent("E1", 1005.0),
        MockHistoricalEvent("E2", 1010.0),
        MockHistoricalEvent("E3", 1015.0),
    )


# --- VALIDATION TESTS ---


def test_validation_empty_events(default_session: ReplaySession) -> None:
    with pytest.raises(ReplayValidationError, match="Empty datasets"):
        ReplayEngine.initialize(default_session, (), 100.0)


def test_validation_unordered_events(default_session: ReplaySession) -> None:
    events = (MockHistoricalEvent("E1", 1005.0), MockHistoricalEvent("E2", 1000.0))
    with pytest.raises(ReplayValidationError, match="chronologically ordered"):
        ReplayEngine.initialize(default_session, events, 100.0)


def test_validation_duplicate_events(default_session: ReplaySession) -> None:
    events = (MockHistoricalEvent("E1", 1005.0), MockHistoricalEvent("E1", 1010.0))
    with pytest.raises(ReplayValidationError, match="Duplicate event_id"):
        ReplayEngine.initialize(default_session, events, 100.0)


def test_validation_negative_speed() -> None:
    events = (MockHistoricalEvent("E1", 1005.0),)
    sess = ReplaySession("S1", 1000.0, 2000.0, -1.0)
    with pytest.raises(ReplayValidationError, match="negative"):
        ReplayEngine.initialize(sess, events, 100.0)


def test_validation_invalid_range() -> None:
    events = (MockHistoricalEvent("E1", 1005.0),)
    sess = ReplaySession("S1", 2000.0, 1000.0)
    with pytest.raises(ReplayValidationError, match="end_time precedes start_time"):
        ReplayEngine.initialize(sess, events, 100.0)


# --- LIFECYCLE TESTS ---


def test_initialization(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    assert state.status == ReplayStatus.READY
    assert current_timestamp(state) == 1000.0
    assert remaining_events(state) == 3


def test_start_ready_to_running(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    assert state.status == ReplayStatus.RUNNING
    assert any(type(e).__name__ == "ReplayStarted" for e in state.system_events)


def test_start_invalid_state(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    with pytest.raises(InvalidReplayStateError, match="Cannot start from RUNNING"):
        ReplayEngine.start(state, 52.0)


def test_pause_running_to_paused(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.pause(state, 52.0)
    assert state.status == ReplayStatus.PAUSED


def test_pause_invalid_state(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    with pytest.raises(InvalidReplayStateError):
        ReplayEngine.pause(state, 51.0)


def test_resume_paused_to_running(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.pause(state, 52.0)
    state = ReplayEngine.resume(state, 53.0)
    assert state.status == ReplayStatus.RUNNING


def test_resume_invalid_state(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    with pytest.raises(InvalidReplayStateError):
        ReplayEngine.resume(state, 52.0)


def test_stop_running_to_stopped(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.stop(state, 52.0)
    assert state.status == ReplayStatus.STOPPED


def test_stop_paused_to_stopped(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.pause(state, 52.0)
    state = ReplayEngine.stop(state, 53.0)
    assert state.status == ReplayStatus.STOPPED


def test_stop_already_stopped(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.stop(state, 52.0)
    with pytest.raises(InvalidReplayStateError):
        ReplayEngine.stop(state, 53.0)


# --- ENGINE STEPPING & SEQUENCE TESTS ---


def test_step_one_running(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    res = ReplayEngine.step_one_event(state, 52.0)

    assert res.event is not None
    assert res.event.event_id == "E1"
    assert current_timestamp(res.state) == 1005.0
    assert processed_events(res.state) == 1


def test_step_one_not_running(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    res = ReplayEngine.step_one_event(state, 51.0)
    assert res.event is None


def test_step_one_completion(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.step_one_event(state, 52.0).state
    state = ReplayEngine.step_one_event(state, 53.0).state
    state = ReplayEngine.step_one_event(state, 54.0).state

    # After final pop, status becomes COMPLETED
    assert state.status == ReplayStatus.COMPLETED

    # Attempting beyond completion returns nothing safely
    res = ReplayEngine.step_one_event(state, 55.0)
    assert res.event is None
    assert res.state.status == ReplayStatus.COMPLETED


def test_step_timestamp_exact(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    res = ReplayEngine.step_timestamp(state, 1010.0, 52.0)

    assert len(res.events) == 2
    assert res.events[0].event_id == "E1"
    assert res.events[1].event_id == "E2"
    assert current_timestamp(res.state) == 1010.0


def test_step_timestamp_partial(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    res = ReplayEngine.step_timestamp(state, 1009.0, 52.0)

    assert len(res.events) == 1
    assert current_timestamp(res.state) == 1009.0


def test_step_timestamp_beyond_end(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    res = ReplayEngine.step_timestamp(state, 5000.0, 52.0)

    assert len(res.events) == 3
    assert res.state.status == ReplayStatus.COMPLETED


def test_step_timestamp_not_running(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    res = ReplayEngine.step_timestamp(state, 5000.0, 51.0)
    assert len(res.events) == 0


def test_reset_engine(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.step_one_event(state, 52.0).state

    state = ReplayEngine.reset(state, 53.0)
    assert state.status == ReplayStatus.READY
    assert processed_events(state) == 0
    assert current_timestamp(state) == 1000.0


# --- METRICS & VIEWS TESTS ---


def test_metrics_initial(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    assert processed_events(state) == 0
    assert remaining_events(state) == 3
    assert completion_ratio(state) == Decimal("0.0000")


def test_metrics_progress(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.step_one_event(state, 52.0).state
    assert completion_ratio(state) == Decimal("0.3333")
    assert remaining_events(state) == 2


def test_metrics_completion(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.step_timestamp(state, 5000.0, 52.0).state
    assert completion_ratio(state) == Decimal("1.0000")
    assert remaining_events(state) == 0


def test_views_current_timestamp(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    assert current_timestamp(state) == 1000.0


def test_views_processed_events(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 51.0)
    state = ReplayEngine.step_one_event(state, 52.0).state
    assert processed_events(state) == 1


def test_metrics_throughput(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state = ReplayEngine.initialize(default_session, base_events, 50.0)
    state = ReplayEngine.start(state, 50.0)

    # Fake processing 3 events over 2 seconds of wall-clock time
    state = ReplayEngine.step_timestamp(state, 5000.0, 52.0).state

    assert elapsed_real_time(state) == 2.0
    assert current_throughput(state) == 1.5  # 3 events / 2 seconds


def test_immutability(
    default_session: ReplaySession,
    base_events: tuple[MockHistoricalEvent, ...],
) -> None:
    state1 = ReplayEngine.initialize(default_session, base_events, 50.0)
    state2 = ReplayEngine.start(state1, 51.0)

    assert state1 is not state2
    assert state1.status == ReplayStatus.READY
    assert state2.status == ReplayStatus.RUNNING
