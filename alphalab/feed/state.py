"""Global immutable state container for the Feed Layer."""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    """Deterministic snapshot of feed connection and subscription states.

    Persistent containers, for the reason given on
    :class:`~alphalab.live.state.LiveState`: a per-message tuple rebuild is
    quadratic in the messages received.
    """

    provider_id: str
    connection: ConnectionSnapshot
    subscriptions: PersistentMap[str, Subscription] = field(default_factory=PersistentMap)
    statistics: FeedStatistics = field(default_factory=FeedStatistics)
    metadata: PersistentMap[str, str] = field(default_factory=PersistentMap)
    events: AppendOnlyLog[FeedEvent] = field(default_factory=AppendOnlyLog)
