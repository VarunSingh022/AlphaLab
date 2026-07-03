"""Global immutable state container for the Live Market Data framework."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.live.connection import ConnectionState
from alphalab.live.events import LiveEvent
from alphalab.live.provider import Provider
from alphalab.live.snapshot import MarketSnapshot
from alphalab.live.subscription import Subscription


@dataclass(frozen=True, slots=True)
class LiveStatistics:
    """Immutable tracking metrics for the Live Data engine."""

    total_ticks_processed: int = 0
    total_snapshots_updated: int = 0
    total_errors: int = 0


@dataclass(frozen=True, slots=True)
class LiveState:
    """Deterministic snapshot of the entire Live Market Data infrastructure."""

    engine_id: str
    providers: Mapping[str, Provider] = field(default_factory=dict)
    connections: Mapping[str, ConnectionState] = field(default_factory=dict)
    subscriptions: Mapping[str, Subscription] = field(default_factory=dict)
    snapshots: Mapping[str, MarketSnapshot] = field(default_factory=dict)
    statistics: LiveStatistics = field(default_factory=LiveStatistics)
    events: tuple[LiveEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
