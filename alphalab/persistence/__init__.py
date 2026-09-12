"""AlphaLab Persistence Layer.

Two halves, and only one of them was ever load-bearing.

The **codec spine** -- :mod:`~alphalab.persistence.serializer`,
:mod:`~alphalab.persistence.decode` and :mod:`~alphalab.persistence.exceptions`
-- is canonical and imported by every snapshot module in the repository. It
turns domain state into deterministic JSON and typed errors back out, and it is
untouched by v2.13.

The **run-state store** -- :mod:`~alphalab.persistence.run_state` and
:mod:`~alphalab.persistence.run_store`, new in v2.13 -- is where a captured run
state durably lives. See ADR-0029.

Deprecated in v2.13, removed in v3.0
------------------------------------
The nine modules of the original store -- ``protocol``, ``storage``, ``state``,
``engine``, ``adapter``, ``snapshot``, ``views``, ``validation`` and ``events``
-- are deprecated. Measured at v2.12 they had **zero** production importers:
nothing outside this package imported any of them, and the only durable-looking
thing among them, ``MemoryStorage``, held everything in memory, had no run
identity, no decoder for its own state, and drew an identifier from the ambient
source on every operation -- so persisting inside a run's
:func:`~alphalab.common.ids.id_scope` consumed the run's own identifiers.
:class:`~alphalab.persistence.run_store.RunStateStore` replaces them.

**Nothing is removed here, and nothing is aliased.** Those modules keep their
behaviour, their signatures and their tests for the whole of v2.x.
:class:`~alphalab.persistence.run_store.RunStateStore` is not a renamed
``PersistenceProtocol``: it has different addressing, a different unit of
storage and a different identifier contract.

The warning fires on **use of a deprecated name through this package**, not on
importing the package, because this package has production importers of other
symbols -- an import-time warning would fire on every consumer of ``serialize``,
``decode`` and ``StateDecodeError``, which is every snapshot module here, to
deprecate names those consumers never touch. That is the PEP 562 mechanism and
the reasoning ``alphalab.common.CommonEvent`` already uses; see ADR-0029
decision 6 and :mod:`tests.regression.test_deprecation_notices`.
"""

from typing import TYPE_CHECKING

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
from alphalab.persistence.exceptions import (
    PersistenceError,
    PersistenceValidationError,
    SerializationError,
    StateDecodeError,
    StorageError,
)
from alphalab.persistence.run_state import RUN_STATE_ENVELOPE_SCHEMA, RunStateRef
from alphalab.persistence.run_store import (
    FileRunStateStore,
    MemoryRunStateStore,
    RunStateStore,
)
from alphalab.persistence.serializer import deserialize, serialize

if TYPE_CHECKING:
    # Deprecated, and served at runtime by ``__getattr__`` below so that touching
    # one of these names warns. Imported here for type checkers only: a caller
    # that still uses the old store keeps its exact types, and a deprecation is
    # not a reason to make working code un-checkable.
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
    from alphalab.persistence.protocol import PersistenceProtocol
    from alphalab.persistence.snapshot import Snapshot, StoredEvent
    from alphalab.persistence.state import (
        MemoryStoreData,
        PersistenceState,
        PersistenceStatistics,
    )
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
    "RUN_STATE_ENVELOPE_SCHEMA",
    "EventAppended",
    "EventsLoaded",
    "FileRunStateStore",
    "MemoryRunStateStore",
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
    "RunStateRef",
    "RunStateStore",
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

#: Deprecated name -> the module that still defines it, unchanged.
#:
#: The submodules themselves do not warn. A direct
#: ``from alphalab.persistence.storage import MemoryStorage`` is silent, exactly
#: as ``from alphalab.common.events import CommonEvent`` is: the notice guards
#: the package surface, which is the one a reader is steered toward.
_DEPRECATED_STORE: dict[str, str] = {
    "EventAppended": "events",
    "EventsLoaded": "events",
    "MemoryStorage": "storage",
    "MemoryStoreData": "state",
    "PersistenceAdapter": "adapter",
    "PersistenceEngine": "engine",
    "PersistenceProtocol": "protocol",
    "PersistenceState": "state",
    "PersistenceStatistics": "state",
    "PersistenceSystemEvent": "events",
    "Snapshot": "snapshot",
    "SnapshotLoaded": "events",
    "SnapshotSaved": "events",
    "StorageCleared": "events",
    "StoredEvent": "snapshot",
    "event_count": "views",
    "latest_snapshot": "views",
    "snapshot_count": "views",
    "storage_statistics": "views",
    "validate_event_append": "validation",
    "validate_snapshot_load": "validation",
    "validate_snapshot_save": "validation",
}


def __getattr__(name: str) -> object:
    """Serve a deprecated store name with a notice, on use rather than on import.

    The original store is deprecated in v2.13 and removed in v3.0, and
    :class:`~alphalab.persistence.run_store.RunStateStore` is what replaces it.
    The warning is deliberately *not* at module import: every snapshot module in
    the repository imports this package's codec spine, so an import-time warning
    would fire on the whole execution path to deprecate names that path never
    uses -- which is how people learn to filter ``DeprecationWarning``, and then
    the next real notice goes unread. See ADR-0029 decision 6.

    The object served is the same object it always was. This is a notice, not an
    alias and not a shim.
    """

    module = _DEPRECATED_STORE.get(name)
    if module is not None:
        import importlib
        import warnings

        warnings.warn(
            f"alphalab.persistence.{name} is deprecated and will be removed in v3.0. "
            "The original store held everything in memory and drew an identifier from "
            "the run's own stream on every operation; use "
            "alphalab.persistence.RunStateStore (FileRunStateStore, "
            "MemoryRunStateStore) for durable run state. Importing it directly from "
            f"alphalab.persistence.{module} does not warn.",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(importlib.import_module(f"alphalab.persistence.{module}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
