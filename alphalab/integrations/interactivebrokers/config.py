"""Immutable configuration for Interactive Brokers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InteractiveBrokersConfig:
    """Immutable environment configuration."""
    api_key: str
    api_secret: str
    base_url: str = "http://127.0.0.1:4000"
    timeout_seconds: float = 5.0
    retry_count: int = 3
    is_paper: bool = False