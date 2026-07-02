"""Configuration models for Replay Sessions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReplaySession:
    """Immutable configuration identifying a replay simulation."""

    session_id: str
    start_time: float
    end_time: float
    speed_multiplier: float = 1.0
