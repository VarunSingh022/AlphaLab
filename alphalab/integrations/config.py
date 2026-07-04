"""Immutable configurations for broker environments."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    """Immutable environment configuration for a specific broker."""
    broker_id: str
    provider_name: str
    environment: str  # e.g., 'paper', 'live'
    api_base_url: str
    rate_limit_per_second: int = 10
    timeout_seconds: float = 5.0
    metadata: Mapping[str, str] = field(default_factory=dict)