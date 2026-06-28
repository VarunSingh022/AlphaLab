"""Base event type for the AlphaLab event pipeline."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import EventId, new_event_id, validate_uuid_id

MetadataValue = str | int | float | bool | None
Metadata = Mapping[str, MetadataValue]


def _empty_metadata() -> dict[str, MetadataValue]:
    """Create an empty metadata mapping.

    Returns:
        A new empty metadata dictionary.
    """

    return {}


def _utc_now() -> datetime:
    """Create a timezone-aware UTC timestamp.

    Returns:
        Current time in UTC.
    """

    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Immutable base event for the runtime event pipeline.

    Attributes:
        id: Unique UUID-backed event identifier.
        timestamp: Time the event occurred. The timestamp must be timezone-aware.
        correlation_id: Optional identifier shared across related events.
        causation_id: Optional identifier of the event that caused this event.
        schema_version: Positive schema version for the event shape.
        metadata: Serialization-friendly metadata copied at construction time.
    """

    id: EventId = field(default_factory=new_event_id)
    timestamp: datetime = field(default_factory=_utc_now)
    correlation_id: EventId | None = None
    causation_id: EventId | None = None
    schema_version: int = 1
    metadata: Metadata = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        validate_uuid_id(self.id, "id")
        if self.correlation_id is not None:
            validate_uuid_id(self.correlation_id, "correlation_id")
        if self.causation_id is not None:
            validate_uuid_id(self.causation_id, "causation_id")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise DomainValidationError("timestamp must be timezone-aware")
        if self.schema_version < 1:
            raise DomainValidationError("schema_version must be positive")
        self._validate_metadata()
        object.__setattr__(self, "metadata", dict(self.metadata))

    def _validate_metadata(self) -> None:
        for key, value in self.metadata.items():
            if not key:
                raise DomainValidationError("metadata keys must be non-empty strings")
            if not isinstance(value, str | int | float | bool | None):
                raise DomainValidationError("metadata values must be scalar JSON-like values")
