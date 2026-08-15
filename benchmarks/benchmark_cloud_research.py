"""High-performance benchmark suite for the Cloud Research Engine."""

import time
from concurrent.futures import ProcessPoolExecutor

from alphalab.cloud_research import initialize_cluster, run_cluster_cycle, submit_parameter_sweep
from alphalab.cloud_research.task import run_task
from alphalab.distributed import JobType


def run_benchmark() -> None:
    print("Starting Cloud Research Engine Benchmark...")

    N_RUN_TASK = 2_000
    payload = {
        "task_path": "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
        "kwargs": {"x": [[1.0], [2.0], [3.0]], "y": [2.0, 4.0, 6.0]},
    }
    start = time.perf_counter()
    for _ in range(N_RUN_TASK):
        run_task(payload)
    duration = time.perf_counter() - start
    print(f"  run_task (in-process, no pool): {duration:.4f}s, {N_RUN_TASK / duration:.2f} ops/sec")

    state = initialize_cluster(
        "bench-cluster", num_workers=4, capacity_per_worker=4, timestamp=1000.0
    )
    state, job_ids = submit_parameter_sweep(
        state,
        JobType.OPTIMIZATION,
        "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
        param_grid={"l2_penalty": tuple(float(i) for i in range(20))},
        base_kwargs={"x": [[1.0], [2.0], [3.0], [4.0]], "y": [2.0, 4.0, 6.0, 8.0]},
        priority=1,
        timestamp=1001.0,
    )
    print(f"  Submitted {len(job_ids)} real jobs across a 20-point parameter sweep.")

    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=4) as executor:
        result = run_cluster_cycle(state, executor, timestamp=1002.0)
    duration = time.perf_counter() - start
    completed = len(result.distributed.completed_jobs)
    print(
        f"  run_cluster_cycle (4 real worker processes, {completed} jobs): "
        f"{duration:.4f}s, {completed / duration:.2f} jobs/sec"
    )


if __name__ == "__main__":
    run_benchmark()
