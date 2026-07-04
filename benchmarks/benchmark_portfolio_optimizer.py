"""High-performance benchmarking suite for the functional Portfolio Engine."""

import time

from alphalab.portfolio_optimizer import (
    Portfolio,
    PortfolioEngine,
    WeightConstraints,
)


def run_benchmark() -> None:
    N = 10_000
    print(f"Starting Portfolio Engine Benchmark: Optimizing & Constraining {N} allocations...")

    state = PortfolioEngine.initialize("PORT-BENCH")
    port = Portfolio("BENCH-P", "Bench Portfolio", "USD", 1000.0)
    state = PortfolioEngine.create(state, port, 1000.0)

    symbols = tuple(f"SYM-{i}" for i in range(100))
    constraints = WeightConstraints(max_position_weight=0.015, cash_reserve_weight=0.05)

    start = time.perf_counter()

    for i in range(N):
        # Deterministically optimize 100 assets equal weight and run iterative clipping/projection
        state = PortfolioEngine.optimize(
            state, "BENCH-P", "EQUAL_WEIGHT", symbols, {}, float(1001 + i)
        )
        state = PortfolioEngine.apply_constraints(state, "BENCH-P", constraints, float(1001 + i))

    duration = time.perf_counter() - start
    ops_sec = N / duration

    print(f"Total Portfolio Restructures Evaluated: {N}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} constraint optimizations/sec")


if __name__ == "__main__":
    run_benchmark()
