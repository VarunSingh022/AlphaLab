"""Aging-aware job assignment.

`alphalab.distributed.queue.JobQueue.submit` already keeps the queue sorted by
`(-priority, created_timestamp)` on every submission -- priority ordering itself is
NOT missing from `alphalab.distributed`; an earlier version of this module claimed
it was, without checking `JobQueue.submit` closely enough first. That was wrong and
is corrected here.

What genuinely is missing: that sort is evaluated once, at submission time, and
never revisited. A job's position relative to jobs already queued cannot change
just because time passes, so a continuous stream of high-priority submissions can
starve an old, low-priority job indefinitely -- it keeps losing every comparison
against fresh high-priority arrivals, forever. `assign_jobs_with_aging` adds a
standard fix for exactly this (the same idea as aging in Linux's CFS or YARN's fair
scheduler): effective priority grows with wait time, so an old job eventually
outranks new arrivals regardless of their nominal priority. With `aging_rate=0.0`
this reduces to re-sorting an already-sorted queue -- a harmless no-op, not this
function's reason to exist. The value is entirely in `aging_rate > 0`.

Otherwise a genuine drop-in alternative to `JobScheduler.assign_jobs`: same
worker-selection heuristic (most free capacity), same validation, same event shape.
"""

from dataclasses import replace

from alphalab.cluster_scheduler.exceptions import ClusterSchedulerInputError
from alphalab.common.ids import new_id
from alphalab.distributed.events import DistributedEvent, JobAssigned
from alphalab.distributed.job import Job, JobStatus
from alphalab.distributed.state import DistributedState
from alphalab.distributed.validation import validate_job_transition


def _effective_priority(job: Job, timestamp: float, aging_rate: float) -> float:
    wait_time = max(0.0, timestamp - job.created_timestamp)
    return job.priority + aging_rate * wait_time


def assign_jobs_with_aging(
    state: DistributedState, timestamp: float, aging_rate: float = 0.0
) -> DistributedState:
    """Assigns queued jobs to available workers in descending effective-priority order.

    Raises:
        ClusterSchedulerInputError: If aging_rate is negative.
    """
    if aging_rate < 0:
        raise ClusterSchedulerInputError(f"aging_rate cannot be negative, got {aging_rate}.")
    if not state.queued_jobs or not state.workers:
        return state

    ordered_queue = sorted(
        state.queued_jobs,
        key=lambda job: _effective_priority(job, timestamp, aging_rate),
        reverse=True,
    )

    new_queued = list(ordered_queue)
    new_workers = dict(state.workers)
    new_running = dict(state.running_jobs)
    events: list[DistributedEvent] = []

    available_workers = sorted(
        (w for w in new_workers.values() if len(w.running_jobs) < w.capacity),
        key=lambda w: w.node_id,
    )

    index = 0
    while index < len(new_queued) and available_workers:
        job = new_queued[index]
        worker = max(available_workers, key=lambda w: w.capacity - len(w.running_jobs))

        validate_job_transition(job, JobStatus.ASSIGNED)
        assigned_job = replace(job, status=JobStatus.ASSIGNED, worker_id=worker.node_id)
        updated_worker = replace(worker, running_jobs=(*worker.running_jobs, job.job_id))

        new_queued.pop(index)
        new_running[assigned_job.job_id] = assigned_job
        new_workers[worker.node_id] = updated_worker

        available_workers = sorted(
            (w for w in new_workers.values() if len(w.running_jobs) < w.capacity),
            key=lambda w: w.node_id,
        )
        events.append(JobAssigned(str(new_id()), timestamp, job.job_id, worker.node_id))
        # Do not increment index -- the list was popped from at this position.

    if not events:
        return state

    return replace(
        state,
        queued_jobs=tuple(new_queued),
        workers=new_workers,
        running_jobs=new_running,
        events=(*state.events, *events),
    )
