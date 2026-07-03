"""Immutable connection state snapshots."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """Immutable representation of a feed connection status."""
    connected: bool
    latency_ms: float
    provider_name: str
    last_heartbeat: float