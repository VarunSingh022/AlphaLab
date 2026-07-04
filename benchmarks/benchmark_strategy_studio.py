"""High-performance benchmarking suite for the functional Strategy Studio."""

import time

from alphalab.studio import (
    Project,
    StrategyDefinition,
    StrategyStudioEngine,
    build_backtest_config,
)


def run_benchmark() -> None:
    N = 10_000
    print(f"Starting Strategy Studio Benchmark: Tracking {N} backtest executions...")

    state = StrategyStudioEngine.initialize("STUDIO-BENCH", "/bench")
    project = Project("BENCH-PROJ", "Benchmark Tracking", 1000.0)
    strategy = StrategyDefinition("STRAT-1", "Alpha", "v1", "Me", "Desc", {"p": 1.0})
    
    state = StrategyStudioEngine.create_project(state, project, 1000.0)
    state = StrategyStudioEngine.register_strategy(state, "BENCH-PROJ", strategy, 1000.0)

    # Pre-generate configurations
    configs = tuple(
        build_backtest_config(f"BT-{i}", "STRAT-1", ("D1",), 0.0, 100.0, 100_000.0)
        for i in range(N)
    )
    
    metrics = {"total_return": 0.10, "sharpe": 1.2, "max_drawdown": 0.05}

    start = time.perf_counter()

    for i in range(N):
        state = StrategyStudioEngine.run_backtest(
            state, "BENCH-PROJ", configs[i], metrics, float(1001 + i)
        )

    duration = time.perf_counter() - start
    ops_sec = N / duration
    
    print(f"Total Backtest Trackings Recorded: {state.metrics.backtests_run}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} backtests tracked/sec")

if __name__ == "__main__":
    run_benchmark()