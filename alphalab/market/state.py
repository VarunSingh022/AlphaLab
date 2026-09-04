"""Global immutable state container for Market Data Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.market.bar import Bar
from alphalab.market.events import MarketEvent
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.tick import Tick


@dataclass(frozen=True, slots=True)
class MarketState:
    """Deterministic snapshot of recent market environment."""

    latest_quotes: Mapping[str, Quote] = field(default_factory=dict)
    latest_books: Mapping[str, OrderBookSnapshot] = field(default_factory=dict)
    latest_ticks: Mapping[str, Tick] = field(default_factory=dict)
    latest_bars: Mapping[str, Bar] = field(default_factory=dict)

    # Unbounded history is still retained; AppendOnlyLog makes growing it
    # O(1) amortized instead of rebuilding a tuple on every publish.
    history: AppendOnlyLog[MarketEvent] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[MarketEvent] = field(default_factory=AppendOnlyLog)
