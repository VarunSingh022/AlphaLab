"""Immutable configuration for Paper Broker."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaperConfig:
    """Immutable environment configuration."""
    api_key: str
    api_secret: str
    base_url: str = "http://localhost"
    timeout_seconds: float = 5.0
    retry_count: int = 3
    is_paper: bool = True