"""Deterministic queue management for distributed workloads."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.distributed.events import JobSubmitted
from alphalab.distributed.job import Job, JobStatus
from alphalab.distributed.state import DistributedState
from alphalab.distributed.validation import validate_job_submission


class JobQueue:
    """Stateless queue operations maintaining priority ordering."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def submit(state: DistributedState, job: Job, timestamp: float) -> DistributedState:
        """Validates and appends a job to the priority queue."""
        validate_job_submission(state, job)

        # Higher priority value = higher priority. Tie-breaker is chronological.
        new_queue = sorted(
            (*state.queued_jobs, job), key=lambda j: (-j.priority, j.created_timestamp)
        )

        evt = JobSubmitted(
            JobQueue._create_id(),
            timestamp,
            job.job_id,
            job.job_type.name,
            job.priority,
        )

        new_stats = replace(
            state.statistics, total_jobs_submitted=state.statistics.total_jobs_submitted + 1
        )

        return replace(
            state,
            queued_jobs=tuple(new_queue),
            statistics=new_stats,
            events=(*state.events, evt),
        )

    @staticmethod
    def cancel(state: DistributedState, job_id: str, timestamp: float) -> DistributedState:
        """Removes a job from the queue and marks it cancelled."""
        target_job = next((j for j in state.queued_jobs if j.job_id == job_id), None)
        if not target_job:
            return state

        new_queue = tuple(j for j in state.queued_jobs if j.job_id != job_id)

        cancelled_job = replace(
            target_job,
            status=JobStatus.CANCELLED,
            completed_timestamp=timestamp,
        )
        new_failed_jobs = dict(state.failed_jobs)
        new_failed_jobs[job_id] = cancelled_job

        new_stats = replace(
            state.statistics, total_jobs_cancelled=state.statistics.total_jobs_cancelled + 1
        )

        return replace(
            state,
            queued_jobs=new_queue,
            failed_jobs=new_failed_jobs,
            statistics=new_stats,
        )

    @staticmethod
    def pop_next(state: DistributedState) -> tuple[DistributedState, Job | None]:
        """Pops the highest priority job from the queue."""
        if not state.queued_jobs:
            return state, None

        next_job = state.queued_jobs[0]
        new_queue = state.queued_jobs[1:]

        return replace(state, queued_jobs=new_queue), next_job

    @staticmethod
    def peek(state: DistributedState) -> Job | None:
        """Observes the next job without mutation."""
        if not state.queued_jobs:
            return None
        return state.queued_jobs[0]

    @staticmethod
    def queue_length(state: DistributedState) -> int:
        """Returns the current number of pending jobs."""
        return len(state.queued_jobs)
