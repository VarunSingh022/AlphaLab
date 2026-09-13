"""High-performance benchmarking suite for the functional Graphical Workbench.

Measures one full UI cycle: the Workbench delegates a backtest to Strategy
Studio, renders the result tab, and the user closes it again.

The tab is closed by asking the Workbench which tab is open
(:func:`~alphalab.workbench.views.active_tab`), not by reconstructing its
identifier. Studio mints its own ``result_id`` for every run and the tab is
named after that, so ``bt-<backtest_id>`` -- what this benchmark used to
close -- names a tab that was never opened. That is why it raised
``WorkbenchValidationError: Tab 'bt-BT-0' is not open.`` on its first iteration,
identically at every tag back to v2.14.
"""

import time

from alphalab.studio import BacktestConfiguration, Project, StrategyStudioEngine
from alphalab.workbench import WorkbenchEngine, WorkbenchManager, active_tab


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
        BacktestConfiguration(f"BT-{i}", "STRAT-1", (), 0.0, 100.0, 100_000.0) for i in range(N)
    )
    metrics = {"total_return": 0.10}

    start = time.perf_counter()

    for i in range(N):
        # 1. Simulate UI routing the request to Studio, and rendering the response tab
        wb_state, studio_state = WorkbenchEngine.run_backtest(
            wb_state, studio_state, "PROJ-B", configs[i], metrics, float(1001 + i)
        )

        # 2. Simulate User closing the tab immediately to prevent infinite array growth
        rendered = active_tab(wb_state)
        if rendered is None:
            raise AssertionError("run_backtest rendered no tab")

        wb_state = WorkbenchManager.close_tab(
            wb_state,
            rendered.tab_id,
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
