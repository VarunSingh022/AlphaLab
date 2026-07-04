"""Immutable configuration for databento Finance Data Provider."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class databentoConfig:
    provider_id: str
    api_key: str
    base_url: str = "https://hist.databento.com"