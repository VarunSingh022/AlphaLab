"""AlphaLab Persistence Layer.

Two halves, and as of v2.17 only the load-bearing one remains.

The **codec spine** -- :mod:`~alphalab.persistence.serializer`,
:mod:`~alphalab.persistence.decode` and :mod:`~alphalab.persistence.exceptions`
-- is canonical and imported by every snapshot module in the repository. It
turns domain state into deterministic JSON and typed errors back out.

The **run-state store** -- :mod:`~alphalab.persistence.run_state` and
:mod:`~alphalab.persistence.run_store` -- is where a captured run state durably
lives. See ADR-0029.

Removed in v2.17
----------------
The nine modules of the original store -- ``protocol``, ``storage``, ``state``,
``engine``, ``adapter``, ``snapshot``, ``views``, ``validation`` and ``events``
-- were deprecated in v2.13 with a v3.0 removal date and are **gone**. ADR-0034
brings that removal forward, because v2.17 is the last engineering release and a
surface nothing imports is not worth carrying into a frozen API.

They had zero production importers when they were deprecated and zero when they
were removed. :class:`~alphalab.persistence.run_store.RunStateStore` is what
replaces them, and it is not a renamed ``PersistenceProtocol``: it has different
addressing, a different unit of storage and a different identifier contract.

**Nothing is aliased.** There is no compatibility shim and no PEP 562 hook here
any more; the names are simply absent, which is what makes an ``ImportError``
the honest answer rather than a notice about something that no longer exists.
"""

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

__all__ = [
    "RUN_STATE_ENVELOPE_SCHEMA",
    "FileRunStateStore",
    "MemoryRunStateStore",
    "PersistenceError",
    "PersistenceValidationError",
    "RunStateRef",
    "RunStateStore",
    "SerializationError",
    "StateDecodeError",
    "StorageError",
    "as_decimal",
    "as_float",
    "as_int",
    "as_mapping",
    "as_named_enum",
    "as_sequence",
    "as_str",
    "as_value_enum",
    "deserialize",
    "require",
    "require_schema_version",
    "serialize",
]
