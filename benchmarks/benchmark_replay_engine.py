"""High-performance benchmarking suite for the functional Replay Engine."""

import time
from dataclasses import dataclass

from alphalab.replay import (
    ReplayEngine,
    ReplaySession,
)


@dataclass(frozen=True, slots=True)
class BenchEvent:
    event_id: str
    timestamp: float


def run_benchmark() -> None:
    N = 1_000_000
    print(f"Starting Replay Engine Benchmark: Processing {N} Events...")

    # 1. Prepare 1,000,000 sequenced events
    events = tuple(BenchEvent(f"E{i}", float(1000 + i)) for i in range(N))

    session = ReplaySession("BENCH-SESS", start_time=1000.0, end_time=float(1000 + N + 1))

    state = ReplayEngine.initialize(session, events, 0.0)
    state = ReplayEngine.start(state, 1.0)

    start = time.perf_counter()

    # 2. Extract strictly deterministically via the chunking API to avoid
    # astronomical tuple copying on O(1M) elements in Python, testing real
    # functional throughput limits.
    res = ReplayEngine.step_timestamp(state, float(1000 + N + 1), 2.0)
    state = res.state

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Replay Evaluation Time: {duration:.4f}s")
    print(f"Events Emitted: {len(res.events)}")
    print(f"Throughput: {ops_sec:.2f} events/sec")


if __name__ == "__main__":
    run_benchmark()
