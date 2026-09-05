"""Global immutable state container for the Live Market Data framework."""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    """Deterministic snapshot of the entire Live Market Data infrastructure.

    Persistent containers for the same reason the canonical market state uses
    them: growing a ``tuple`` of events per tick copies O(N^2) elements, which
    is what made ``benchmarks/benchmark_live.py`` take minutes to route 100k
    ticks.
    """

    engine_id: str
    providers: PersistentMap[str, Provider] = field(default_factory=PersistentMap)
    connections: PersistentMap[str, ConnectionState] = field(default_factory=PersistentMap)
    subscriptions: PersistentMap[str, Subscription] = field(default_factory=PersistentMap)
    snapshots: PersistentMap[str, MarketSnapshot] = field(default_factory=PersistentMap)
    statistics: LiveStatistics = field(default_factory=LiveStatistics)
    events: AppendOnlyLog[LiveEvent] = field(default_factory=AppendOnlyLog)
    metadata: PersistentMap[str, str] = field(default_factory=PersistentMap)
