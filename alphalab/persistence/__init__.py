"""AlphaLab Persistence Layer."""

from alphalab.persistence.adapter import PersistenceAdapter
from alphalab.persistence.decode import (
    as_decimal,
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_sequence,
    as_str,
    as_value_enum,
    require,
    require_schema_version,
)
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
    StateDecodeError,
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
    "StateDecodeError",
    "StorageCleared",
    "StorageError",
    "StoredEvent",
    "as_decimal",
    "as_float",
    "as_int",
    "as_mapping",
    "as_named_enum",
    "as_sequence",
    "as_str",
    "as_value_enum",
    "deserialize",
    "event_count",
    "latest_snapshot",
    "require",
    "require_schema_version",
    "serialize",
    "snapshot_count",
    "storage_statistics",
    "validate_event_append",
    "validate_snapshot_load",
    "validate_snapshot_save",
]
