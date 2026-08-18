"""Comprehensive tests for the Cluster Scheduler: aging-aware assignment, tag-based
affinity routing, and queue position lookup."""

import pytest

from alphalab.cluster_scheduler import (
    ClusterSchedulerInputError,
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
from alphalab.distributed.state import DistributedState


def _cluster(capacity: int = 1) -> DistributedState:
    state = DistributedEngine.initialize("TEST-CLUSTER")
    return DistributedEngine.register_worker(
        state,
        WorkerNode(node_id="w1", hostname="h1", status=WorkerStatus.IDLE, capacity=capacity),
        0.0,
    )


def _job(job_id: str, priority: int, created_timestamp: float, tags: str | None = None) -> Job:
    metadata = {"tags": tags} if tags else {}
    return Job(
        job_id=job_id,
        job_type=JobType.BACKTEST,
        status=JobStatus.PENDING,
        priority=priority,
        created_timestamp=created_timestamp,
        metadata=metadata,
    )


# --------------------------------------------------------------------------- #
# Aging-aware assignment
# --------------------------------------------------------------------------- #


def test_zero_aging_rate_matches_base_scheduler_exactly() -> None:
    """The honesty claim in the module docstring, verified: aging_rate=0.0 must
    be a true no-op relative to JobScheduler.assign_jobs, not just similar."""
    state = _cluster()
    state = DistributedEngine.submit_job(state, _job("low", priority=1, created_timestamp=0.0), 0.0)
    state = DistributedEngine.submit_job(
        state, _job("high", priority=10, created_timestamp=1.0), 1.0
    )

    no_aging_result = assign_jobs_with_aging(state, timestamp=1000.0, aging_rate=0.0)
    base_result = DistributedEngine.assign_jobs(state, timestamp=1000.0)

    assert set(no_aging_result.running_jobs.keys()) == set(base_result.running_jobs.keys())


def test_without_aging_higher_priority_always_wins() -> None:
    state = _cluster()
    state = DistributedEngine.submit_job(
        state, _job("old_low", priority=1, created_timestamp=0.0), 0.0
    )
    state = DistributedEngine.submit_job(
        state, _job("fresh_high", priority=10, created_timestamp=1000.0), 1000.0
    )

    result = assign_jobs_with_aging(state, timestamp=1000.0, aging_rate=0.0)
    assert "fresh_high" in result.running_jobs
    assert "old_low" not in result.running_jobs


def test_sufficient_aging_lets_old_low_priority_job_win() -> None:
    """The actual reason this function exists, verified against hand-computed
    effective priorities: old_low waits 1000s at rate 0.02 -> effective priority
    1 + 0.02*1000 = 21, beating fresh_high's nominal priority of 10."""
    state = _cluster()
    state = DistributedEngine.submit_job(
        state, _job("old_low", priority=1, created_timestamp=0.0), 0.0
    )
    state = DistributedEngine.submit_job(
        state, _job("fresh_high", priority=10, created_timestamp=1000.0), 1000.0
    )

    result = assign_jobs_with_aging(state, timestamp=1000.0, aging_rate=0.02)
    assert "old_low" in result.running_jobs
    assert "fresh_high" not in result.running_jobs


def test_aging_rejects_negative_rate() -> None:
    state = _cluster()
    with pytest.raises(ClusterSchedulerInputError):
        assign_jobs_with_aging(state, timestamp=0.0, aging_rate=-0.01)


def test_assign_jobs_with_aging_no_op_on_empty_queue() -> None:
    state = _cluster()
    result = assign_jobs_with_aging(state, timestamp=0.0, aging_rate=0.1)
    assert result is state


def test_assign_jobs_with_aging_respects_worker_capacity() -> None:
    state = _cluster(capacity=1)
    state = DistributedEngine.submit_job(state, _job("a", priority=1, created_timestamp=0.0), 0.0)
    state = DistributedEngine.submit_job(state, _job("b", priority=2, created_timestamp=1.0), 1.0)

    result = assign_jobs_with_aging(state, timestamp=10.0, aging_rate=0.0)
    assert len(result.running_jobs) == 1
    assert len(result.queued_jobs) == 1


# --------------------------------------------------------------------------- #
# Tag-based affinity routing
# --------------------------------------------------------------------------- #


def _tagged_cluster() -> DistributedState:
    state = DistributedEngine.initialize("TAG-CLUSTER")
    state = DistributedEngine.register_worker(
        state,
        WorkerNode(
            node_id="cpu-worker",
            hostname="h1",
            status=WorkerStatus.IDLE,
            capacity=1,
            metadata={"tags": "cpu"},
        ),
        0.0,
    )
    state = DistributedEngine.register_worker(
        state,
        WorkerNode(
            node_id="gpu-worker",
            hostname="h2",
            status=WorkerStatus.IDLE,
            capacity=1,
            metadata={"tags": "gpu,cpu"},
        ),
        0.0,
    )
    return state


def test_job_with_required_tag_routes_to_matching_worker() -> None:
    state = _tagged_cluster()
    state = DistributedEngine.submit_job(
        state, _job("gpu_job", priority=1, created_timestamp=0.0, tags="gpu"), 0.0
    )

    result = assign_jobs_with_affinity(state, timestamp=1.0)
    assert result.running_jobs["gpu_job"].worker_id == "gpu-worker"


def test_job_without_tags_can_use_any_worker() -> None:
    state = _tagged_cluster()
    state = DistributedEngine.submit_job(
        state, _job("any_job", priority=1, created_timestamp=0.0), 0.0
    )

    result = assign_jobs_with_affinity(state, timestamp=1.0)
    assert len(result.running_jobs) == 1


def test_job_with_unmatchable_tag_is_skipped_not_blocking() -> None:
    """The specific behavior distinguishing this from JobScheduler.assign_jobs and
    assign_jobs_with_aging: a job with no matching worker must not block jobs
    behind it that could otherwise run immediately."""
    state = _tagged_cluster()
    state = DistributedEngine.submit_job(
        state, _job("tpu_job", priority=1, created_timestamp=0.0, tags="tpu"), 0.0
    )
    state = DistributedEngine.submit_job(
        state, _job("cpu_job", priority=1, created_timestamp=1.0, tags="cpu"), 1.0
    )

    result = assign_jobs_with_affinity(state, timestamp=2.0)
    assert "cpu_job" in result.running_jobs
    assert "tpu_job" not in result.running_jobs
    assert any(j.job_id == "tpu_job" for j in result.queued_jobs)


def test_multi_tag_worker_satisfies_single_tag_requirement() -> None:
    """gpu-worker is tagged 'gpu,cpu' -- a job requiring only 'cpu' should be
    able to use it too (superset matching, not exact matching)."""
    state = DistributedEngine.initialize("SINGLE-WORKER-CLUSTER")
    state = DistributedEngine.register_worker(
        state,
        WorkerNode(
            node_id="gpu-worker",
            hostname="h1",
            status=WorkerStatus.IDLE,
            capacity=1,
            metadata={"tags": "gpu,cpu"},
        ),
        0.0,
    )
    state = DistributedEngine.submit_job(
        state, _job("cpu_job", priority=1, created_timestamp=0.0, tags="cpu"), 0.0
    )

    result = assign_jobs_with_affinity(state, timestamp=1.0)
    assert result.running_jobs["cpu_job"].worker_id == "gpu-worker"


def test_job_requiring_multiple_tags_needs_all_of_them() -> None:
    state = _tagged_cluster()
    state = DistributedEngine.submit_job(
        state, _job("multi_job", priority=1, created_timestamp=0.0, tags="gpu,cpu"), 0.0
    )

    result = assign_jobs_with_affinity(state, timestamp=1.0)
    assert result.running_jobs["multi_job"].worker_id == "gpu-worker"


def test_assign_jobs_with_affinity_no_op_on_empty_queue() -> None:
    state = _tagged_cluster()
    result = assign_jobs_with_affinity(state, timestamp=0.0)
    assert result is state


# --------------------------------------------------------------------------- #
# Queue position
# --------------------------------------------------------------------------- #


def test_queue_position_of_first_job_is_zero() -> None:
    state = _cluster()
    state = DistributedEngine.submit_job(
        state, _job("only_job", priority=1, created_timestamp=0.0), 0.0
    )
    assert queue_position(state, "only_job") == 0


def test_queue_position_reflects_priority_ordering() -> None:
    """JobQueue.submit already sorts by priority -- a low-priority job submitted
    first should still end up behind a high-priority job submitted after it."""
    state = _cluster()
    state = DistributedEngine.submit_job(state, _job("low", priority=1, created_timestamp=0.0), 0.0)
    state = DistributedEngine.submit_job(
        state, _job("high", priority=10, created_timestamp=1.0), 1.0
    )

    assert queue_position(state, "high") == 0
    assert queue_position(state, "low") == 1


def test_queue_position_returns_none_for_unknown_job() -> None:
    state = _cluster()
    assert queue_position(state, "never-submitted") is None


def test_queue_position_returns_none_once_assigned() -> None:
    state = _cluster()
    state = DistributedEngine.submit_job(
        state, _job("job1", priority=1, created_timestamp=0.0), 0.0
    )
    assigned = DistributedEngine.assign_jobs(state, timestamp=1.0)
    assert queue_position(assigned, "job1") is None
