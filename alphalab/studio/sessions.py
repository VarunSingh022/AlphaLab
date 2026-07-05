"""Immutable tracking of active researcher sessions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StudioSession:
    session_id: str
    user_id: str
    active_project_id: str
    started_at: float
    last_activity: float
