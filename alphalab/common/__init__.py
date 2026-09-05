"""Shared foundations for AlphaLab packages."""

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.constants import DEFAULT_ENCODING, DEFAULT_SCHEMA_VERSION, PACKAGE_NAME
from alphalab.common.events import BaseEvent, CommonEvent
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
from alphalab.common.types import MetadataMapping, MetadataValue
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
