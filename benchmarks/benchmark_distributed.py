"""High-performance benchmarking suite for the functional Distributed Scheduler."""

import time

from alphalab.distributed import (
    DistributedEngine,
    Job,
    JobStatus,
    JobType,
    WorkerNode,
    WorkerStatus,
)


def run_benchmark() -> None:
    state = DistributedEngine.initialize("CLUSTER-BENCH")

    W = 100
    N = 10_000
    print("Starting Distributed Framework Benchmark...")
    print(f"Registering {W} Workers with capacity 100 each...")

    # 1. Setup Workers (100 workers * 100 capacity = 10,000 cluster capacity)
    for w in range(W):
        worker = WorkerNode(f"W-{w}", f"host-{w}", WorkerStatus.IDLE, 100)
        state = DistributedEngine.register_worker(state, worker, 1000.0)

    # 2. Queue Generation Benchmark
    print(f"Submitting {N} Jobs to Priority Queue...")
    start_q = time.perf_counter()

    for i in range(N):
        job = Job(
            f"J-{i}",
            JobType.BACKTEST,
            JobStatus.PENDING,
            priority=10,
            created_timestamp=1000.0 + i,
        )
        state = DistributedEngine.submit_job(state, job, 1000.0 + i)

    duration_q = time.perf_counter() - start_q

    # 3. Scheduling Benchmark
    print("Executing deterministic scheduler assignment...")
    start_s = time.perf_counter()

    state = DistributedEngine.assign_jobs(state, 2000.0)

    duration_s = time.perf_counter() - start_s

    q_sec = N / duration_q
    s_sec = N / duration_s

    print(f"Submission Time: {duration_q:.4f}s")
    print(f"Submission Throughput: {q_sec:.2f} jobs/sec")

    print(f"Scheduling Time: {duration_s:.4f}s")
    print(f"Scheduling Throughput: {s_sec:.2f} jobs/sec")
    print(f"Total Assigned: {len(state.running_jobs)}")


if __name__ == "__main__":
    run_benchmark()
