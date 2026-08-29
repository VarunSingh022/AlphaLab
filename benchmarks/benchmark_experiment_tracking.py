"""High-performance benchmark suite for Experiment Tracking."""

import time

from alphalab.experiment_tracking import (
    ExperimentTracker,
    best_run,
    complete_run,
    log_metric,
    new_version,
    record_experiment,
    start_run,
)
from alphalab.studio import StrategyStudioEngine
from alphalab.studio.project import Project


def run_benchmark() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "bench", parameters={"lr": 0.1}, timestamp=0.0)

    N_LOG = 10_000
    print(f"Starting Experiment Tracking Benchmark: {N_LOG} metric logs on one run...")
    start = time.perf_counter()
    for i in range(N_LOG):
        tracker = log_metric(tracker, run_id, "loss", 1.0 / (i + 1))
    duration = time.perf_counter() - start
    print(f"  log_metric (growing history): {duration:.4f}s, {N_LOG / duration:.2f} ops/sec")

    tracker = complete_run(tracker, run_id, timestamp=1.0)

    N_VERSIONS = 500
    start = time.perf_counter()
    current_id = run_id
    for i in range(N_VERSIONS):
        tracker, current_id = new_version(
            tracker, current_id, parameters={"lr": 0.1 / (i + 2)}, timestamp=float(i)
        )
        tracker = log_metric(tracker, current_id, "loss", 1.0 / (i + 1))
        tracker = complete_run(tracker, current_id, timestamp=float(i) + 0.5)
    duration = time.perf_counter() - start
    print(f"  new_version (chain)  : {duration:.4f}s, {N_VERSIONS / duration:.2f} ops/sec")

    N_QUERY = 10_000
    start = time.perf_counter()
    for _ in range(N_QUERY):
        best_run(tracker, "loss", higher_is_better=False)
    duration = time.perf_counter() - start
    print(
        f"  best_run ({len(tracker.runs)} runs): {duration:.4f}s, {N_QUERY / duration:.2f} ops/sec"
    )

    studio_state = StrategyStudioEngine.initialize("BENCH-STUDIO")
    project = Project(project_id="P1", name="Bench Project", created_at=0.0)
    studio_state = StrategyStudioEngine.create_project(studio_state, project, 0.0)

    N_RECORD = 5_000
    start = time.perf_counter()
    for i in range(N_RECORD):
        studio_state, _ = record_experiment(
            studio_state,
            "P1",
            parameters={"x": float(i)},
            target_metric=float(i) / 100,
            timestamp=float(i),
        )
    duration = time.perf_counter() - start
    print(
        f"  record_experiment (studio bridge)  : {duration:.4f}s, {N_RECORD / duration:.2f} ops/sec"
    )


if __name__ == "__main__":
    run_benchmark()
