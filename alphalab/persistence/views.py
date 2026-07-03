"""Pure queries exposing transparent Persistence State access."""

from alphalab.persistence.snapshot import Snapshot
from alphalab.persistence.state import PersistenceState, PersistenceStatistics


def storage_statistics(state: PersistenceState) -> PersistenceStatistics:
    """Returns the most recent tracking metrics for the storage backend."""
    return state.statistics


def snapshot_count(state: PersistenceState) -> int:
    """Returns the total number of snapshots actively stored."""
    return len(state.store.snapshots)


def event_count(state: PersistenceState) -> int:
    """Returns the total number of events actively stored."""
    return len(state.store.events)


def latest_snapshot(state: PersistenceState, subsystem: str) -> Snapshot | None:
    """Retrieves the most recent snapshot for a given subsystem by timestamp."""
    sub_snaps = [s for s in state.store.snapshots.values() if s.subsystem == subsystem]
    if not sub_snaps:
        return None

    return max(sub_snaps, key=lambda s: s.timestamp)
