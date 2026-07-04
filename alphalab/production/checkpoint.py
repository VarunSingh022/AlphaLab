"""Immutable states facilitating deterministic recovery."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Immutable system-wide backup of all mission-critical data."""

    checkpoint_id: str
    timestamp: float
    runtime_state: str
    portfolio_state: str
    orders_state: str
    positions_state: str
    research_state: str
    replay_state: str
    metadata: Mapping[str, str] = field(default_factory=dict)
