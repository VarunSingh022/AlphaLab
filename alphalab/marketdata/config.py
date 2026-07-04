"""Immutable configurations for Market Data Providers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider_id: str
    name: str
    api_key: str
    api_secret: str = ""
    base_url: str = ""
    timeout: float = 5.0
