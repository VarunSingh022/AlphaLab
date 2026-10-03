"""Queue inspection.

Job cancellation is not duplicated here: `alphalab.distributed.engine.DistributedEngine.cancel_job`
already exists and is already exported (it withdraws a queued or assigned job,
marks it CANCELLED, records it among the cancelled jobs with a ``JobCancelled``
event, and increments DistributedStatistics.total_jobs_cancelled -- since v3.12;
until then it stored the job among the failures and recorded no event). An
earlier version of this module built a duplicate of it before this was checked
carefully enough -- caught and removed rather than shipped. `queue_position` is
genuinely not available anywhere in `alphalab.distributed`, including its own
`views.py`.
"""

from alphalab.distributed.state import DistributedState


def queue_position(state: DistributedState, job_id: str) -> int | None:
    """Returns the 0-based position of a job in the pending queue.

    Since `alphalab.distributed.queue.JobQueue.submit` keeps `queued_jobs`
    continuously sorted by (-priority, created_timestamp), this position already
    reflects priority order -- no separate priority-sorted view is needed.

    Returns None if the job isn't currently queued -- already assigned, running,
    completed, failed, cancelled, or never existed are all structurally the same
    "not queued" answer from this function's perspective.
    """
    for index, job in enumerate(state.queued_jobs):
        if job.job_id == job_id:
            return index
    return None
