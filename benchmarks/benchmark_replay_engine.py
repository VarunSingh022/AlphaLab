"""High-performance benchmarking suite for the functional Replay Engine.

Two APIs, and until v2.5 this file measured the wrong one.

``step_timestamp`` drains a batch in a single call and appends one system event
for the batch. ``step_one_event`` advances the cursor by exactly one record and
appends one ``ReplayAdvanced`` per record -- and it is the one
:class:`~alphalab.backtesting.replay.ReplayBacktest` calls, so it is the API the
integrated replay path actually runs on.

Before v2.5, ``ReplayState.system_events`` was a tuple rebuilt on every append,
which made ``step_one_event`` quadratic in the records already read. This
benchmark's own comment said it used the batch API "to avoid astronomical tuple
copying" -- it was steering around the defect rather than measuring it, so the
slow path was the wired path and was unmeasured. The log is now an
``AppendOnlyLog`` and the per-record sweep below is what proves it.
"""

import time
from dataclasses import dataclass

from alphalab.replay import ReplayEngine, ReplaySession


@dataclass(frozen=True, slots=True)
class BenchEvent:
    event_id: str
    timestamp: float


def _prepare(count: int) -> tuple[object, ...]:
    return tuple(BenchEvent(f"E{i}", float(1000 + i)) for i in range(count))


def _running_state(count: int):  # type: ignore[no-untyped-def]
    session = ReplaySession("BENCH-SESS", start_time=1000.0, end_time=float(1000 + count + 1))
    state = ReplayEngine.initialize(session, _prepare(count), 0.0)  # type: ignore[arg-type]
    return ReplayEngine.start(state, 1.0)


def _step_one_event(count: int) -> float:
    """The integrated path: one call per record, as ReplayBacktest drives it."""

    state = _running_state(count)
    start = time.perf_counter()
    for index in range(count):
        state = ReplayEngine.step_one_event(state, float(index + 1)).state
    return time.perf_counter() - start


def run_benchmark() -> None:
    print("Starting Replay Engine Benchmark.")

    print("  step_one_event -- the API the integrated replay path uses:")
    previous: float | None = None
    for count in (2_000, 4_000, 8_000):
        duration = _step_one_event(count)
        growth = f"  x{duration / previous:.2f}" if previous is not None else ""
        print(f"    N={count:>6}: {duration:7.4f}s, {count / duration:>12,.0f} events/sec{growth}")
        previous = duration
    print("    (linear is ~x2.00 per doubling; quadratic was ~x3.20 before v2.5)")

    batch = 1_000_000
    state = _running_state(batch)
    start = time.perf_counter()
    result = ReplayEngine.step_timestamp(state, float(1000 + batch + 1), 2.0)
    duration = time.perf_counter() - start
    print(f"  step_timestamp -- batch drain of {batch:,} events:")
    print(
        f"    {duration:.4f}s, {batch / duration:,.0f} events/sec, {len(result.events):,} emitted"
    )


if __name__ == "__main__":
    run_benchmark()
