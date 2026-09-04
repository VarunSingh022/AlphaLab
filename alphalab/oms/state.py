"""Global immutable state container for OMS."""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.oms.book import OrderBook
from alphalab.oms.events import OMSEvent
from alphalab.oms.ids import OrderId


@dataclass(frozen=True, slots=True)
class OMSState:
    """Deterministic snapshot of the OMS."""

    orders: OrderBook = field(default_factory=OrderBook)
    active_orders: frozenset[OrderId] = field(default_factory=frozenset)
    completed_orders: frozenset[OrderId] = field(default_factory=frozenset)
    history: AppendOnlyLog[OMSEvent] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[OMSEvent] = field(default_factory=AppendOnlyLog)
