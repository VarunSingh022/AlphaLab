"""High-performance benchmarking suite for the functional Persistence Engine."""

import time
from dataclasses import dataclass
from decimal import Decimal

from alphalab.persistence import (
    MemoryStorage,
    PersistenceAdapter,
    PersistenceEngine,
)


@dataclass(frozen=True)
class BenchEvent:
    trade_id: str
    price: Decimal


def run_benchmark() -> None:
    state = PersistenceEngine.initialize("MEM-BENCH")
    store = MemoryStorage()

    N = 100_000
    print(f"Starting Persistence Engine Benchmark: Storing {N} Events...")

    # 1. Pre-generate domain events to isolate serialization & storage overhead
    domain_events = tuple(
        BenchEvent(trade_id=f"TRD-{i}", price=Decimal("150.00")) for i in range(N)
    )

    start = time.perf_counter()

    # 2. Sequentially translate, serialize, and persist
    for i, domain_event in enumerate(domain_events):
        ts = float(1000 + i)
        stored_evt = PersistenceAdapter.to_stored_event(
            event_id=f"EVT-{i}",
            timestamp=ts,
            domain_event=domain_event,
        )
        state, _ = store.append_event(state, stored_evt, ts)

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Persistence Engine Evaluation Time: {duration:.4f}s")
    print(f"Events Appended: {state.statistics.total_events_appended}")
    print(f"Total Bytes Stored: {state.statistics.bytes_stored}")
    print(f"Throughput: {ops_sec:.2f} events/sec")


if __name__ == "__main__":
    run_benchmark()
