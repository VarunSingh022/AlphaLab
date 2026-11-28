"""A cluster cycle given a registry runs a job's task by name and resolves nothing (v4.3).

ADR-0072 section 6 and plan D-54-6: the registry the trial ledger's executors
take replaces dotted-path resolution for ``cloud_research`` (GAP-05, the v4.3
review's SEC-001), and ``resolve_task`` stays, superseded, until 5.0. The cycle
also takes the assignment policy its workers follow, so
``alphalab.cluster_scheduler``'s policies are on the path that runs jobs
(GAP-31). These tests hold:

* a registered task runs, in a process pool and in a thread pool;
* a name the registry does not hold -- a dotted path to a real, importable
  function included -- fails its job before anything is imported or submitted;
* a sweep through the registry is counted exactly as the dotted-path sweep is;
* a task that raises, arguments that are not a mapping, and a task a process
  pool cannot pickle each fail their job, saying why, and the cycle runs on;
* the policy given decides which job the workers take;
* outcomes are applied in submission order, whatever finishes first (DET-001).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from typing import Any

import pytest

from alphalab.cloud_research import (
    CloudResearchState,
    collect_sweep,
    initialize_cluster,
    run_cluster_cycle,
    submit_parameter_sweep,
    submit_research_job,
)
from alphalab.cloud_research.example_tasks import always_fails, train_and_evaluate_linear_model
from alphalab.cluster_scheduler import assign_jobs_with_aging
from alphalab.common.registry import RunnerRegistry
from alphalab.distributed import DistributedEngine, Job, JobStatus, JobType
from alphalab.distributed.events import JobFailed

NEVER_IMPORTED = "tests.unit.common._never_imported"
LINEAR = "alphalab.cloud_research.example_tasks.train_and_evaluate_linear_model"
DATA = {"x": [[1.0], [2.0], [3.0], [4.0]], "y": [2.0, 4.0, 6.0, 8.0]}


def add(x: float, y: float) -> float:
    """A task at a module's top level, so a process pool pickles it by reference."""

    return x + y


def nap(seconds: float) -> float:
    time.sleep(seconds)
    return seconds


REGISTRY = (
    RunnerRegistry()
    .register("add", add)
    .register("fit", train_and_evaluate_linear_model)
    .register("fails", always_fails)
    .register("nap", nap)
)


class _Counting(ThreadPoolExecutor):
    """A thread pool that counts what it was given."""

    submitted = 0

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        type(self).submitted += 1
        return super().submit(fn, *args, **kwargs)


def _cluster(capacity: int = 4) -> CloudResearchState:
    return initialize_cluster("lab", num_workers=1, capacity_per_worker=capacity, timestamp=0.0)


def _reasons(state: CloudResearchState) -> dict[str, str]:
    return {
        event.job_id: event.reason
        for event in state.distributed.events
        if isinstance(event, JobFailed)
    }


@pytest.mark.parametrize("pool", [ProcessPoolExecutor, ThreadPoolExecutor])
def test_a_registered_task_runs_by_name(pool: type[ProcessPoolExecutor]) -> None:
    state, job_id = submit_research_job(
        _cluster(), JobType.OPTIMIZATION, "add", {"x": 1.5, "y": 2.0}, 1, 1.0
    )

    with pool(max_workers=2) as executor:
        done = run_cluster_cycle(state, executor, 2.0, registry=REGISTRY)

    assert done.results == {job_id: 3.5}
    assert job_id in done.distributed.completed_jobs


@pytest.mark.parametrize("task", [f"{NEVER_IMPORTED}.anything", LINEAR, "subtract"])
def test_a_name_the_registry_does_not_hold_fails_before_anything_is_imported(task: str) -> None:
    """A dotted path to a real, importable task is a name like any other: not registered."""

    state, job_id = submit_research_job(_cluster(), JobType.OPTIMIZATION, task, DATA, 1, 1.0)
    _Counting.submitted = 0

    with _Counting(max_workers=1) as executor:
        done = run_cluster_cycle(state, executor, 2.0, registry=REGISTRY)

    assert _Counting.submitted == 0, "nothing reaches the executor"
    assert NEVER_IMPORTED not in sys.modules
    assert done.results == {}
    assert "never resolved to code (BDY-028)" in _reasons(done)[job_id]
    assert job_id in done.distributed.failed_jobs


