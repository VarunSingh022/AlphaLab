"""Immutable interface protocol for Persistence storage backends."""

from collections.abc import Sequence
from typing import Protocol

from alphalab.persistence.events import PersistenceSystemEvent
from alphalab.persistence.snapshot import Snapshot, StoredEvent
from alphalab.persistence.state import PersistenceState, PersistenceStatistics


class PersistenceProtocol(Protocol):
    """Pure functional interface mapping storage interactions."""

    def save_snapshot(
        self, state: PersistenceState, snapshot: Snapshot, timestamp: float
    ) -> tuple[PersistenceState, tuple[PersistenceSystemEvent, ...]]: ...

    def load_snapshot(
        self, state: PersistenceState, snapshot_id: str, timestamp: float
    ) -> tuple[PersistenceState, Snapshot, tuple[PersistenceSystemEvent, ...]]: ...

    def append_event(
        self, state: PersistenceState, event: StoredEvent, timestamp: float
    ) -> tuple[PersistenceState, tuple[PersistenceSystemEvent, ...]]: ...

    def load_events(
        self, state: PersistenceState, timestamp: float
    ) -> tuple[PersistenceState, Sequence[StoredEvent], tuple[PersistenceSystemEvent, ...]]: ...

    def clear(
        self, state: PersistenceState, reason: str, timestamp: float
    ) -> tuple[PersistenceState, tuple[PersistenceSystemEvent, ...]]: ...

    def statistics(self, state: PersistenceState) -> PersistenceStatistics: ...
