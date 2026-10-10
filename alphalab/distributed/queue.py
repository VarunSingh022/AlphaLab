"""Deterministic queue management for distributed workloads.

The queue is kept in priority order at all times: descending ``priority``, then
ascending ``created_timestamp``. That has always been the contract --
``alphalab.cluster_scheduler.queue_manager.queue_position`` relies on it, and so
does every consumer that reads ``queued_jobs[0]``.

How it is kept, as of v2.17
---------------------------

:meth:`JobQueue.submit` used to rebuild the whole queue and re-sort it on every
submission. Profiled at 8,000 submissions that was ~65% of the total cost and
32,004,000 evaluations of the sort key -- quadratic in the number of jobs, and
the largest single term in this package (see :mod:`alphalab.distributed.state`).

A job whose key sorts at or after the current tail belongs at the end, so it is
**appended** in O(1) amortized. The ordering that produces is identical to
re-sorting: Python's sort is stable, so a new element with a key equal to the
tail's would be placed after it anyway. Only a job that must go *earlier* than an
existing one costs a rebuild, and that is the uncommon case -- a queue is
normally fed in arrival order at a given priority.
"""

from dataclasses import replace

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import new_id
from alphalab.distributed.events import JobCancelled, JobSubmitted
from alphalab.distributed.job import Job, JobStatus
from alphalab.distributed.state import DistributedState
from alphalab.distributed.validation import validate_job_submission, validate_job_transition


def queue_key(job: Job) -> tuple[int, float]:
    """The ordering key: higher priority first, then chronological.

    The one definition. :meth:`JobQueue.submit` uses it to decide whether an
    append preserves order and to rebuild when it does not, so the fast path and
    the slow path cannot disagree about what "sorted" means.
    """

    return (-job.priority, job.created_timestamp)


class JobQueue:
    """Stateless queue operations maintaining priority ordering."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def submit(state: DistributedState, job: Job, timestamp: float) -> DistributedState:
        """Validates and inserts a job into the priority queue."""
        validate_job_submission(state, job)

        queued = state.queued_jobs
        if not queued or queue_key(queued[-1]) <= queue_key(job):
            new_queue = queued.append(job)
        else:
            # The job belongs before something already queued, so the order has
            # to be re-established. Stable, and over the same key, so the result
            # is what the fast path would have produced had it applied.
            new_queue = AppendOnlyLog(sorted([*queued, job], key=queue_key))

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
            queued_jobs=new_queue,
            queued_ids=state.queued_ids.add(job.job_id),
            statistics=new_stats,
            events=state.events.append(evt),
        )

    @staticmethod
    def cancel(state: DistributedState, job_id: str, timestamp: float) -> DistributedState:
        """Withdraws a job that has not started, recording that it was cancelled.

        A queued job leaves the queue; a job assigned to a worker and not yet
        running frees the worker's slot. A running job cannot be cancelled --
        it runs to completion or fails (:func:`validate_job_transition`) -- and
        an unknown or finished job is left as it is. Either way a cancellation
        is recorded among the cancelled jobs with a :class:`JobCancelled` event:
        until v3.12 it was stored among the failures and recorded nothing
        (ledger SCF-003).
        """
        target_job = None
        if job_id in state.queued_ids:
            target_job = next((j for j in state.queued_jobs if j.job_id == job_id), None)
        if target_job is not None:
            state = replace(
                state,
                queued_jobs=AppendOnlyLog(j for j in state.queued_jobs if j.job_id != job_id),
                queued_ids=state.queued_ids.discard(job_id),
            )
        elif job_id in state.running_jobs:
            target_job = state.running_jobs[job_id]
            validate_job_transition(target_job, JobStatus.CANCELLED)
            workers = state.workers
            if target_job.worker_id and target_job.worker_id in workers:
                worker = workers[target_job.worker_id]
                workers = workers.set(
                    target_job.worker_id,
                    replace(
                        worker,
                        running_jobs=tuple(j for j in worker.running_jobs if j != job_id),
                    ),
                )
            state = replace(state, running_jobs=state.running_jobs.delete(job_id), workers=workers)
        else:
            return state

        cancelled_job = replace(
            target_job,
            status=JobStatus.CANCELLED,
            completed_timestamp=timestamp,
        )
        event = JobCancelled(str(new_id()), timestamp, job_id, target_job.worker_id or "UNASSIGNED")
        return replace(
            state,
            cancelled_jobs=state.cancelled_jobs.set(job_id, cancelled_job),
            statistics=replace(
                state.statistics, total_jobs_cancelled=state.statistics.total_jobs_cancelled + 1
            ),
            events=state.events.append(event),
        )

    @staticmethod
    def pop_next(state: DistributedState) -> tuple[DistributedState, Job | None]:
        """Pops the highest priority job from the queue."""
        if not state.queued_jobs:
            return state, None

        next_job = state.queued_jobs[0]
        new_queue = AppendOnlyLog(state.queued_jobs[1:])

        return replace(
            state,
            queued_jobs=new_queue,
            queued_ids=state.queued_ids.discard(next_job.job_id),
        ), next_job

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
