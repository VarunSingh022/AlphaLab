"""Immutable configuration for polygon Finance Data Provider."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class polygonConfig:
    provider_id: str
    api_key: str
    base_url: str = "https://api.polygon.io"