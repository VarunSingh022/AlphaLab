"""High-performance benchmark suite for the Cluster Scheduler."""

import time

from alphalab.cluster_scheduler import (
    assign_jobs_with_affinity,
    assign_jobs_with_aging,
    queue_position,
)
from alphalab.distributed import (
    DistributedEngine,
    Job,
    JobStatus,
    JobType,
    WorkerNode,
    WorkerStatus,
)


def run_benchmark() -> None:
    state = DistributedEngine.initialize("BENCH-CLUSTER")
    for i in range(10):
        tags = "gpu" if i % 2 == 0 else "cpu"
        state = DistributedEngine.register_worker(
            state,
            WorkerNode(
                node_id=f"worker-{i}",
                hostname=f"h{i}",
                status=WorkerStatus.IDLE,
                capacity=4,
                metadata={"tags": tags},
            ),
            0.0,
        )

    for i in range(100):
        tags = "gpu" if i % 3 == 0 else "cpu"
        job = Job(
            job_id=f"job-{i}",
            job_type=JobType.BACKTEST,
            status=JobStatus.PENDING,
            priority=i % 5,
            created_timestamp=float(i),
            metadata={"tags": tags},
        )
        state = DistributedEngine.submit_job(state, job, float(i))

    N = 1_000
    print(
        f"Starting Cluster Scheduler Benchmark: {N} iterations per operation on a 100-job queue..."
    )

    start = time.perf_counter()
    for _ in range(N):
        assign_jobs_with_aging(state, timestamp=200.0, aging_rate=0.01)
    duration = time.perf_counter() - start
    print(f"  assign_jobs_with_aging   : {duration:.4f}s, {N / duration:.2f} ops/sec")

    start = time.perf_counter()
    for _ in range(N):
        assign_jobs_with_affinity(state, timestamp=200.0)
    duration = time.perf_counter() - start
    print(f"  assign_jobs_with_affinity: {duration:.4f}s, {N / duration:.2f} ops/sec")

    N_LOOKUP = 100_000
    start = time.perf_counter()
    for _ in range(N_LOOKUP):
        queue_position(state, "job-50")
    duration = time.perf_counter() - start
    print(f"  queue_position           : {duration:.4f}s, {N_LOOKUP / duration:.2f} ops/sec")


if __name__ == "__main__":
    run_benchmark()
