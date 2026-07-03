"""Adapter translating AlphaLab domain objects to Persistence objects."""

from typing import Any

from alphalab.persistence.serializer import serialize
from alphalab.persistence.snapshot import Snapshot, StoredEvent


class PersistenceAdapter:
    """Stateless translator mapping generic framework objects to storable records."""

    @staticmethod
    def to_stored_event(event_id: str, timestamp: float, domain_event: Any) -> StoredEvent:
        """Serializes an arbitrary domain event into an immutable StoredEvent."""
        event_type = type(domain_event).__name__
        payload = serialize(domain_event)
        return StoredEvent(
            event_id=event_id,
            timestamp=timestamp,
            event_type=event_type,
            payload=payload,
        )

    @staticmethod
    def to_snapshot(
        snapshot_id: str, subsystem: str, timestamp: float, state_object: Any
    ) -> Snapshot:
        """Serializes an arbitrary system state into an immutable Snapshot."""
        payload = serialize(state_object)
        return Snapshot(
            snapshot_id=snapshot_id,
            subsystem=subsystem,
            timestamp=timestamp,
            payload=payload,
        )
