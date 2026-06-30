"""Deterministic snapshot persistence for time-travel and exact system restore."""

import pickle
from dataclasses import dataclass

from alphalab.kernel.state import SystemState


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Immutable wrapper encapsulating a point-in-time system state and checkpoint metadata."""

    state: SystemState
    checkpoint_id: str
    timestamp: float


class SnapshotManager:
    """In-memory and serializable snapshot manager providing exact state recovery."""

    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}

    def save(self, state: SystemState, checkpoint_id: str) -> Snapshot:
        """Generates and indexes an immutable snapshot of the active state."""
        snapshot = Snapshot(state=state, checkpoint_id=checkpoint_id, timestamp=state.timestamp)
        self._snapshots[checkpoint_id] = snapshot
        return snapshot

    def load(self, checkpoint_id: str) -> Snapshot:
        """Retrieves an existing snapshot by its checkpoint identifier."""
        if checkpoint_id not in self._snapshots:
            raise KeyError(f"Snapshot checkpoint '{checkpoint_id}' does not exist.")
        return self._snapshots[checkpoint_id]

    def restore(self, checkpoint_id: str) -> SystemState:
        """Restores and extracts the system state associated with a saved checkpoint."""
        return self.load(checkpoint_id).state

    def serialize(self, checkpoint_id: str) -> bytes:
        """Serializes a snapshot into deterministic binary payload."""
        snapshot = self.load(checkpoint_id)
        return pickle.dumps(snapshot)

    def deserialize_and_save(self, payload: bytes) -> Snapshot:
        """Deserializes raw byte payloads and registers the extracted snapshot."""
        snapshot: Snapshot = pickle.loads(payload)
        if not isinstance(snapshot, Snapshot):
            raise TypeError("Deserialized payload is not a valid Snapshot instance.")
        self._snapshots[snapshot.checkpoint_id] = snapshot
        return snapshot
