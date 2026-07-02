"""Immutable domain events describing changes in the Replay Engine's lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReplaySystemEvent:
    """Base class for all Replay system lifecycle events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class ReplayStarted(ReplaySystemEvent):
    """Emitted when a replay session begins execution."""

    session_id: str
    total_events: int


@dataclass(frozen=True, slots=True)
class ReplayPaused(ReplaySystemEvent):
    """Emitted when a running replay session is manually paused."""

    session_id: str


@dataclass(frozen=True, slots=True)
class ReplayResumed(ReplaySystemEvent):
    """Emitted when a paused replay session resumes."""

    session_id: str


@dataclass(frozen=True, slots=True)
class ReplayStopped(ReplaySystemEvent):
    """Emitted when a replay session is intentionally stopped before completion."""

    session_id: str


@dataclass(frozen=True, slots=True)
class ReplayCompleted(ReplaySystemEvent):
    """Emitted when a replay session successfully exhausts all historical events."""

    session_id: str


@dataclass(frozen=True, slots=True)
class ReplayAdvanced(ReplaySystemEvent):
    """Emitted when the replay clock is advanced and events are yielded."""

    old_timestamp: float
    new_timestamp: float
    events_yielded: int


@dataclass(frozen=True, slots=True)
class ReplayReset(ReplaySystemEvent):
    """Emitted when the replay engine state is forcibly reset to its initial condition."""

    reset_time: float
