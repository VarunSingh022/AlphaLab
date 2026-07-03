"""AlphaLab Persistence Layer."""

from alphalab.persistence.adapter import PersistenceAdapter
from alphalab.persistence.engine import PersistenceEngine
from alphalab.persistence.events import (
    EventAppended,
    EventsLoaded,
    PersistenceSystemEvent,
    SnapshotLoaded,
    SnapshotSaved,
    StorageCleared,
)
from alphalab.persistence.exceptions import (
    PersistenceError,
    PersistenceValidationError,
    SerializationError,
    StorageError,
)
from alphalab.persistence.protocol import PersistenceProtocol
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.snapshot import Snapshot, StoredEvent
from alphalab.persistence.state import MemoryStoreData, PersistenceState, PersistenceStatistics
from alphalab.persistence.storage import MemoryStorage
from alphalab.persistence.validation import (
    validate_event_append,
    validate_snapshot_load,
    validate_snapshot_save,
)
from alphalab.persistence.views import (
    event_count,
    latest_snapshot,
    snapshot_count,
    storage_statistics,
)

__all__ = [
    "EventAppended",
    "EventsLoaded",
    "MemoryStorage",
    "MemoryStoreData",
    "PersistenceAdapter",
    "PersistenceEngine",
    "PersistenceError",
    "PersistenceProtocol",
    "PersistenceState",
    "PersistenceStatistics",
    "PersistenceSystemEvent",
    "PersistenceValidationError",
    "SerializationError",
    "Snapshot",
    "SnapshotLoaded",
    "SnapshotSaved",
    "StorageCleared",
    "StorageError",
    "StoredEvent",
    "deserialize",
    "event_count",
    "latest_snapshot",
    "serialize",
    "snapshot_count",
    "storage_statistics",
    "validate_event_append",
    "validate_snapshot_load",
    "validate_snapshot_save",
]
