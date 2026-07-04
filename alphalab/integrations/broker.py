"""Immutable models tracking abstract integration health and state."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    """Immutable tracking of physical broker API stability."""
    broker_id: str
    latency_ms: float
    reconnect_count: int
    api_available: bool
    rate_limit_remaining: int