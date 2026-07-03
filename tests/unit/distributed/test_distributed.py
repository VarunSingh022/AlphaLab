"""Comprehensive tests validating strict job scheduling, distribution and worker lifecycles."""

import pytest

from alphalab.distributed import (
    DistributedAdapter,
    DistributedEngine,
    DistributedState,
    DistributedValidationError,
    InvalidJobStateError,
    Job,
    JobStatus,
    JobType,
    WorkerNode,
    WorkerStatus,
    active_workers,
    cluster_statistics,
    completed_jobs,
    failed_jobs,
    queue_length,
    running_jobs,
    worker_utilization,
)


@pytest.fixture
def base_state() -> DistributedState:
    return DistributedEngine.initialize("CLUSTER-01")


@pytest.fixture
def standard_worker() -> WorkerNode:
    return WorkerNode(
        node_id="W-1",
        hostname="worker-1.alphalab.local",
        status=WorkerStatus.IDLE,
        capacity=2,
    )


@pytest.fixture
def standard_job() -> Job:
    return Job(
        job_id="J-1",
        job_type=JobType.BACKTEST,
        status=JobStatus.PENDING,
        priority=10,
        created_timestamp=1000.0,
    )


# --- ENGINE & REGISTRY TESTS ---


def test_initialization(base_state: DistributedState) -> None:
    assert base_state.cluster_id == "CLUSTER-01"
    assert queue_length(base_state) == 0

    with pytest.raises(ValueError):
        DistributedEngine.initialize("")


def test_register_worker(base_state: DistributedState, standard_worker: WorkerNode) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    assert len(active_workers(s1)) == 1
    assert any(type(e).__name__ == "WorkerRegistered" for e in s1.events)


def test_register_invalid_worker(base_state: DistributedState, standard_worker: WorkerNode) -> None:
    bad_worker = WorkerNode("W-2", "host", WorkerStatus.IDLE, 0)
    with pytest.raises(DistributedValidationError, match="positive"):
        DistributedEngine.register_worker(base_state, bad_worker, 1000.0)


def test_register_duplicate_worker(
    base_state: DistributedState, standard_worker: WorkerNode
) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    with pytest.raises(DistributedValidationError, match="already registered"):
        DistributedEngine.register_worker(s1, standard_worker, 1001.0)


def test_remove_worker(base_state: DistributedState, standard_worker: WorkerNode) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    s2 = DistributedEngine.remove_worker(s1, "W-1", 1001.0)
    assert len(active_workers(s2)) == 0
    assert any(type(e).__name__ == "WorkerRemoved" for e in s2.events)


def test_update_worker_status(base_state: DistributedState, standard_worker: WorkerNode) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    s2 = DistributedEngine.set_worker_status(s1, "W-1", WorkerStatus.OFFLINE)
    assert s2.workers["W-1"].status == WorkerStatus.OFFLINE


# --- QUEUE TESTS ---


def test_submit_job(base_state: DistributedState, standard_job: Job) -> None:
    s1 = DistributedEngine.submit_job(base_state, standard_job, 1001.0)
    assert queue_length(s1) == 1
    assert cluster_statistics(s1).total_jobs_submitted == 1
    assert any(type(e).__name__ == "JobSubmitted" for e in s1.events)


def test_submit_duplicate_job(base_state: DistributedState, standard_job: Job) -> None:
    s1 = DistributedEngine.submit_job(base_state, standard_job, 1001.0)
    with pytest.raises(DistributedValidationError, match="Duplicate"):
        DistributedEngine.submit_job(s1, standard_job, 1002.0)


def test_cancel_job(base_state: DistributedState, standard_job: Job) -> None:
    s1 = DistributedEngine.submit_job(base_state, standard_job, 1001.0)
    s2 = DistributedEngine.cancel_job(s1, "J-1", 1002.0)

    assert queue_length(s2) == 0
    assert cluster_statistics(s2).total_jobs_cancelled == 1
    assert len(failed_jobs(s2)) == 1
    assert failed_jobs(s2)[0].status == JobStatus.CANCELLED


