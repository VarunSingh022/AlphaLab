"""High-performance benchmarking suite for the functional Research Engine."""

import time

from alphalab.research import ResearchEngine, ResearchPayload, ResearchPolicy, TradePayload


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
        "BENCH-STRAT", returns, trades, {"period": 20.0}, regimes, 10_000_000.0, 252, 0.0
    )
    policy = ResearchPolicy(
        walk_forward_windows=5,
        ruin_drawdown=0.20,
        minimum_trades=50,
        maximum_trade_share=0.30,
        worst_period_return=-0.10,
        shock_return=-0.10,
        gain_multiplier=0.5,
        loss_multiplier=2.0,
    )
    states = [ResearchEngine.initialize(f"R-{i}", "BENCH-STRAT", 1000.0) for i in range(N)]

    start = time.perf_counter()

    for i in range(N):
        ResearchEngine.run_full_research(states[i], payload, policy, 1001.0 + i, seed=42)

    duration = time.perf_counter() - start
    ops_sec = N / duration

    print(f"Total Scientific Evaluations: {N}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} comprehensive strategy research runs/sec")


if __name__ == "__main__":
    run_benchmark()
