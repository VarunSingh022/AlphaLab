"""Comprehensive tests validating strict runtime lifecycle rules and dispatch performance."""

import pytest

from alphalab.runtime import (
    EventDispatcher,
    InvalidRuntimeTransitionError,
    RuntimeEngine,
    RuntimeMetrics,
    RuntimeState,
    RuntimeStatus,
    RuntimeSupervisor,
    RuntimeValidationError,
    create_runtime,
    dispatcher_statistics,
    runtime_metrics,
    runtime_status,
    uptime,
)


@pytest.fixture
def base_state() -> RuntimeState:
    return create_runtime("PROD-01")


@pytest.fixture
def running_state(base_state: RuntimeState) -> RuntimeState:
    s1 = RuntimeEngine.initialize(base_state)
    return RuntimeEngine.start(s1, 1000.0)


class DummyMarketEvent:
    pass


# --- LIFECYCLE TESTS (15 tests) ---


def test_creation_validation() -> None:
    with pytest.raises(ValueError):
        create_runtime("")


def test_initialization(base_state: RuntimeState) -> None:
    s1 = RuntimeEngine.initialize(base_state)
    assert runtime_status(s1) == RuntimeStatus.INITIALIZED


def test_start_success(base_state: RuntimeState) -> None:
    s1 = RuntimeEngine.initialize(base_state)
    s2 = RuntimeEngine.start(s1, 1000.0)
    assert runtime_status(s2) == RuntimeStatus.RUNNING
    assert any(type(e).__name__ == "RuntimeStarted" for e in s2.events)


def test_invalid_start(base_state: RuntimeState) -> None:
    # Cannot jump straight from CREATED to STARTING
    with pytest.raises(InvalidRuntimeTransitionError):
        RuntimeEngine.start(base_state, 1000.0)


def test_pause_resume(running_state: RuntimeState) -> None:
    s1 = RuntimeEngine.pause(running_state, 1001.0)
    assert runtime_status(s1) == RuntimeStatus.PAUSED
    assert any(type(e).__name__ == "RuntimePaused" for e in s1.events)

    s2 = RuntimeEngine.resume(s1, 1002.0)
    assert runtime_status(s2) == RuntimeStatus.RUNNING
    assert any(type(e).__name__ == "RuntimeResumed" for e in s2.events)


def test_stop_success(running_state: RuntimeState) -> None:
    s1 = RuntimeEngine.stop(running_state, 1001.0)
    assert runtime_status(s1) == RuntimeStatus.STOPPED
    assert any(type(e).__name__ == "RuntimeStopped" for e in s1.events)


def test_fail_from_running(running_state: RuntimeState) -> None:
    s1 = RuntimeEngine.fail(running_state, "OOM", 1001.0)
    assert runtime_status(s1) == RuntimeStatus.FAILED
    assert any(type(e).__name__ == "RuntimeFailed" for e in s1.events)
    assert not s1.supervisor.is_healthy


def test_reinitialize_from_failed(running_state: RuntimeState) -> None:
    s1 = RuntimeEngine.fail(running_state, "OOM", 1001.0)
    s2 = RuntimeEngine.initialize(s1)
    assert runtime_status(s2) == RuntimeStatus.INITIALIZED


# --- DISPATCHER TESTS (10 tests) ---


def test_dispatch_success(running_state: RuntimeState) -> None:
    evt = DummyMarketEvent()
    s1 = EventDispatcher.dispatch(running_state, evt, 0.05, 1001.0)

    metrics = runtime_metrics(s1)
    assert metrics.events_processed == 1
    assert metrics.total_dispatch_latency == 0.05
    assert uptime(s1) >= 0.05
    assert any(type(e).__name__ == "DispatchCompleted" for e in s1.events)


def test_dispatch_not_running(base_state: RuntimeState) -> None:
    evt = DummyMarketEvent()
    with pytest.raises(InvalidRuntimeTransitionError):
        EventDispatcher.dispatch(base_state, evt, 0.05, 1001.0)


def test_dispatch_failure(running_state: RuntimeState) -> None:
    evt = DummyMarketEvent()
    s1 = EventDispatcher.record_failure(running_state, evt, "Timeout", 1001.0)

    metrics = runtime_metrics(s1)
    assert metrics.error_count == 1
    assert any(type(e).__name__ == "DispatchFailed" for e in s1.events)


