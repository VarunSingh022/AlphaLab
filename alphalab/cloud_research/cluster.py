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
"""

from collections.abc import Mapping
from concurrent.futures import Executor, as_completed
from dataclasses import dataclass, field, replace
from typing import Any

from alphalab.cloud_research.exceptions import CloudResearchInputError
from alphalab.cloud_research.task import run_task
from alphalab.common.ids import new_id
from alphalab.distributed.engine import DistributedEngine
from alphalab.distributed.job import Job, JobStatus, JobType
from alphalab.distributed.node import WorkerNode, WorkerStatus
from alphalab.distributed.state import DistributedState


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


def submit_research_job(
    state: CloudResearchState,
    job_type: JobType,
    task_path: str,
    kwargs: Mapping[str, Any],
    priority: int,
    timestamp: float,
) -> tuple[CloudResearchState, str]:
    """Submits one job for a real, importable task.

    Returns the updated state and the new job's id.
    """
    job_id = str(new_id())
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


def run_cluster_cycle(
    state: CloudResearchState, executor: Executor, timestamp: float
) -> CloudResearchState:
    """Assigns queued jobs to idle workers and genuinely executes them.

    Blocks until every job assigned this cycle has completed or failed -- this is
    one full cycle, not a background scheduler; call it repeatedly (e.g. in a loop
    submitting new jobs between cycles) for continuous operation.
    """
    assigned = DistributedEngine.assign_jobs(state.distributed, timestamp)

    newly_assigned = tuple(
        job for job in assigned.running_jobs.values() if job.status is JobStatus.ASSIGNED
    )
    if not newly_assigned:
        return replace(state, distributed=assigned)

    current = assigned
    for job in newly_assigned:
        current = DistributedEngine.start_job(current, job.job_id, timestamp)

    futures = {executor.submit(run_task, job.payload): job.job_id for job in newly_assigned}
    new_results = dict(state.results)

    for future in as_completed(futures):
        job_id = futures[future]
        outcome = future.result()
        if outcome.get("success"):
            new_results[job_id] = outcome["result"]
            current = DistributedEngine.complete_job(current, job_id, timestamp)
        else:
            current = DistributedEngine.fail_job(
                current, job_id, outcome.get("error", "unknown error"), timestamp
            )

    return CloudResearchState(distributed=current, results=new_results)
