"""Worker lifecycle and health registry."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.distributed.events import WorkerRegistered, WorkerRemoved
from alphalab.distributed.node import WorkerNode, WorkerStatus
from alphalab.distributed.state import DistributedState
from alphalab.distributed.validation import validate_worker_registration


class WorkerRegistry:
    """Stateless dictionary transformations for the worker lifecycle."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def register(state: DistributedState, worker: WorkerNode, timestamp: float) -> DistributedState:
        """Validates and registers a computation node."""
        validate_worker_registration(state, worker)

        evt = WorkerRegistered(
            WorkerRegistry._create_id(), timestamp, worker.node_id, worker.capacity
        )

        return replace(
            state,
            workers=state.workers.set(worker.node_id, worker),
            events=state.events.append(evt),
        )

    @staticmethod
    def remove(state: DistributedState, worker_id: str, timestamp: float) -> DistributedState:
        """Removes a worker node from the cluster."""
        if worker_id not in state.workers:
            return state

        evt = WorkerRemoved(WorkerRegistry._create_id(), timestamp, worker_id)

        return replace(
            state,
            workers=state.workers.delete(worker_id),
            events=state.events.append(evt),
        )

    @staticmethod
    def update_status(
        state: DistributedState, worker_id: str, status: WorkerStatus
    ) -> DistributedState:
        """Updates a worker's explicit lifecycle status."""
        if worker_id not in state.workers:
            return state

        worker = state.workers[worker_id]
        if worker.status == status:
            return state

        return replace(state, workers=state.workers.set(worker_id, replace(worker, status=status)))
