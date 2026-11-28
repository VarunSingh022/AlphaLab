"""Real cluster orchestration: closes the loop the underlying distributed package
leaves open.

`alphalab.distributed` is a complete, correct job/worker lifecycle state machine --
but nothing in it ever actually executes a job's payload, and `Job` has nowhere to
store a result once one exists. `run_cluster_cycle` is the piece that was missing:
it assigns queued jobs to idle workers using the real
`alphalab.distributed.scheduler.JobScheduler`, genuinely executes them in a real
`concurrent.futures.ProcessPoolExecutor` (separate OS processes, real parallelism,
not a simulation), and applies completion/failure based on the real outcome.

Note on determinism: which task's process happens to finish first is governed by
OS-level scheduling across independent processes and is not itself deterministic
run to run. This does not violate this project's deterministic-computation
principle -- every individual task's result is exactly reproducible given the same
inputs, which is what determinism means for the computation itself. Only the wall-
clock interleaving of otherwise-independent parallel work is not, which is an
inherent, honest property of real parallel execution, not a defect.

Running through a registry (v4.3, ADR-0072, GAP-05 and GAP-31): given a
:class:`~alphalab.common.registry.RunnerRegistry`, a cycle looks each job's task
up by name in it -- the registry the trial ledger's executors take -- and never
resolves a path, so a job naming anything unregistered fails before anything is
imported. ``assign`` chooses which queued jobs the idle workers take: the
cluster's own scheduler, or a policy of :mod:`alphalab.cluster_scheduler`. The
ledger's :class:`~alphalab.lifecycle.experiments.ClusterExecutor` runs an
experiment's trials as registered tasks on this cycle, so a sweep over backtests
is counted and recorded trial by trial. Without a registry a job's task is a
dotted path, resolved as before: superseded, and kept until 5.0 (D-54-6).

Job identity: a job's ``job_id`` is derived, not drawn (DET-007) -- ``uuid5`` over
the cluster, how many jobs it has taken, and the job's type, task, priority and
instant -- as a tracker run's ``run_id`` is. Until v4.3 it came from the ambient
identifier stream, so a submission inside a seeded scope consumed one of that
scope's identifiers. The queue's own events are stamped in the caller's scope,
as every engine's are.
"""

import json
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field, replace
from typing import Any, Final
from uuid import UUID, uuid5

from alphalab.cloud_research.exceptions import CloudResearchInputError
from alphalab.cloud_research.task import run_task
from alphalab.common.exceptions import AlphaLabRegistryError
from alphalab.common.registry import RunnerRegistry
from alphalab.distributed.engine import DistributedEngine
from alphalab.distributed.job import Job, JobStatus, JobType
from alphalab.distributed.node import WorkerNode, WorkerStatus
from alphalab.distributed.state import DistributedState

#: Which queued jobs a cluster's idle workers take: a function of the cluster and
#: the instant, returning the cluster with those jobs assigned. The cluster's own
#: is :meth:`~alphalab.distributed.engine.DistributedEngine.assign_jobs`;
#: :mod:`alphalab.cluster_scheduler` adds aging and tag affinity.
type AssignmentPolicy = Callable[[DistributedState, float], DistributedState]


@dataclass(frozen=True, slots=True)
class CloudResearchState:
    """Wraps the real DistributedState with actual job results.

    Attributes:
        distributed: The real, unmodified alphalab.distributed state machine.
        results: The real return value of every completed job, keyed by job_id --
            distributed.Job has no field for this, since that package assumes
            execution and result storage happen entirely outside it.
    """

    distributed: DistributedState
    results: Mapping[str, Any] = field(default_factory=dict)