def test_priority_queue_ordering(base_state: DistributedState) -> None:
    j1 = Job("J-1", JobType.BACKTEST, JobStatus.PENDING, 5, 1000.0)
    j2 = Job("J-2", JobType.BACKTEST, JobStatus.PENDING, 20, 1001.0)
    j3 = Job("J-3", JobType.BACKTEST, JobStatus.PENDING, 10, 1002.0)

    s1 = DistributedEngine.submit_job(base_state, j1, 1000.0)
    s2 = DistributedEngine.submit_job(s1, j2, 1001.0)
    s3 = DistributedEngine.submit_job(s2, j3, 1002.0)

    # Priority order should be J-2 (20), J-3 (10), J-1 (5)
    assert s3.queued_jobs[0].job_id == "J-2"
    assert s3.queued_jobs[1].job_id == "J-3"
    assert s3.queued_jobs[2].job_id == "J-1"


def test_fifo_tiebreaker(base_state: DistributedState) -> None:
    j1 = Job("J-1", JobType.BACKTEST, JobStatus.PENDING, 10, 1000.0)
    j2 = Job("J-2", JobType.BACKTEST, JobStatus.PENDING, 10, 1001.0)

    s1 = DistributedEngine.submit_job(base_state, j2, 1001.0)  # Submitting J-2 first
    s2 = DistributedEngine.submit_job(s1, j1, 1000.0)  # But J-1 was created earlier

    # Due to creation timestamp tie-breaker, J-1 comes first
    assert s2.queued_jobs[0].job_id == "J-1"
    assert s2.queued_jobs[1].job_id == "J-2"


# --- SCHEDULER & COORDINATOR TESTS ---


def test_assign_jobs_success(
    base_state: DistributedState,
    standard_worker: WorkerNode,
    standard_job: Job,
) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    s2 = DistributedEngine.submit_job(s1, standard_job, 1001.0)

    s3 = DistributedEngine.assign_jobs(s2, 1002.0)

    assert queue_length(s3) == 0
    assert len(running_jobs(s3)) == 1
    assert running_jobs(s3)[0].status == JobStatus.ASSIGNED
    assert running_jobs(s3)[0].worker_id == "W-1"

    assert worker_utilization(s3)["W-1"] == 0.5  # 1 out of 2 capacity used
    assert any(type(e).__name__ == "JobAssigned" for e in s3.events)


def test_assign_jobs_exceeds_capacity(
    base_state: DistributedState, standard_worker: WorkerNode
) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    # Submit 3 jobs. Capacity is 2.
    for i in range(3):
        job = Job(f"J-{i}", JobType.BACKTEST, JobStatus.PENDING, 10, 1000.0)
        s1 = DistributedEngine.submit_job(s1, job, 1000.0)

    s2 = DistributedEngine.assign_jobs(s1, 1001.0)

    assert queue_length(s2) == 1
    assert len(running_jobs(s2)) == 2
    assert worker_utilization(s2)["W-1"] == 1.0


def test_job_execution_lifecycle(
    base_state: DistributedState, standard_worker: WorkerNode, standard_job: Job
) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    s2 = DistributedEngine.submit_job(s1, standard_job, 1001.0)

    # Assign
    s3 = DistributedEngine.assign_jobs(s2, 1002.0)

    # Start
    s4 = DistributedEngine.start_job(s3, "J-1", 1003.0)
    assert running_jobs(s4)[0].status == JobStatus.RUNNING
    assert any(type(e).__name__ == "JobStarted" for e in s4.events)

    # Complete
    s5 = DistributedEngine.complete_job(s4, "J-1", 1004.0)
    assert len(running_jobs(s5)) == 0
    assert len(completed_jobs(s5)) == 1
    assert completed_jobs(s5)[0].status == JobStatus.COMPLETED
    assert cluster_statistics(s5).total_jobs_completed == 1
    assert worker_utilization(s5)["W-1"] == 0.0  # Capacity freed
    assert active_workers(s5)[0].completed_jobs == 1


