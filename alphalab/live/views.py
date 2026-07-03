"""Pure queries exposing transparent Live State access."""

from collections.abc import Sequence

from alphalab.live.connection import ConnectionState
from alphalab.live.provider import Provider
from alphalab.live.snapshot import MarketSnapshot
from alphalab.live.state import LiveState, LiveStatistics
from alphalab.live.subscription import Subscription


def active_providers(state: LiveState) -> Sequence[Provider]:
    """Returns all registered providers in the system."""
    return tuple(state.providers.values())


def connection_status(state: LiveState, provider_id: str) -> ConnectionState | None:
    """Returns the current connection metrics for a specific provider."""
    return state.connections.get(provider_id)


def list_subscriptions(state: LiveState) -> Sequence[Subscription]:
    """Returns all active subscriptions across all providers."""
    return tuple(sub for sub in state.subscriptions.values() if sub.active)


def latest_snapshot(state: LiveState, symbol: str) -> MarketSnapshot | None:
    """Returns the most recent aggregated market data for a symbol."""
    return state.snapshots.get(symbol)


def engine_statistics(state: LiveState) -> LiveStatistics:
    """Returns the global processing metrics for the live feed."""
    return state.statistics
