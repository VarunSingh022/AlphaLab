"""Top-level Engine Facade orchestrating all distributed components."""

from alphalab.distributed.coordinator import JobCoordinator
from alphalab.distributed.job import Job
from alphalab.distributed.node import WorkerNode, WorkerStatus
from alphalab.distributed.queue import JobQueue
from alphalab.distributed.registry import WorkerRegistry
from alphalab.distributed.scheduler import JobScheduler
from alphalab.distributed.state import DistributedState


class DistributedEngine:
    """Facade for managing the deterministic state of the cluster."""

    @staticmethod
    def initialize(cluster_id: str) -> DistributedState:
        """Constructs an empty base state for the distributed layer."""
        if not cluster_id.strip():
            raise ValueError("Cluster ID cannot be empty.")
        return DistributedState(cluster_id=cluster_id)

    @staticmethod
    def register_worker(
        state: DistributedState,
        worker: WorkerNode,
        timestamp: float,
    ) -> DistributedState:
        return WorkerRegistry.register(state, worker, timestamp)

    @staticmethod
    def remove_worker(
        state: DistributedState, worker_id: str, timestamp: float
    ) -> DistributedState:
        return WorkerRegistry.remove(state, worker_id, timestamp)

    @staticmethod
    def set_worker_status(
        state: DistributedState, worker_id: str, status: WorkerStatus
    ) -> DistributedState:
        return WorkerRegistry.update_status(state, worker_id, status)

    @staticmethod
    def submit_job(state: DistributedState, job: Job, timestamp: float) -> DistributedState:
        return JobQueue.submit(state, job, timestamp)

    @staticmethod
    def cancel_job(state: DistributedState, job_id: str, timestamp: float) -> DistributedState:
        return JobQueue.cancel(state, job_id, timestamp)

    @staticmethod
    def assign_jobs(state: DistributedState, timestamp: float) -> DistributedState:
        return JobScheduler.assign_jobs(state, timestamp)

    @staticmethod
    def start_job(state: DistributedState, job_id: str, timestamp: float) -> DistributedState:
        return JobCoordinator.start_job(state, job_id, timestamp)

    @staticmethod
    def complete_job(state: DistributedState, job_id: str, timestamp: float) -> DistributedState:
        return JobCoordinator.complete_job(state, job_id, timestamp)

    @staticmethod
    def fail_job(
        state: DistributedState, job_id: str, reason: str, timestamp: float
    ) -> DistributedState:
        return JobCoordinator.fail_job(state, job_id, reason, timestamp)
