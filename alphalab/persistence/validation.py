"""Validation rules ensuring strict storage guarantees."""

from alphalab.persistence.exceptions import PersistenceValidationError, StorageError
from alphalab.persistence.serializer import deserialize
from alphalab.persistence.snapshot import Snapshot, StoredEvent
from alphalab.persistence.state import PersistenceState


def validate_snapshot_save(state: PersistenceState, snapshot: Snapshot) -> None:
    """Validates uniqueness and structural integrity of a snapshot."""
    if snapshot.snapshot_id in state.store.snapshots:
        raise StorageError(f"Duplicate snapshot ID: {snapshot.snapshot_id}")
    if snapshot.timestamp < 0.0:
        raise PersistenceValidationError("Snapshot timestamp cannot be negative.")

    # Ensure payload is not corrupt
    deserialize(snapshot.payload)


def validate_event_append(state: PersistenceState, event: StoredEvent) -> None:
    """Enforces strict chronological, append-only uniqueness constraints."""
    if event.event_id in state.store.event_ids:
        raise StorageError(f"Duplicate event ID: {event.event_id}")

    if event.timestamp < 0.0:
        raise PersistenceValidationError("Event timestamp cannot be negative.")

    if state.store.events:
        last_ts = state.store.events[-1].timestamp
        if event.timestamp < last_ts:
            raise StorageError(f"Chronological ordering violation: {event.timestamp} < {last_ts}")

    # Ensure payload is not corrupt
    deserialize(event.payload)


def validate_snapshot_load(state: PersistenceState, snapshot_id: str) -> None:
    """Validates existence of a requested snapshot."""
    if snapshot_id not in state.store.snapshots:
        raise StorageError(f"Snapshot ID not found: {snapshot_id}")
