"""Immutable configuration for nse Finance Data Provider."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class nseConfig:
    provider_id: str
    api_key: str
    base_url: str = "https://www.nseindia.com"
