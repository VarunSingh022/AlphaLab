"""Regression guard for the quadratic v2.5 removed from the replay cursor.

``ReplayState.system_events`` was a tuple, and ``step_one_event`` appended one
``ReplayAdvanced`` to it per record by rebuilding the whole tuple. A replay of N
records therefore copied O(N^2) elements -- the same defect v2.1 removed from the
risk engine, v2.2 from the OMS and v2.3 from the market and broker layers, left
behind on the one path v2.2 had just wired into execution:
:class:`~alphalab.backtesting.replay.ReplayBacktest` drives ``step_one_event``
once per record.

It went unnoticed because ``benchmarks/benchmark_replay_engine.py`` measured
``step_timestamp`` instead, with a comment saying it did so "to avoid
astronomical tuple copying" -- steering around the defect rather than measuring
it. The benchmark now measures the integrated API.

Measured before the fix: 0.0165s / 0.0513s / 0.1740s at N=2000/4000/8000
(x3.11, x3.39). After: x2.06, x2.01.

The structural assertion is the real guard; the timing one is a coarse backstop
with a wide tolerance, there to catch a return to quadratic scaling rather than
to police constant factors.
"""

import time
from dataclasses import dataclass

from alphalab.common.append_log import AppendOnlyLog
from alphalab.replay.engine import ReplayEngine
from alphalab.replay.session import ReplaySession
from alphalab.replay.state import ReplayState


@dataclass(frozen=True, slots=True)
class _Event:
    event_id: str
    timestamp: float


def _running(count: int) -> ReplayState:
    events = tuple(_Event(f"E{i}", float(i + 1)) for i in range(count))
    session = ReplaySession("COMPLEXITY", start_time=0.0, end_time=float(count + 1))
    state = ReplayEngine.initialize(session, events, 0.0)
    return ReplayEngine.start(state, 0.0)


def _drive(count: int) -> float:
    state = _running(count)
    start = time.perf_counter()
    for index in range(count):
        state = ReplayEngine.step_one_event(state, float(index + 1)).state
    return time.perf_counter() - start


# --- structural: the property the fix rests on -------------------------------


def test_replay_system_events_is_an_append_only_log() -> None:
    assert isinstance(_running(1).system_events, AppendOnlyLog)


def test_the_log_still_compares_equal_to_the_tuple_it_replaced() -> None:
    """Migrating the container must not change what the state means."""

    state = ReplayEngine.initialize(
        ReplaySession("S", start_time=0.0, end_time=2.0),
        (_Event("E0", 1.0),),
        0.0,
    )
    assert state.system_events == ()

    started = ReplayEngine.start(state, 0.0)
    assert len(started.system_events) == 1
    assert type(started.system_events[0]).__name__ == "ReplayStarted"


def test_stepping_is_invisible_to_the_state_before_it() -> None:
    """Appending must not leak a later event into an earlier state."""

    first = _running(3)
    second = ReplayEngine.step_one_event(first, 1.0).state
    third = ReplayEngine.step_one_event(second, 2.0).state

    assert len(first.system_events) == 1  # ReplayStarted only
    assert len(second.system_events) == 2
    assert len(third.system_events) == 3


def test_every_record_still_records_exactly_one_advance() -> None:
    """The fix must not drop or duplicate an event."""

    state = _running(5)
    for index in range(5):
        state = ReplayEngine.step_one_event(state, float(index + 1)).state

    advanced = [e for e in state.system_events if type(e).__name__ == "ReplayAdvanced"]
    completed = [e for e in state.system_events if type(e).__name__ == "ReplayCompleted"]
    assert len(advanced) == 5
    assert len(completed) == 1
    assert [e.timestamp for e in advanced] == [1.0, 2.0, 3.0, 4.0, 5.0]


# --- timing backstop ---------------------------------------------------------


def test_stepping_stays_linear_in_the_records_already_replayed() -> None:
    """Quadratic over a 4x workload is ~16x; linear is ~4x."""

    small = _drive(2_000)
    large = _drive(8_000)

    assert large < small * 8.0, (
        f"Replaying 8,000 records took {large:.3f}s against {small:.3f}s for 2,000; "
        "the cursor's system-event log is being rebuilt per record."
    )