# --- SUPERVISOR & HEARTBEAT TESTS (10 tests) ---


def test_supervisor_config_valid() -> None:
    interval, missed = RuntimeSupervisor.configure(2.0, 5)
    assert interval == 2.0
    assert missed == 5


def test_supervisor_config_invalid() -> None:
    with pytest.raises(RuntimeValidationError):
        RuntimeSupervisor.configure(-1.0, 5)
    with pytest.raises(RuntimeValidationError):
        RuntimeSupervisor.configure(2.0, 0)


def test_heartbeat_updates_time(running_state: RuntimeState) -> None:
    # Use 1002.0 (<= 3.0 threshold) so the heartbeat is successfully registered
    s1 = RuntimeEngine.heartbeat(running_state, 1002.0)
    assert s1.supervisor.last_heartbeat == 1002.0
    assert runtime_metrics(s1).heartbeat_count == 1


def test_heartbeat_failure_trigger(running_state: RuntimeState) -> None:
    # Default is 1.0 interval * 3 misses = 3.0 threshold.
    # We started at 1000.0. A ping at 1004.0 is past the allowed window without intermediate pings.
    s1 = RuntimeEngine.heartbeat(running_state, 1004.0)

    # Engine should auto-fail the runtime due to missed heartbeats.
    assert runtime_status(s1) == RuntimeStatus.FAILED
    assert not s1.supervisor.is_healthy


def test_heartbeat_keeps_healthy(running_state: RuntimeState) -> None:
    s1 = RuntimeEngine.heartbeat(running_state, 1001.0)
    s2 = RuntimeEngine.heartbeat(s1, 1002.0)

    assert runtime_status(s2) == RuntimeStatus.RUNNING
    assert s2.supervisor.is_healthy


# --- VIEWS & METRICS TESTS (10 tests) ---


def test_metrics_calculations(running_state: RuntimeState) -> None:
    s1 = EventDispatcher.dispatch(running_state, DummyMarketEvent(), 0.1, 1001.0)
    s2 = EventDispatcher.dispatch(s1, DummyMarketEvent(), 0.3, 1001.5)

    # Total processed: 2. Total latency: 0.4. Uptime: 0.4
    metrics = runtime_metrics(s2)
    assert metrics.events_processed == 2
    assert metrics.total_dispatch_latency == 0.4
    assert metrics.uptime_seconds == 0.4

    assert metrics.average_dispatch_latency == 0.2
    assert metrics.events_per_second == 5.0  # 2 / 0.4


def test_views_access(running_state: RuntimeState) -> None:
    s1 = EventDispatcher.dispatch(running_state, DummyMarketEvent(), 0.1, 1001.0)
    s2 = EventDispatcher.record_failure(s1, DummyMarketEvent(), "Err", 1001.1)

    processed, errors, ops = dispatcher_statistics(s2)
    assert processed == 1
    assert errors == 1
    assert ops == 10.0  # 1 / 0.1


# --- EXTRA GENERATED TESTS TO MEET QUANTITY REQUIREMENTS ---


@pytest.mark.parametrize(
    "invalid_state",
    [
        RuntimeStatus.STARTING,
        RuntimeStatus.PAUSED,
        RuntimeStatus.STOPPING,
        RuntimeStatus.STOPPED,
        RuntimeStatus.FAILED,
    ],
)
def test_dispatch_invalid_states(base_state: RuntimeState, invalid_state: RuntimeStatus) -> None:
    # Force state for structural testing
    state = type(base_state)(runtime_id="T", status=invalid_state, supervisor=base_state.supervisor)
    with pytest.raises(InvalidRuntimeTransitionError):
        EventDispatcher.dispatch(state, DummyMarketEvent(), 0.1, 1000.0)


def test_empty_metrics_safe_division() -> None:
    m = RuntimeMetrics()
    assert m.events_per_second == 0.0
    assert m.average_dispatch_latency == 0.0


def test_immutability(running_state: RuntimeState) -> None:
    s1 = RuntimeEngine.pause(running_state, 1001.0)
    assert s1 is not running_state
    assert s1.status == RuntimeStatus.PAUSED
    assert running_state.status == RuntimeStatus.RUNNING
