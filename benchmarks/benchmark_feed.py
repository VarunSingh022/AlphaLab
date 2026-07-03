"""High-performance benchmarking suite for the Feed Abstraction Layer."""

import time

from alphalab.feed import (
    FeedEngine,
    MockFeed,
    RawPayload,
)


def run_benchmark() -> None:
    state = FeedEngine.initialize("MOCK-BENCH", "MockProvider")
    feed = MockFeed()
    
    N = 100_000
    print(f"Starting Feed Layer Benchmark: Processing {N} Raw Events...")

    # 1. Setup connection and subscription
    state, _ = feed.connect(state, 1000.0)
    state, _ = feed.subscribe(state, "AAPL", "TICK", 1001.0)

    # 2. Pre-generate payloads to measure pure feed evaluation overhead
    payloads = tuple(
        RawPayload(
            payload_type="TICK",
            data={
                "symbol": "AAPL",
                "ts": str(1002.0 + i),
                "price": "150.00",
                "size": "100",
                "id": f"TRD-{i}",
            }
        )
        for i in range(N)
    )

    start = time.perf_counter()

    # 3. Sequentially publish and adapt
    for i, payload in enumerate(payloads):
        state, _ = feed.publish(state, payload, 1002.0 + float(i))

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Feed Translation & Publish Time: {duration:.4f}s")
    print(f"Messages Received: {state.statistics.messages_received}")
    print(f"Throughput: {ops_sec:.2f} events/sec")


if __name__ == "__main__":
    run_benchmark()