def test_a_sweep_through_the_registry_is_counted_as_the_dotted_sweep_is() -> None:
    """The cloud sweep runs through the runner registry (ADR-0072 section 10)."""

    grid = {"l2_penalty": (0.0, 1.0, 10.0)}

    def swept(task: str, registry: RunnerRegistry | None) -> Any:
        state, job_ids = submit_parameter_sweep(
            _cluster(), JobType.OPTIMIZATION, task, grid, DATA, priority=1, timestamp=1.0
        )
        with ProcessPoolExecutor(max_workers=2) as executor:
            done = run_cluster_cycle(state, executor, 2.0, registry=registry)
        return collect_sweep(done, grid, job_ids, "r_squared", lambda result: result["r_squared"])

    registered = swept("fit", REGISTRY)
    dotted = swept(LINEAR, None)

    assert registered.trials == dotted.trials == 3
    assert registered.scores == dotted.scores
    assert registered.best == dotted.best == "l2_penalty=0.0"


def test_a_task_that_raises_fails_its_job_saying_why() -> None:
    state, job_id = submit_research_job(
        _cluster(), JobType.OPTIMIZATION, "fails", {"message": "the task's own"}, 1, 1.0
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        done = run_cluster_cycle(state, executor, 2.0, registry=REGISTRY)

    assert _reasons(done) == {job_id: "ValueError: the task's own"}


def test_arguments_that_are_not_a_mapping_and_a_missing_task_fail_their_jobs() -> None:
    state = _cluster()
    for job_id, payload in (
        ("listed", {"task_path": "add", "kwargs": [1.0, 2.0]}),
        ("unnamed", {"kwargs": {"x": 1.0, "y": 2.0}}),
    ):
        job = Job(job_id, JobType.OPTIMIZATION, JobStatus.PENDING, 1, 1.0, payload=payload)
        state = CloudResearchState(DistributedEngine.submit_job(state.distributed, job, 1.0))

    with ThreadPoolExecutor(max_workers=2) as executor:
        done = run_cluster_cycle(state, executor, 2.0, registry=REGISTRY)

    assert _reasons(done) == {
        "listed": "payload's 'kwargs' must be a mapping.",
        "unnamed": "payload is missing a string 'task_path'.",
    }


def test_a_task_a_process_pool_cannot_pickle_fails_its_job_and_the_pool_runs_on() -> None:
    offset = 3.0
    registry = REGISTRY.register("local", lambda x: x + offset)
    state, local = submit_research_job(_cluster(), JobType.OPTIMIZATION, "local", {"x": 1}, 1, 1.0)
    state, plain = submit_research_job(state, JobType.OPTIMIZATION, "add", {"x": 1, "y": 2}, 1, 1.0)

    with ProcessPoolExecutor(max_workers=2) as executor:
        done = run_cluster_cycle(state, executor, 2.0, registry=registry)
        done, after = submit_research_job(
            done, JobType.OPTIMIZATION, "add", {"x": 7, "y": 2}, 1, 3.0
        )
        done = run_cluster_cycle(done, executor, 4.0, registry=registry)

    assert "pickle" in _reasons(done)[local].lower()
    assert done.results == {plain: 3, after: 9}


def test_the_cycle_follows_the_assignment_policy_it_is_given() -> None:
    """One worker slot: priority takes the newer, aging the one that waited longest."""

    state = _cluster(capacity=1)
    state, waited = submit_research_job(
        state, JobType.OPTIMIZATION, "add", {"x": 1, "y": 0}, 1, 0.0
    )
    state, urgent = submit_research_job(
        state, JobType.OPTIMIZATION, "add", {"x": 2, "y": 0}, 5, 90.0
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        by_priority = run_cluster_cycle(state, executor, 100.0, registry=REGISTRY)
        by_age = run_cluster_cycle(
            state,
            executor,
            100.0,
            registry=REGISTRY,
            assign=partial(assign_jobs_with_aging, aging_rate=1.0),
        )

    assert list(by_priority.results) == [urgent]
    assert list(by_age.results) == [waited]


def test_outcomes_through_a_registry_are_applied_in_submission_order() -> None:
    """DET-001 holds on the registry path: the first job submitted finishes last."""

    state = initialize_cluster("C", num_workers=3, capacity_per_worker=1, timestamp=0.0)
    submitted = []
    for index, seconds in enumerate((0.30, 0.15, 0.0)):
        state, job_id = submit_research_job(
            state, JobType.PLUGIN_TASK, "nap", {"seconds": seconds}, 10 - index, 1.0
        )
        submitted.append(job_id)

    with ThreadPoolExecutor(max_workers=3) as executor:
        done = run_cluster_cycle(state, executor, 2.0, registry=REGISTRY)

    assert list(done.results) == submitted
