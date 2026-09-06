"""Global immutable state container for the Persistence Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap, PersistentSet
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
    """Pure in-memory, immutable backing structures for storage.

    Every field is a persistent container rather than a ``tuple``, ``dict`` or
    ``frozenset``. Appending one event rebuilt all three -- the event tuple, the
    id set, and (on ``PersistenceState``) the system-event tuple -- so storing N
    events copied O(N^2) entries. Measured on v2.8.0, appending 32,000 events
    took 14.0s and each doubling of the workload cost ~4.5x the time; the
    shipped 100k benchmark did not finish.

    This is the same defect v2.1 removed from the engine histories and v2.2 from
    the OMS order book, in the one package that migration never reached. It
    mattered less while nothing persisted through this layer, and it stops being
    optional once durable run state does.

    ``event_ids`` gains a second property in the move: ``PersistentSet``
    iterates in insertion order, so a store holding one serializes
    deterministically. ``frozenset`` had no encoder branch at all, which is why
    neither this state nor :class:`PersistenceState` could be serialized before.
    """

    events: AppendOnlyLog[StoredEvent] = field(default_factory=AppendOnlyLog)
    snapshots: PersistentMap[str, Snapshot] = field(default_factory=PersistentMap)
    event_ids: PersistentSet[str] = field(default_factory=PersistentSet)


@dataclass(frozen=True, slots=True)
class PersistenceState:
    """Deterministic snapshot of the Persistence Layer.

    ``events`` is an :class:`~alphalab.common.append_log.AppendOnlyLog` for the
    reason given on :class:`MemoryStoreData`: every storage operation appends
    one system event, and rebuilding the tuple each time made the layer's cost
    quadratic in the number of operations rather than linear.
    """

    engine_id: str
    store: MemoryStoreData = field(default_factory=MemoryStoreData)
    statistics: PersistenceStatistics = field(default_factory=PersistenceStatistics)
    events: AppendOnlyLog[PersistenceSystemEvent] = field(default_factory=AppendOnlyLog)
    metadata: Mapping[str, str] = field(default_factory=dict)
