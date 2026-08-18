"""Tag-based worker affinity routing.

Neither Job.metadata nor WorkerNode.metadata (both Mapping[str, str], already
present on both types) is consulted by JobScheduler.assign_jobs -- any worker can
run any job regardless of capability. This adds real tag-matching: a job is only
assigned to a worker whose tags are a superset of what the job requires (e.g. a
job tagged "gpu" only runs on a worker tagged "gpu"), using a "tags" key already
storable in each type's existing metadata mapping rather than adding new fields.

Unlike JobScheduler.assign_jobs and assign_jobs_with_priority, which always place
the front-of-queue job whenever any worker has capacity, this skips a job that has
no matching worker rather than blocking every job behind it in the queue -- one
job requiring a capability no current worker has should not stall unrelated jobs
that could otherwise run immediately.
"""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.distributed.events import DistributedEvent, JobAssigned
from alphalab.distributed.job import JobStatus
from alphalab.distributed.node import WorkerNode
from alphalab.distributed.state import DistributedState
from alphalab.distributed.validation import validate_job_transition

_TAGS_KEY = "tags"


def _parse_tags(metadata: dict[str, str]) -> frozenset[str]:
    raw = metadata.get(_TAGS_KEY, "")
    return frozenset(tag.strip() for tag in raw.split(",") if tag.strip())


def assign_jobs_with_affinity(state: DistributedState, timestamp: float) -> DistributedState:
    """Assigns queued jobs to workers whose tags are a superset of each job's
    required tags, skipping jobs with no currently-matching worker.
    """
    if not state.queued_jobs or not state.workers:
        return state

    new_queued = list(state.queued_jobs)
    new_workers = dict(state.workers)
    new_running = dict(state.running_jobs)
    events: list[DistributedEvent] = []

    index = 0
    while index < len(new_queued):
        job = new_queued[index]
        required_tags = _parse_tags(dict(job.metadata))

        candidates = sorted(
            (
                w
                for w in new_workers.values()
                if len(w.running_jobs) < w.capacity
                and required_tags.issubset(_parse_tags(dict(w.metadata)))
            ),
            key=lambda w: w.node_id,
        )
        if not candidates:
            index += 1
            continue

        worker: WorkerNode = max(candidates, key=lambda w: w.capacity - len(w.running_jobs))
        validate_job_transition(job, JobStatus.ASSIGNED)
        assigned_job = replace(job, status=JobStatus.ASSIGNED, worker_id=worker.node_id)
        updated_worker = replace(worker, running_jobs=(*worker.running_jobs, job.job_id))

        new_queued.pop(index)
        new_running[assigned_job.job_id] = assigned_job
        new_workers[worker.node_id] = updated_worker
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
