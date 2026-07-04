"""Immutable configuration for Yahoo Finance Data Provider."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class YahooConfig:
    provider_id: str
    api_key: str
    base_url: str = "https://query1.finance.yahoo.com"
