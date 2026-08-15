"""Comprehensive tests for the Cloud Research Engine: task resolution, real
process-pool execution (success and failure), cluster orchestration, and
parameter sweeps."""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from alphalab.cloud_research import (
    CloudResearchInputError,
    initialize_cluster,
    resolve_task,
    run_cluster_cycle,
    run_task,
    submit_parameter_sweep,
    submit_research_job,
)
from alphalab.distributed import JobStatus, JobType

# --------------------------------------------------------------------------- #
# Task resolution
# --------------------------------------------------------------------------- #


def test_resolve_task_finds_real_function() -> None:
    function = resolve_task("alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model")
    assert callable(function)


def test_resolve_task_rejects_path_without_dot() -> None:
    with pytest.raises(CloudResearchInputError):
        resolve_task("not_a_dotted_path")


def test_resolve_task_rejects_unimportable_module() -> None:
    with pytest.raises(CloudResearchInputError):
        resolve_task("alphalab.nonexistent_module.some_function")


def test_resolve_task_rejects_missing_attribute() -> None:
    with pytest.raises(CloudResearchInputError):
        resolve_task("alphalab.cloud_research.example_tasks.does_not_exist")


def test_run_task_returns_success_result() -> None:
    payload = {
        "task_path": "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
        "kwargs": {"x": [[1.0], [2.0], [3.0]], "y": [2.0, 4.0, 6.0]},
    }
    outcome = run_task(payload)
    assert outcome["success"] is True
    assert outcome["result"]["r_squared"] == pytest.approx(1.0)


def test_run_task_never_raises_on_bad_task_path() -> None:
    """run_task must capture failures as data, since an exception has to cross a
    real process boundary -- it never propagates as a raised exception."""
    outcome = run_task({"task_path": "bad.path.nonexistent", "kwargs": {}})
    assert outcome["success"] is False
    assert "error" in outcome


def test_run_task_captures_exception_from_inside_the_task() -> None:
    outcome = run_task(
        {
            "task_path": "alphalab.cloud_research.example_tasks.always_fails",
            "kwargs": {"message": "boom"},
        }
    )
    assert outcome["success"] is False
    assert "ValueError" in outcome["error"]
    assert "boom" in outcome["error"]


def test_run_task_rejects_missing_task_path() -> None:
    outcome = run_task({"kwargs": {}})
    assert outcome["success"] is False


# --------------------------------------------------------------------------- #
# Cluster initialization
# --------------------------------------------------------------------------- #


def test_initialize_cluster_creates_requested_worker_count() -> None:
    state = initialize_cluster("cluster-1", num_workers=3, capacity_per_worker=2, timestamp=1000.0)
    assert len(state.distributed.workers) == 3


def test_initialize_cluster_rejects_non_positive_num_workers() -> None:
    with pytest.raises(CloudResearchInputError):
        initialize_cluster("cluster-1", num_workers=0, capacity_per_worker=1, timestamp=0.0)


def test_initialize_cluster_rejects_non_positive_capacity() -> None:
    with pytest.raises(CloudResearchInputError):
        initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=0, timestamp=0.0)


def test_cloud_research_state_is_immutable() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    with pytest.raises(FrozenInstanceError):
        state.results = {}  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Job submission
# --------------------------------------------------------------------------- #


def test_submit_research_job_adds_to_queue() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    new_state, job_id = submit_research_job(
        state, JobType.OPTIMIZATION, "some.task.path", {"a": 1}, priority=1, timestamp=1.0
    )
    assert len(new_state.distributed.queued_jobs) == 1
    assert new_state.distributed.queued_jobs[0].job_id == job_id
    assert new_state.distributed.queued_jobs[0].status is JobStatus.PENDING


def test_submit_research_job_stores_task_path_and_kwargs_in_payload() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    new_state, _job_id = submit_research_job(
        state, JobType.OPTIMIZATION, "some.task.path", {"a": 1}, priority=1, timestamp=1.0
    )
    job = new_state.distributed.queued_jobs[0]
    assert job.payload["task_path"] == "some.task.path"
    assert job.payload["kwargs"] == {"a": 1}


# --------------------------------------------------------------------------- #
# Real cluster execution: genuine ProcessPoolExecutor, not a mock
# --------------------------------------------------------------------------- #


def test_run_cluster_cycle_with_no_queued_jobs_is_a_no_op() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    with ProcessPoolExecutor(max_workers=1) as executor:
        result = run_cluster_cycle(state, executor, timestamp=1.0)
    assert result.distributed.completed_jobs == {}
    assert result.results == {}


def test_run_cluster_cycle_executes_a_real_job_in_a_real_worker_process() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    state, job_id = submit_research_job(
        state,
        JobType.OPTIMIZATION,
        "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
        {"x": [[1.0], [2.0], [3.0]], "y": [2.0, 4.0, 6.0]},
        priority=1,
        timestamp=1.0,
    )

    with ProcessPoolExecutor(max_workers=1) as executor:
        result = run_cluster_cycle(state, executor, timestamp=2.0)

    assert job_id in result.distributed.completed_jobs
    assert job_id in result.results
    assert result.results[job_id]["r_squared"] == pytest.approx(1.0)
    assert len(result.distributed.queued_jobs) == 0


