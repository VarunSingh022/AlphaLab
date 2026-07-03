"""Global immutable state container for the Feed Layer."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.feed.connection import ConnectionSnapshot
from alphalab.feed.events import FeedEvent
from alphalab.feed.subscription import Subscription


@dataclass(frozen=True, slots=True)
class FeedStatistics:
    """Immutable tracking metrics for the provider."""

    messages_received: int = 0
    bytes_received: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class FeedState:
    """Deterministic snapshot of feed connection and subscription states."""

    provider_id: str
    connection: ConnectionSnapshot
    subscriptions: Mapping[str, Subscription] = field(default_factory=dict)
    statistics: FeedStatistics = field(default_factory=FeedStatistics)
    metadata: Mapping[str, str] = field(default_factory=dict)
    events: tuple[FeedEvent, ...] = field(default_factory=tuple)