def test_job_fail_lifecycle(
    base_state: DistributedState, standard_worker: WorkerNode, standard_job: Job
) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    s2 = DistributedEngine.submit_job(s1, standard_job, 1001.0)

    s3 = DistributedEngine.assign_jobs(s2, 1002.0)
    s4 = DistributedEngine.start_job(s3, "J-1", 1003.0)

    s5 = DistributedEngine.fail_job(s4, "J-1", "OOM", 1004.0)
    assert len(running_jobs(s5)) == 0
    assert len(failed_jobs(s5)) == 1
    assert failed_jobs(s5)[0].status == JobStatus.FAILED
    assert cluster_statistics(s5).total_jobs_failed == 1
    assert worker_utilization(s5)["W-1"] == 0.0  # Capacity safely freed


def test_invalid_job_transitions(
    base_state: DistributedState, standard_worker: WorkerNode, standard_job: Job
) -> None:
    s1 = DistributedEngine.register_worker(base_state, standard_worker, 1000.0)
    s2 = DistributedEngine.submit_job(s1, standard_job, 1001.0)
    s3 = DistributedEngine.assign_jobs(s2, 1002.0)

    # Cannot complete a job that hasn't started (is only ASSIGNED)
    with pytest.raises(InvalidJobStateError):
        DistributedEngine.complete_job(s3, "J-1", 1003.0)


# --- ADAPTER & IMMUTABILITY TESTS ---


def test_adapter_optimization_job() -> None:
    job = DistributedAdapter.create_optimization_job("OPT-1", {"x": 5}, 10, 1000.0)
    assert job.job_id == "OPT-1"
    assert job.job_type == JobType.OPTIMIZATION
    assert job.payload["x"] == 5


def test_adapter_replay_job() -> None:
    job = DistributedAdapter.create_replay_job("REP-1", 100.0, 200.0, 5, 1000.0)
    assert job.job_type == JobType.REPLAY
    assert job.payload["start_time"] == 100.0


def test_adapter_analytics_job() -> None:
    job = DistributedAdapter.create_analytics_job("AN-1", ("SNAP-1",), 5, 1000.0)
    assert job.job_type == JobType.ANALYTICS
    assert "SNAP-1" in job.payload["snapshot_ids"]


def test_immutability(base_state: DistributedState, standard_job: Job) -> None:
    s1 = DistributedEngine.submit_job(base_state, standard_job, 1000.0)

    assert base_state is not s1
    assert queue_length(base_state) == 0
    assert queue_length(s1) == 1


# --- EXTRA COVERAGE (Scale testing logic checks) ---


def test_greedy_scheduler_multiple_workers(base_state: DistributedState) -> None:
    w1 = WorkerNode("W-1", "host1", WorkerStatus.IDLE, 1)
    w2 = WorkerNode("W-2", "host2", WorkerStatus.IDLE, 2)

    s1 = DistributedEngine.register_worker(base_state, w1, 1000.0)
    s2 = DistributedEngine.register_worker(s1, w2, 1001.0)

    for i in range(3):
        job = Job(f"J-{i}", JobType.BACKTEST, JobStatus.PENDING, 10, 1000.0)
        s2 = DistributedEngine.submit_job(s2, job, 1000.0)

    s3 = DistributedEngine.assign_jobs(s2, 1002.0)

    assert queue_length(s3) == 0
    assert len(running_jobs(s3)) == 3
    # W-2 should take 2 jobs, W-1 should take 1
    assert len(s3.workers["W-1"].running_jobs) == 1
    assert len(s3.workers["W-2"].running_jobs) == 2
