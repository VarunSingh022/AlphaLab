"""Immutable domain events describing changes in the Persistence Layer."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class PersistenceSystemEvent(BaseEvent):
    """Base class for all Persistence lifecycle events."""

    pass


@dataclass(frozen=True, slots=True)
class SnapshotSaved(PersistenceSystemEvent):
    """Emitted when a system snapshot is successfully stored."""

    snapshot_id: str
    subsystem: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class SnapshotLoaded(PersistenceSystemEvent):
    """Emitted when a stored snapshot is retrieved."""

    snapshot_id: str


@dataclass(frozen=True, slots=True)
class EventAppended(PersistenceSystemEvent):
    """Emitted when a business event is durably appended to the event store."""

    stored_event_id: str
    event_type: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class EventsLoaded(PersistenceSystemEvent):
    """Emitted when a historical sequence of events is retrieved."""

    event_count: int


@dataclass(frozen=True, slots=True)
class StorageCleared(PersistenceSystemEvent):
    """Emitted when the storage backend is forcibly cleared."""

    reason: str
