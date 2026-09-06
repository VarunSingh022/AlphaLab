"""Shared foundations for AlphaLab packages."""

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.constants import DEFAULT_ENCODING, DEFAULT_SCHEMA_VERSION, PACKAGE_NAME
from alphalab.common.events import BaseEvent
from alphalab.common.exceptions import (
    AlphaLabError,
    AlphaLabRegistryError,
    AlphaLabSerializationError,
    AlphaLabValidationError,
)
from alphalab.common.ids import (
    DeterministicIdSource,
    Identifier,
    id_scope,
    id_source,
    is_uuid,
    new_id,
    require_uuid,
    use_id_source,
)
from alphalab.common.metadata import Metadata, copy_metadata
from alphalab.common.point_in_time import PointInTimeRecord
from alphalab.common.point_in_time import known_as_of as generic_known_as_of
from alphalab.common.results import Result
from alphalab.common.serialization import dataclass_to_dict
from alphalab.common.time import ensure_timezone_aware, to_utc, utc_now
from alphalab.common.types import MetadataMapping, MetadataValue, ParamValue
from alphalab.common.validators import (
    require_non_empty_string,
    require_non_negative_int,
    require_positive_int,
    require_type,
)
from alphalab.common.version import __version__

__all__ = [
    "DEFAULT_ENCODING",
    "DEFAULT_SCHEMA_VERSION",
    "PACKAGE_NAME",
    "AlphaLabError",
    "AlphaLabRegistryError",
    "AlphaLabSerializationError",
    "AlphaLabValidationError",
    "AppendOnlyLog",
    "BaseEvent",
    "CommonEvent",
    "DeterministicIdSource",
    "Identifier",
    "Metadata",
    "MetadataMapping",
    "MetadataValue",
    "ParamValue",
    "PointInTimeRecord",
    "Registry",
    "Result",
    "__version__",
    "copy_metadata",
    "dataclass_to_dict",
    "ensure_timezone_aware",
    "generic_known_as_of",
    "id_scope",
    "id_source",
    "is_uuid",
    "new_id",
    "require_non_empty_string",
    "require_non_negative_int",
    "require_positive_int",
    "require_type",
    "require_uuid",
    "to_utc",
    "use_id_source",
    "utc_now",
]


def __getattr__(name: str) -> object:
    """Serve ``CommonEvent`` with a deprecation warning, on use rather than import.

    ``CommonEvent`` is deprecated in v2.6 and removed in v3.0: it has no
    consumer anywhere in this repository, and every subsystem event derives from
    :class:`~alphalab.common.events.BaseEvent` instead.

    The warning is deliberately *not* at module import. Nearly everything in
    AlphaLab imports ``alphalab.common`` for ``BaseEvent``, so an import-time
    warning would fire on every run to deprecate a symbol nobody uses -- which is
    how people learn to filter DeprecationWarning. PEP 562 lets the warning
    reach exactly the caller who touches the name. See ADR-0015 decision 9.
    """

    if name == "CommonEvent":
        import warnings

        from alphalab.common.events import CommonEvent

        warnings.warn(
            "alphalab.common.CommonEvent is deprecated and will be removed in "
            "v3.0. Subsystem events derive from alphalab.common.events.BaseEvent.",
            DeprecationWarning,
            stacklevel=2,
        )
        return CommonEvent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
