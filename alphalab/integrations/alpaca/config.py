"""Immutable configuration for Alpaca Broker."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlpacaConfig:
    """Immutable environment configuration."""

    api_key: str
    api_secret: str
    base_url: str = "https://paper-api.alpaca.markets"
    timeout_seconds: float = 5.0
    retry_count: int = 3
    is_paper: bool = False
