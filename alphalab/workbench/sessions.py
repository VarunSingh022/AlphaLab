"""Immutable tracking of frontend user sessions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkbenchSession:
    session_id: str
    user_id: str
    started_at: float
    last_interaction: float