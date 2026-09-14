"""Shared event primitives."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BaseEvent:
    """Minimal immutable event base for subsystem event records."""

    event_id: str
    timestamp: float
