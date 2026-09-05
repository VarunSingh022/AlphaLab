"""Global immutable state container for Market Data Engine."""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.market.bar import Bar
from alphalab.market.events import MarketEvent
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.tick import Tick


@dataclass(frozen=True, slots=True)
class MarketState:
    """Deterministic snapshot of recent market environment.

    The ``latest_*`` indexes are
    :class:`~alphalab.common.persistent_map.PersistentMap` rather than ``dict``
    because publishing rebuilt the whole index on every update: one quote cost
    O(universe), so ingesting a wide universe slowed down in proportion to how
    wide it was. Writing to a persistent map is O(1) amortized, and it iterates
    in insertion order, so a serialized snapshot of this state stays
    deterministic.
    """

    latest_quotes: PersistentMap[str, Quote] = field(default_factory=PersistentMap)
    latest_books: PersistentMap[str, OrderBookSnapshot] = field(default_factory=PersistentMap)
    latest_ticks: PersistentMap[str, Tick] = field(default_factory=PersistentMap)
    latest_bars: PersistentMap[str, Bar] = field(default_factory=PersistentMap)

    # Unbounded history is still retained; AppendOnlyLog makes growing it
    # O(1) amortized instead of rebuilding a tuple on every publish.
    history: AppendOnlyLog[MarketEvent] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[MarketEvent] = field(default_factory=AppendOnlyLog)
