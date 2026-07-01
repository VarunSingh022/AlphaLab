"""Global immutable state container for Market Data Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

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

    # In a real high-frequency system, unbounded history is a leak.
    # Kept as tuple for immutability and test alignment per specs.
    history: tuple[MarketEvent, ...] = field(default_factory=tuple)
    events: tuple[MarketEvent, ...] = field(default_factory=tuple)
