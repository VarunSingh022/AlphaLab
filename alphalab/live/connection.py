"""Immutable tracking models for provider network connection states."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectionState:
    """Immutable snapshot of a provider's connection status."""

    provider_id: str
    connected: bool = False
    latency_ms: float = 0.0
    last_heartbeat: float = 0.0
