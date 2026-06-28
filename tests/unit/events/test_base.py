from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime

import pytest

from alphalab.core.events import DomainEvent, MetadataValue
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import new_event_id

NOW = datetime(2026, 2, 3, 10, 15, tzinfo=UTC)


def test_domain_event_creation_equality_and_serialization() -> None:
    event_id = new_event_id()
    metadata: dict[str, MetadataValue] = {"source": "unit", "attempt": 1}

    event = DomainEvent(id=event_id, timestamp=NOW, schema_version=2, metadata=metadata)
    same_event = DomainEvent(id=event_id, timestamp=NOW, schema_version=2, metadata=metadata)

    assert event == same_event
    assert asdict(event) == {
        "id": event_id,
        "timestamp": NOW,
        "correlation_id": None,
        "causation_id": None,
        "schema_version": 2,
        "metadata": {"source": "unit", "attempt": 1},
    }


def test_domain_event_copies_metadata_at_construction() -> None:
    metadata = {"source": "before"}

    event = DomainEvent(timestamp=NOW, metadata=metadata)
    metadata["source"] = "after"

    assert event.metadata["source"] == "before"


def test_domain_event_is_frozen() -> None:
    event = DomainEvent(timestamp=NOW)

    with pytest.raises(FrozenInstanceError):
        event.__setattr__("schema_version", 2)


def test_domain_event_rejects_naive_timestamp() -> None:
    with pytest.raises(DomainValidationError):
        DomainEvent(timestamp=datetime(2026, 2, 3, 10, 15))


def test_domain_event_rejects_invalid_schema_version() -> None:
    with pytest.raises(DomainValidationError):
        DomainEvent(timestamp=NOW, schema_version=0)


def test_domain_event_rejects_non_scalar_metadata() -> None:
    with pytest.raises(DomainValidationError):
        DomainEvent(timestamp=NOW, metadata={"nested": {"not": "allowed"}})  # type: ignore[dict-item]
