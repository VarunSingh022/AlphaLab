"""Deterministic resource matching algorithms."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.distributed.events import DistributedEvent, JobAssigned
from alphalab.distributed.job import JobStatus
from alphalab.distributed.state import DistributedState
from alphalab.distributed.validation import validate_job_transition


class JobScheduler:
    """Matches pending workloads to available worker capacity deterministically."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def assign_jobs(state: DistributedState, timestamp: float) -> DistributedState:
        """
        Greedy deterministic scheduler.
        Scans queue and attempts to fill worker capacities.
        """
        if not state.queued_jobs or not state.workers:
            return state

        new_queued = list(state.queued_jobs)
        new_workers = dict(state.workers)
        new_running = dict(state.running_jobs)
        events: list[DistributedEvent] = []

        # Sort workers deterministically by node_id to ensure repeatable assignments
        available_workers = sorted(
            [w for w in new_workers.values() if len(w.running_jobs) < w.capacity],
            key=lambda w: w.node_id,
        )

        i = 0
        while i < len(new_queued) and available_workers:
            job = new_queued[i]

            # Select the worker with the most free capacity
            worker = max(available_workers, key=lambda w: w.capacity - len(w.running_jobs))

            # Validate structural constraints
            validate_job_transition(job, JobStatus.ASSIGNED)

            # Mutate Job
            assigned_job = replace(job, status=JobStatus.ASSIGNED, worker_id=worker.node_id)

            # Mutate Worker
            updated_running = (*worker.running_jobs, job.job_id)
            updated_worker = replace(worker, running_jobs=updated_running)

            # Update collections
            new_queued.pop(i)
            new_running[assigned_job.job_id] = assigned_job
            new_workers[worker.node_id] = updated_worker

            # Update local loop tracking
            available_workers = sorted(
                [w for w in new_workers.values() if len(w.running_jobs) < w.capacity],
                key=lambda w: w.node_id,
            )

            events.append(
                JobAssigned(JobScheduler._create_id(), timestamp, job.job_id, worker.node_id)
            )

            # Note: Do not increment 'i' because we popped from the list.

        if not events:
            return state

        return replace(
            state,
            queued_jobs=tuple(new_queued),
            workers=new_workers,
            running_jobs=new_running,
            events=(*state.events, *events),
        )
