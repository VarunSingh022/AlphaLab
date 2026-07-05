"""Worker lifecycle and health registry."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.common.registry import with_mapping_item, without_mapping_key
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

        new_workers = with_mapping_item(state.workers, worker.node_id, worker)

        evt = WorkerRegistered(
            WorkerRegistry._create_id(), timestamp, worker.node_id, worker.capacity
        )

        return replace(state, workers=new_workers, events=(*state.events, evt))

    @staticmethod
    def remove(state: DistributedState, worker_id: str, timestamp: float) -> DistributedState:
        """Removes a worker node from the cluster."""
        if worker_id not in state.workers:
            return state

        new_workers = without_mapping_key(state.workers, worker_id)

        evt = WorkerRemoved(WorkerRegistry._create_id(), timestamp, worker_id)

        return replace(state, workers=new_workers, events=(*state.events, evt))

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

        updated_worker = replace(worker, status=status)
        new_workers = with_mapping_item(state.workers, worker_id, updated_worker)

        return replace(state, workers=new_workers)
