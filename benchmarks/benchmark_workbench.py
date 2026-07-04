"""High-performance benchmarking suite for the functional Graphical Workbench."""

import time

from alphalab.studio import BacktestConfiguration, Project, StrategyStudioEngine
from alphalab.workbench import WorkbenchEngine


def run_benchmark() -> None:
    N = 100_000
    print(f"Starting AlphaLab Workbench Benchmark: Rendering & Delegating {N} interactions...")

    # Boot Studio (Backend)
    studio_state = StrategyStudioEngine.initialize("STUDIO-B", "/bench")
    project = Project("PROJ-B", "Benchmark Tracking", 1000.0)
    studio_state = StrategyStudioEngine.create_project(studio_state, project, 1000.0)

    # Boot Workbench (Frontend UI)
    wb_state = WorkbenchEngine.initialize("WB-BENCH", 1000.0)

    # Pre-generate configurations to simulate rapid user backtest dispatches
    configs = tuple(
        BacktestConfiguration(f"BT-{i}", "STRAT-1", (), 0.0, 100.0, 100_000.0)
        for i in range(N)
    )
    metrics = {"total_return": 0.10}

    start = time.perf_counter()

    for i in range(N):
        # 1. Simulate UI routing the request to Studio, and rendering the response tab
        wb_state, studio_state = WorkbenchEngine.run_backtest(
            wb_state, studio_state, "PROJ-B", configs[i], metrics, float(1001 + i)
        )
        
        # 2. Simulate User closing the tab immediately to prevent infinite array growth
        from alphalab.workbench.manager import WorkbenchManager
        wb_state = WorkbenchManager.close_tab(
            wb_state, 
            f"bt-{configs[i].backtest_id}", 
            float(1001 + i),
        )

    duration = time.perf_counter() - start
    ops_sec = N / duration
    
    print(f"Total Backtest Delegations: {studio_state.metrics.backtests_run}")
    print(f"Open Tabs in UI: {len(wb_state.tabs)}")
    print(f"Evaluation Time: {duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} UI rendering/delegation cycles/sec")

if __name__ == "__main__":
    run_benchmark()