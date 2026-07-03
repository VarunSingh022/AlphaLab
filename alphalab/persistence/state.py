"""Global immutable state container for the Persistence Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.persistence.events import PersistenceSystemEvent
from alphalab.persistence.snapshot import Snapshot, StoredEvent


@dataclass(frozen=True, slots=True)
class PersistenceStatistics:
    """Immutable tracking metrics for the storage backend."""

    total_events_appended: int = 0
    total_snapshots_saved: int = 0
    bytes_stored: int = 0


@dataclass(frozen=True, slots=True)
class MemoryStoreData:
    """Pure in-memory, immutable backing structures for storage."""

    events: tuple[StoredEvent, ...] = field(default_factory=tuple)
    snapshots: Mapping[str, Snapshot] = field(default_factory=dict)
    event_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class PersistenceState:
    """Deterministic snapshot of the Persistence Layer."""

    engine_id: str
    store: MemoryStoreData = field(default_factory=MemoryStoreData)
    statistics: PersistenceStatistics = field(default_factory=PersistenceStatistics)
    events: tuple[PersistenceSystemEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