def initialize_cluster(
    cluster_id: str, num_workers: int, capacity_per_worker: int, timestamp: float
) -> CloudResearchState:
    """Creates a cluster with num_workers local workers, each able to run
    capacity_per_worker jobs concurrently.

    Raises:
        CloudResearchInputError: If num_workers or capacity_per_worker are not
            positive.
    """
    if num_workers <= 0:
        raise CloudResearchInputError(f"num_workers must be positive, got {num_workers}.")
    if capacity_per_worker <= 0:
        raise CloudResearchInputError(
            f"capacity_per_worker must be positive, got {capacity_per_worker}."
        )

    state = DistributedEngine.initialize(cluster_id)
    for i in range(num_workers):
        worker = WorkerNode(
            node_id=f"{cluster_id}-worker-{i}",
            hostname=f"local-{i}",
            status=WorkerStatus.IDLE,
            capacity=capacity_per_worker,
        )
        state = DistributedEngine.register_worker(state, worker, timestamp)

    return CloudResearchState(distributed=state)


#: The namespace every job identifier is derived under: ``uuid5(NAMESPACE_DNS,
#: "research-job.alphalab.dev")``, written out as a literal so that it is a fixed
#: fact of the scheme. Any reader can recompute it.
_JOB_NAMESPACE: Final = UUID("5ed6c3bb-73d8-5ed8-a909-8328bd167500")

#: The scheme tag, and the first line of every job key.
_JOB_KEY_SCHEME: Final = "alphalab.research_job.v1"


def _job_id(
    state: CloudResearchState, job_type: JobType, task_path: str, priority: int, timestamp: float
) -> str:
    """The identifier of the job ``submit_research_job`` is about to queue (DET-007).

    ``uuid5`` over a canonical key, in a fixed field order: the scheme tag, the
    cluster, how many jobs it has taken, then the job's type, task, priority
    and instant. The job's arguments are not part of it: they may be any
    objects, with no canonical text here. The cluster's count of submissions
    only grows, so it keeps two jobs submitted alike apart. Nothing is drawn
    from the identifier stream.
    """

    distributed = state.distributed
    key = "\n".join(
        (
            _JOB_KEY_SCHEME,
            f"cluster_id={json.dumps(distributed.cluster_id, ensure_ascii=False)}",
            f"sequence={distributed.statistics.total_jobs_submitted}",
            f"job_type={job_type.name}",
            f"task_path={json.dumps(task_path, ensure_ascii=False)}",
            f"priority={priority}",
            f"timestamp={float(timestamp)!r}",
        )
    )
    return str(uuid5(_JOB_NAMESPACE, key))


def submit_research_job(
    state: CloudResearchState,
    job_type: JobType,
    task_path: str,
    kwargs: Mapping[str, Any],
    priority: int,
    timestamp: float,
) -> tuple[CloudResearchState, str]:
    """Submits one job for a task: a registered name, or a dotted path.

    A cycle run with a :class:`~alphalab.common.registry.RunnerRegistry` looks
    ``task_path`` up in it by name and resolves nothing (v4.3); one run without
    resolves it as a dotted path, superseded and kept until 5.0. Either way
    ``task_path`` and ``kwargs`` select the code the worker runs and what it is
    called with: neither may come from untrusted input (ledger BDY-028,
    :mod:`alphalab.cloud_research.task`).

    Returns the updated state and the new job's id.
    """
    job_id = _job_id(state, job_type, task_path, priority, timestamp)
    job = Job(
        job_id=job_id,
        job_type=job_type,
        status=JobStatus.PENDING,
        priority=priority,
        created_timestamp=timestamp,
        payload={"task_path": task_path, "kwargs": dict(kwargs)},
    )
    new_distributed = DistributedEngine.submit_job(state.distributed, job, timestamp)
    return replace(state, distributed=new_distributed), job_id


