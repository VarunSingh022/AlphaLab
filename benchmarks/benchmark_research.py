"""High-performance benchmarking suite for the functional Research Engine."""

import time

from alphalab.research import ResearchEngine, ResearchPayload, TradePayload


def run_benchmark() -> None:
    N = 1000
    print(f"Starting Research Engine Benchmark: Processing {N} scientific evaluations...")

    # Synthesize standard deterministic payload (simulating 1 year of daily returns)
    returns = (0.005, -0.002, 0.01, -0.005) * 63
    regimes = ("BULL", "BEAR", "BULL", "SIDEWAYS") * 63
    trades = tuple(
        TradePayload(f"T{i}", "AAPL", 150.0, 155.0, 10.0, 50.0, 86400.0) for i in range(100)
    )

    payload = ResearchPayload(
        "BENCH-STRAT", returns, trades, {"period": 20.0}, regimes, 10_000_000.0
    )
    states = [ResearchEngine.initialize(f"R-{i}", "BENCH-STRAT", 1000.0) for i in range(N)]

    start = time.perf_counter()

    for i in range(N):
        ResearchEngine.run_full_research(states[i], payload, 1001.0 + i)

    duration = time.perf_counter() - start
    ops_sec = N / duration

    print(f"Total Scientific Evaluations: {N}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} comprehensive strategy research runs/sec")


if __name__ == "__main__":
    run_benchmark()
