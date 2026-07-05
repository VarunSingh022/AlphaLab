"""Shared event primitives."""

from dataclasses import dataclass, field
from datetime import datetime

from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
from alphalab.common.ids import Identifier, new_id
from alphalab.common.metadata import copy_metadata
from alphalab.common.time import ensure_timezone_aware, utc_now
from alphalab.common.types import MetadataMapping
from alphalab.common.validators import require_non_empty_string, require_positive_int


@dataclass(frozen=True, slots=True)
class BaseEvent:
    """Minimal immutable event base for subsystem event records."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class CommonEvent:
    """Minimal immutable event envelope shared across packages."""

    name: str
    id: Identifier = field(default_factory=new_id)
    timestamp: datetime = field(default_factory=utc_now)
    schema_version: int = DEFAULT_SCHEMA_VERSION
    metadata: MetadataMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty_string(self.name, "name")
        ensure_timezone_aware(self.timestamp)
        require_positive_int(self.schema_version, "schema_version")
        object.__setattr__(self, "metadata", copy_metadata(self.metadata))
