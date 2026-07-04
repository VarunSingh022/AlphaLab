"""Pure queries exposing transparent Market Data State access."""

from collections.abc import Sequence

from alphalab.marketdata.config import ProviderConfig
from alphalab.marketdata.connection import ConnectionState
from alphalab.marketdata.state import MarketDataHealth, MarketDataState, ProviderMetrics
from alphalab.marketdata.subscription import Subscription


def provider_summary(state: MarketDataState) -> Sequence[ProviderConfig]:
    return tuple(state.providers.values())


def connection_status(state: MarketDataState, provider_id: str) -> ConnectionState | None:
    return state.connections.get(provider_id)


def subscription_summary(state: MarketDataState) -> Sequence[Subscription]:
    return tuple(state.subscriptions.values())


def cache_statistics(state: MarketDataState) -> int:
    """Returns the total number of cached historical datasets."""
    return len(state.cache.records)


def market_health(state: MarketDataState) -> MarketDataHealth:
    connected = sum(1 for c in state.connections.values() if c.status.name == "CONNECTED")
    return MarketDataHealth(
        is_healthy=connected > 0, active_providers=connected, failed_providers=0
    )


def provider_metrics(state: MarketDataState, provider_id: str) -> ProviderMetrics | None:
    return state.metrics.get(provider_id)
