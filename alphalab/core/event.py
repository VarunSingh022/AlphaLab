"""Core domain event model."""

from dataclasses import dataclass
from datetime import datetime

from alphalab.core.enums import EventType
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import EventId, validate_uuid_id


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable record of a domain event.

    Attributes:
        event_id: Unique event identifier.
        event_type: Category of event represented by this record.
        occurred_at: Time the event occurred. The timestamp must be timezone-aware.
        source: Non-empty producer name.
        correlation_id: Optional event identifier used to correlate related events.
    """

    event_id: EventId
    event_type: EventType
    occurred_at: datetime
    source: str
    correlation_id: EventId | None = None

    def __post_init__(self) -> None:
        validate_uuid_id(self.event_id, "event_id")
        if self.correlation_id is not None:
            validate_uuid_id(self.correlation_id, "correlation_id")
        if not isinstance(self.event_type, EventType):
            raise DomainValidationError("event_type must be an EventType")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise DomainValidationError("occurred_at must be timezone-aware")
        if not self.source.strip():
            raise DomainValidationError("source must be non-empty")
