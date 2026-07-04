"""Immutable configuration for Zerodha Broker."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ZerodhaConfig:
    """Immutable environment configuration."""

    api_key: str
    api_secret: str
    base_url: str = "https://api.kite.trade"
    timeout_seconds: float = 5.0
    retry_count: int = 3
    is_paper: bool = False
