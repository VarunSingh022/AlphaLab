"""Validation rules preventing broken workloads or cluster corruption."""

from alphalab.distributed.exceptions import DistributedValidationError, InvalidJobStateError
from alphalab.distributed.job import Job, JobStatus
from alphalab.distributed.node import WorkerNode
from alphalab.distributed.state import DistributedState


def validate_worker_registration(state: DistributedState, worker: WorkerNode) -> None:
    """Ensures worker identities are unique and valid."""
    if not worker.node_id.strip():
        raise DistributedValidationError("Worker ID cannot be empty.")
    if worker.node_id in state.workers:
        raise DistributedValidationError(f"Worker {worker.node_id} already registered.")
    if worker.capacity <= 0:
        raise DistributedValidationError("Worker capacity must be strictly positive.")


def validate_job_submission(state: DistributedState, job: Job) -> None:
    """Ensures jobs are well-formed and unique."""
    if not job.job_id.strip():
        raise DistributedValidationError("Job ID cannot be empty.")
    if job.priority < 0:
        raise DistributedValidationError("Job priority cannot be negative.")

    all_job_ids = (
        {j.job_id for j in state.queued_jobs}
        | set(state.running_jobs.keys())
        | set(state.completed_jobs.keys())
        | set(state.failed_jobs.keys())
    )
    if job.job_id in all_job_ids:
        raise DistributedValidationError(f"Duplicate Job ID detected: {job.job_id}")


def validate_job_transition(job: Job, target_status: JobStatus) -> None:
    """Verifies strict state machine constraints for job processing."""
    valid_transitions = {
        JobStatus.PENDING: {JobStatus.ASSIGNED, JobStatus.CANCELLED},
        JobStatus.ASSIGNED: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
        JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.FAILED},
        JobStatus.COMPLETED: set(),
        JobStatus.FAILED: set(),
        JobStatus.CANCELLED: set(),
    }

    allowed = valid_transitions.get(job.status, set())
    if target_status not in allowed:
        raise InvalidJobStateError(
            f"Cannot transition job {job.job_id} from {job.status.name} to {target_status.name}."
        )
