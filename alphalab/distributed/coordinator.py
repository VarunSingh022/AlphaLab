"""High-level orchestration of job execution lifecycles."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.distributed.events import JobCompleted, JobFailed, JobStarted
from alphalab.distributed.job import JobStatus
from alphalab.distributed.state import DistributedState
from alphalab.distributed.validation import validate_job_transition


class JobCoordinator:
    """Stateless tracker of job starts, completions, and failures."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def start_job(state: DistributedState, job_id: str, timestamp: float) -> DistributedState:
        """Transitions a job from ASSIGNED to RUNNING."""
        if job_id not in state.running_jobs:
            return state

        job = state.running_jobs[job_id]
        validate_job_transition(job, JobStatus.RUNNING)

        running_job = replace(job, status=JobStatus.RUNNING, started_timestamp=timestamp)

        evt = JobStarted(JobCoordinator._create_id(), timestamp, job_id, job.worker_id or "UNKNOWN")

        return replace(
            state,
            running_jobs=state.running_jobs.set(job_id, running_job),
            events=state.events.append(evt),
        )

    @staticmethod
    def complete_job(state: DistributedState, job_id: str, timestamp: float) -> DistributedState:
        """Transitions a job to COMPLETED and frees worker capacity."""
        if job_id not in state.running_jobs:
            return state

        job = state.running_jobs[job_id]
        validate_job_transition(job, JobStatus.COMPLETED)

        completed_job = replace(job, status=JobStatus.COMPLETED, completed_timestamp=timestamp)
        exec_time = timestamp - job.started_timestamp if job.started_timestamp > 0 else 0.0

        new_workers = state.workers
        if job.worker_id and job.worker_id in new_workers:
            worker = new_workers[job.worker_id]
            updated_running = tuple(j for j in worker.running_jobs if j != job_id)
            new_workers = new_workers.set(
                job.worker_id,
                replace(
                    worker, running_jobs=updated_running, completed_jobs=worker.completed_jobs + 1
                ),
            )

        evt = JobCompleted(
            JobCoordinator._create_id(), timestamp, job_id, job.worker_id or "UNKNOWN", exec_time
        )

        new_stats = replace(
            state.statistics, total_jobs_completed=state.statistics.total_jobs_completed + 1
        )

        return replace(
            state,
            running_jobs=state.running_jobs.delete(job_id),
            completed_jobs=state.completed_jobs.set(job_id, completed_job),
            workers=new_workers,
            statistics=new_stats,
            events=state.events.append(evt),
        )

    @staticmethod
    def fail_job(
        state: DistributedState, job_id: str, reason: str, timestamp: float
    ) -> DistributedState:
        """Transitions a job to FAILED and safely clears worker capacity."""
        if job_id not in state.running_jobs:
            return state

        job = state.running_jobs[job_id]
        validate_job_transition(job, JobStatus.FAILED)

        failed_job = replace(job, status=JobStatus.FAILED, completed_timestamp=timestamp)

        new_workers = state.workers
        if job.worker_id and job.worker_id in new_workers:
            worker = new_workers[job.worker_id]
            updated_running = tuple(j for j in worker.running_jobs if j != job_id)
            new_workers = new_workers.set(
                job.worker_id, replace(worker, running_jobs=updated_running)
            )

        evt = JobFailed(
            JobCoordinator._create_id(), timestamp, job_id, job.worker_id or "UNKNOWN", reason
        )

        new_stats = replace(
            state.statistics, total_jobs_failed=state.statistics.total_jobs_failed + 1
        )

        return replace(
            state,
            running_jobs=state.running_jobs.delete(job_id),
            failed_jobs=state.failed_jobs.set(job_id, failed_job),
            workers=new_workers,
            statistics=new_stats,
            events=state.events.append(evt),
        )
