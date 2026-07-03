"""High-performance benchmarking suite for the functional Live Data framework."""

import time

from alphalab.live import (
    AssetClass,
    LiveEngine,
    Provider,
    Subscription,
    TradeTick,
)


def run_benchmark() -> None:
    state = LiveEngine.initialize("LIVE-BENCH")

    provider = Provider("BENCH-P", "BenchProvider", "VendorX", frozenset({AssetClass.EQUITY}))
    state = LiveEngine.register_provider(state, provider, 1000.0)
    state = LiveEngine.connect_provider(state, "BENCH-P", 1001.0)

    symbols = [f"SYM-{i}" for i in range(100)]
    for sym in symbols:
        sub = Subscription("BENCH-P", sym, AssetClass.EQUITY)
        state = LiveEngine.subscribe(state, sub, 1002.0)

    N = 100_000
    print(f"Starting Live Data Benchmark: Normalizing and Routing {N} Ticks...")

    # Pre-generate ticks to avoid measuring allocation overhead
    ticks = tuple(
        TradeTick(
            provider_id="BENCH-P",
            timestamp=float(2000 + i),
            symbol=symbols[i % 100],
            price=150.0 + (i % 10),
            size=100.0,
        )
        for i in range(N)
    )

    start = time.perf_counter()

    for tick in ticks:
        state = LiveEngine.process_trade(state, tick)

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Normalization & Snapshot Aggregation Time: {duration:.4f}s")
    print(f"Total Ticks Processed: {state.statistics.total_ticks_processed}")
    print(f"Throughput: {ops_sec:.2f} ticks/sec")


if __name__ == "__main__":
    run_benchmark()
