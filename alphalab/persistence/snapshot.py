"""Immutable data models for the Event Store and Snapshot Store."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """Immutable representation of a serialized domain event."""

    event_id: str
    timestamp: float
    event_type: str
    payload: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Immutable representation of a serialized subsystem state snapshot."""

    snapshot_id: str
    subsystem: str
    timestamp: float
    payload: str
