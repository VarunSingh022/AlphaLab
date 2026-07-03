"""Pure queries exposing transparent Distributed State access."""

from collections.abc import Sequence

from alphalab.distributed.job import Job
from alphalab.distributed.node import WorkerNode
from alphalab.distributed.state import DistributedState, DistributedStatistics


def queue_length(state: DistributedState) -> int:
    """Returns the current number of pending jobs."""
    return len(state.queued_jobs)


def running_jobs(state: DistributedState) -> Sequence[Job]:
    """Returns all actively executing jobs."""
    return tuple(state.running_jobs.values())


def completed_jobs(state: DistributedState) -> Sequence[Job]:
    """Returns all successfully completed jobs."""
    return tuple(state.completed_jobs.values())


def failed_jobs(state: DistributedState) -> Sequence[Job]:
    """Returns all failed or cancelled jobs."""
    return tuple(state.failed_jobs.values())


def worker_utilization(state: DistributedState) -> dict[str, float]:
    """Returns the percentage of capacity used per active worker."""
    utilization = {}
    for node_id, worker in state.workers.items():
        if worker.capacity == 0:
            utilization[node_id] = 0.0
        else:
            utilization[node_id] = len(worker.running_jobs) / worker.capacity
    return utilization


def cluster_statistics(state: DistributedState) -> DistributedStatistics:
    """Returns global routing and execution metrics."""
    return state.statistics


def active_workers(state: DistributedState) -> Sequence[WorkerNode]:
    """Returns all configured compute nodes."""
    return tuple(state.workers.values())