def test_run_cluster_cycle_handles_a_real_job_failure() -> None:
    """The failure path exercised against a job that genuinely raises in a real
    worker process, not a simulated failure."""
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    state, job_id = submit_research_job(
        state,
        JobType.PLUGIN_TASK,
        "alphalab.cloud_research.example_tasks.always_fails",
        {"message": "expected test failure"},
        priority=1,
        timestamp=1.0,
    )

    with ProcessPoolExecutor(max_workers=1) as executor:
        result = run_cluster_cycle(state, executor, timestamp=2.0)

    assert job_id in result.distributed.failed_jobs
    assert job_id not in result.results
    failed_job = result.distributed.failed_jobs[job_id]
    assert failed_job.status is JobStatus.FAILED


def test_run_cluster_cycle_frees_worker_capacity_after_completion() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    state, _ = submit_research_job(
        state,
        JobType.OPTIMIZATION,
        "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
        {"x": [[1.0], [2.0]], "y": [2.0, 4.0]},
        priority=1,
        timestamp=1.0,
    )

    with ProcessPoolExecutor(max_workers=1) as executor:
        result = run_cluster_cycle(state, executor, timestamp=2.0)

    worker = next(iter(result.distributed.workers.values()))
    assert worker.running_jobs == ()
    assert worker.completed_jobs == 1


def test_run_cluster_cycle_respects_worker_capacity_limits() -> None:
    """3 jobs, 1 worker with capacity 1 -- only one job should be assigned and
    executed this cycle; the rest remain queued."""
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    for i in range(3):
        state, _ = submit_research_job(
            state,
            JobType.OPTIMIZATION,
            "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
            {"x": [[1.0], [2.0]], "y": [2.0, 4.0]},
            priority=1,
            timestamp=float(i),
        )

    with ProcessPoolExecutor(max_workers=1) as executor:
        result = run_cluster_cycle(state, executor, timestamp=10.0)

    assert len(result.distributed.completed_jobs) == 1
    assert len(result.distributed.queued_jobs) == 2


# --------------------------------------------------------------------------- #
# Parameter sweeps
# --------------------------------------------------------------------------- #


def test_submit_parameter_sweep_creates_one_job_per_combination() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=4, timestamp=0.0)
    new_state, job_ids = submit_parameter_sweep(
        state,
        JobType.OPTIMIZATION,
        "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
        param_grid={"l2_penalty": (0.0, 1.0, 10.0)},
        base_kwargs={"x": [[1.0], [2.0]], "y": [2.0, 4.0]},
        priority=1,
        timestamp=1.0,
    )
    assert len(job_ids) == 3
    assert len(new_state.distributed.queued_jobs) == 3


def test_submit_parameter_sweep_cartesian_product_across_two_dimensions() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=10, timestamp=0.0)
    _new_state, job_ids = submit_parameter_sweep(
        state,
        JobType.OPTIMIZATION,
        "some.task",
        param_grid={"a": (1, 2), "b": (10, 20, 30)},
        base_kwargs={},
        priority=1,
        timestamp=1.0,
    )
    assert len(job_ids) == 2 * 3


def test_submit_parameter_sweep_merges_base_kwargs_with_grid_values() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=4, timestamp=0.0)
    new_state, _job_ids = submit_parameter_sweep(
        state,
        JobType.OPTIMIZATION,
        "some.task",
        param_grid={"l2_penalty": (0.0,)},
        base_kwargs={"x": [1, 2, 3]},
        priority=1,
        timestamp=1.0,
    )
    job = new_state.distributed.queued_jobs[0]
    assert job.payload["kwargs"]["x"] == [1, 2, 3]
    assert job.payload["kwargs"]["l2_penalty"] == 0.0


def test_submit_parameter_sweep_rejects_empty_param_grid() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    with pytest.raises(CloudResearchInputError):
        submit_parameter_sweep(state, JobType.OPTIMIZATION, "x", {}, {}, priority=1, timestamp=1.0)


def test_submit_parameter_sweep_rejects_empty_value_sequence() -> None:
    state = initialize_cluster("cluster-1", num_workers=1, capacity_per_worker=1, timestamp=0.0)
    with pytest.raises(CloudResearchInputError):
        submit_parameter_sweep(
            state, JobType.OPTIMIZATION, "x", {"a": ()}, {}, priority=1, timestamp=1.0
        )


def test_full_sweep_executes_and_produces_distinct_real_results() -> None:
    """End-to-end: submit a real sweep, run it through a real process pool, and
    confirm the results genuinely differ in the expected direction (higher ridge
    penalty degrades in-sample R^2 for a perfectly-fit line)."""
    state = initialize_cluster("cluster-1", num_workers=2, capacity_per_worker=4, timestamp=0.0)
    state, job_ids = submit_parameter_sweep(
        state,
        JobType.OPTIMIZATION,
        "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model",
        param_grid={"l2_penalty": (0.0, 10.0)},
        base_kwargs={"x": [[1.0], [2.0], [3.0], [4.0]], "y": [2.0, 4.0, 6.0, 8.0]},
        priority=1,
        timestamp=1.0,
    )

    with ProcessPoolExecutor(max_workers=2) as executor:
        result = run_cluster_cycle(state, executor, timestamp=2.0)

    assert len(result.distributed.completed_jobs) == 2
    r_squared_values = {result.results[job_id]["r_squared"] for job_id in job_ids}
    assert len(r_squared_values) == 2  # genuinely different results, not duplicated
    assert max(r_squared_values) == pytest.approx(1.0)  # the unregularized fit is exact
