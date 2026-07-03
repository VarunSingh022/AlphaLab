"""High-performance benchmarking suite for the functional Runtime Dispatcher."""

import time

from alphalab.runtime import (
    EventDispatcher,
    RuntimeEngine,
    create_runtime,
)


class DummyMarketEvent:
    pass


def run_benchmark() -> None:
    base = create_runtime("BENCH-PROD")
    init = RuntimeEngine.initialize(base)
    state = RuntimeEngine.start(init, 1000.0)

    N = 100_000
    print(f"Starting Live Runtime Benchmark: Dispatching {N} Events...")

    event = DummyMarketEvent()

    start = time.perf_counter()

    # In purely functional implementations, we reassign the mutated state reference.
    # We pass a synthetic tiny processing time simulating deep engine responses.
    for i in range(N):
        state = EventDispatcher.dispatch(state, event, 0.0001, 1000.0 + (i * 0.001))

    duration = time.perf_counter() - start

    ops_sec = N / duration
    avg_lat = state.metrics.average_dispatch_latency

    print(f"Runtime Total Dispatch Wall-Clock Time: {duration:.4f}s")
    print(f"Events Handled: {state.metrics.events_processed}")
    print(f"Internal Avg Dispatch Latency metric: {avg_lat:.6f}s")
    print(f"Throughput: {ops_sec:.2f} events/sec")


if __name__ == "__main__":
    run_benchmark()
