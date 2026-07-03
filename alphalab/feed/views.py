"""Pure queries exposing transparent Feed State access."""

from collections.abc import Sequence

from alphalab.feed.state import FeedState, FeedStatistics
from alphalab.feed.subscription import Subscription


def active_subscriptions(state: FeedState) -> Sequence[Subscription]:
    """Returns all currently active stream subscriptions."""
    return tuple(s for s in state.subscriptions.values() if s.active)


def connection_status(state: FeedState) -> bool:
    """Returns True if the feed is actively connected."""
    return state.connection.connected


def provider_name(state: FeedState) -> str:
    """Returns the human-readable name of the feed provider."""
    return state.connection.provider_name


def latest_statistics(state: FeedState) -> FeedStatistics:
    """Returns message metrics and error tracking counts."""
    return state.statistics


def current_latency(state: FeedState) -> float:
    """Returns the most recent heartbeat latency observation."""
    return state.connection.latency_ms
