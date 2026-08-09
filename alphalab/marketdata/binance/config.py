"""Immutable configuration for the Binance market data provider."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class binanceConfig:
    """Configuration for connecting to Binance's public REST API.

    Attributes:
        provider_id: Identifier for this provider instance within AlphaLab.
        api_key: Binance API key. Not required for the public market-data endpoints
            this client currently uses (klines, book ticker, trades, depth), but
            kept here since authenticated endpoints (account data, order placement)
            would need it if this client's scope ever grows.
        base_url: Binance's public REST API base URL. Previously defaulted to a
            Yahoo Finance URL by mistake -- corrected here.
        timeout_seconds: HTTP request timeout.
    """

    provider_id: str
    api_key: str
    base_url: str = "https://api.binance.com"
    timeout_seconds: float = 10.0