def _run_registered(task: Callable[..., Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Calls a registered task as :func:`~alphalab.cloud_research.task.run_task` calls a path."""

    try:
        return {"success": True, "result": task(**kwargs)}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _registered(
    registry: RunnerRegistry, payload: Mapping[str, Any]
) -> tuple[Callable[..., Any], Mapping[str, Any]] | str:
    """The task a job names, and its arguments; or why the job fails, before anything runs."""

    name = payload.get("task_path")
    if not isinstance(name, str):
        return "payload is missing a string 'task_path'."
    try:
        task = registry.runner(name)
    except AlphaLabRegistryError as exc:
        return f"{type(exc).__name__}: {exc}"
    kwargs = payload.get("kwargs", {})
    if not isinstance(kwargs, Mapping):
        return "payload's 'kwargs' must be a mapping."
    return task, kwargs


def run_cluster_cycle(
    state: CloudResearchState,
    executor: Executor,
    timestamp: float,
    *,
    registry: RunnerRegistry | None = None,
    assign: AssignmentPolicy | None = None,
) -> CloudResearchState:
    """Assigns queued jobs to idle workers and genuinely executes them.

    Blocks until every job assigned this cycle has completed or failed -- this is
    one full cycle, not a background scheduler; call it repeatedly (e.g. in a loop
    submitting new jobs between cycles) for continuous operation.

    Args:
        state: The cluster.
        executor: Where the assigned jobs run.
        timestamp: The cycle's instant.
        registry: Where a job's task is looked up by name (v4.3). A name it
            does not hold fails the job before anything is imported, and nothing
            is resolved as a path. Without one, a task is a dotted path, resolved
            as before (superseded; kept until 5.0, D-54-6).
        assign: Which queued jobs the idle workers take; the cluster's own
            scheduler when ``None``, or a policy of :mod:`alphalab.cluster_scheduler`.
    """
    policy = DistributedEngine.assign_jobs if assign is None else assign
    assigned = policy(state.distributed, timestamp)

    newly_assigned = tuple(
        job for job in assigned.running_jobs.values() if job.status is JobStatus.ASSIGNED
    )
    if not newly_assigned:
        return replace(state, distributed=assigned)

    current = assigned
    for job in newly_assigned:
        current = DistributedEngine.start_job(current, job.job_id, timestamp)

    new_results = dict(state.results)
    if registry is None:
        submitted = [(job.job_id, executor.submit(run_task, job.payload)) for job in newly_assigned]
        # Every outcome is collected first and applied in submission order. Until
        # v3.10 they were applied as the executor finished them, so the results
        # mapping, the distributed event log and the seeded identifiers those events
        # mint all depended on the operating system's scheduling (ledger DET-001).
        outcomes = [(job_id, future.result()) for job_id, future in submitted]
    else:
        outcomes = _run_through(registry, executor, newly_assigned)
    for job_id, outcome in outcomes:
        if outcome.get("success"):
            new_results[job_id] = outcome["result"]
            current = DistributedEngine.complete_job(current, job_id, timestamp)
        else:
            current = DistributedEngine.fail_job(
                current, job_id, outcome.get("error", "unknown error"), timestamp
            )

    return CloudResearchState(distributed=current, results=new_results)


def _run_through(
    registry: RunnerRegistry, executor: Executor, jobs: tuple[Job, ...]
) -> list[tuple[str, dict[str, Any]]]:
    """Every job's outcome, its task looked up in ``registry``, in submission order.

    A job whose task is not registered fails here, before anything is submitted
    to the executor. A task the executor could not run -- one a process pool
    cannot pickle, or a pool that broke -- fails its job, saying why.
    """

    running: list[tuple[str, Future[dict[str, Any]] | str]] = []
    for job in jobs:
        found = _registered(registry, job.payload)
        if isinstance(found, str):
            running.append((job.job_id, found))
        else:
            task, kwargs = found
            running.append((job.job_id, executor.submit(_run_registered, task, dict(kwargs))))
    outcomes: list[tuple[str, dict[str, Any]]] = []
    for job_id, held in running:
        if isinstance(held, str):
            outcomes.append((job_id, {"success": False, "error": held}))
            continue
        try:
            outcomes.append((job_id, held.result()))
        except Exception as exc:
            outcomes.append((job_id, {"success": False, "error": f"{type(exc).__name__}: {exc}"}))
    return outcomes
