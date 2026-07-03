"""Deterministic pure-memory storage backend satisfying PersistenceProtocol."""

import uuid
from collections.abc import Sequence
from dataclasses import replace

from alphalab.persistence.events import (
    EventAppended,
    EventsLoaded,
    PersistenceSystemEvent,
    SnapshotLoaded,
    SnapshotSaved,
    StorageCleared,
)
from alphalab.persistence.snapshot import Snapshot, StoredEvent
from alphalab.persistence.state import MemoryStoreData, PersistenceState, PersistenceStatistics
from alphalab.persistence.validation import (
    validate_event_append,
    validate_snapshot_load,
    validate_snapshot_save,
)


class MemoryStorage:
    """Pure in-memory, deterministic storage simulation."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    def save_snapshot(
        self, state: PersistenceState, snapshot: Snapshot, timestamp: float
    ) -> tuple[PersistenceState, tuple[PersistenceSystemEvent, ...]]:
        validate_snapshot_save(state, snapshot)

        byte_size = len(snapshot.payload.encode("utf-8"))
        evt = SnapshotSaved(
            self._create_id(), timestamp, snapshot.snapshot_id, snapshot.subsystem, byte_size
        )

        new_snapshots = dict(state.store.snapshots)
        new_snapshots[snapshot.snapshot_id] = snapshot

        new_store = replace(state.store, snapshots=new_snapshots)
        new_stats = replace(
            state.statistics,
            total_snapshots_saved=state.statistics.total_snapshots_saved + 1,
            bytes_stored=state.statistics.bytes_stored + byte_size,
        )

        new_state = replace(
            state, store=new_store, statistics=new_stats, events=(*state.events, evt)
        )
        return new_state, (evt,)

    def load_snapshot(
        self, state: PersistenceState, snapshot_id: str, timestamp: float
    ) -> tuple[PersistenceState, Snapshot, tuple[PersistenceSystemEvent, ...]]:
        validate_snapshot_load(state, snapshot_id)

        snapshot = state.store.snapshots[snapshot_id]
        evt = SnapshotLoaded(self._create_id(), timestamp, snapshot_id)

        new_state = replace(state, events=(*state.events, evt))
        return new_state, snapshot, (evt,)

    def append_event(
        self, state: PersistenceState, event: StoredEvent, timestamp: float
    ) -> tuple[PersistenceState, tuple[PersistenceSystemEvent, ...]]:
        validate_event_append(state, event)

        byte_size = len(event.payload.encode("utf-8"))
        sys_evt = EventAppended(
            self._create_id(), timestamp, event.event_id, event.event_type, byte_size
        )

        new_events = (*state.store.events, event)
        new_event_ids = frozenset(state.store.event_ids | {event.event_id})

        new_store = replace(state.store, events=new_events, event_ids=new_event_ids)
        new_stats = replace(
            state.statistics,
            total_events_appended=state.statistics.total_events_appended + 1,
            bytes_stored=state.statistics.bytes_stored + byte_size,
        )

        new_state = replace(
            state, store=new_store, statistics=new_stats, events=(*state.events, sys_evt)
        )
        return new_state, (sys_evt,)

    def load_events(
        self, state: PersistenceState, timestamp: float
    ) -> tuple[PersistenceState, Sequence[StoredEvent], tuple[PersistenceSystemEvent, ...]]:
        events = state.store.events
        evt = EventsLoaded(self._create_id(), timestamp, len(events))

        new_state = replace(state, events=(*state.events, evt))
        return new_state, events, (evt,)

    def clear(
        self, state: PersistenceState, reason: str, timestamp: float
    ) -> tuple[PersistenceState, tuple[PersistenceSystemEvent, ...]]:
        evt = StorageCleared(self._create_id(), timestamp, reason)

        new_store = MemoryStoreData()
        new_stats = PersistenceStatistics()

        new_state = replace(
            state, store=new_store, statistics=new_stats, events=(*state.events, evt)
        )
        return new_state, (evt,)

    def statistics(self, state: PersistenceState) -> PersistenceStatistics:
        return state.statistics
