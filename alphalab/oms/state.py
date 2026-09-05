"""Global immutable state container for OMS."""

from dataclasses import dataclass, field
from typing import Any

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentSet
from alphalab.oms.book import OrderBook
from alphalab.oms.events import OMSEvent
from alphalab.oms.ids import OrderId


@dataclass(frozen=True, slots=True)
class OMSState:
    """Deterministic snapshot of the OMS.

    ``active_orders`` and ``completed_orders`` partition the book's orders by
    whether they are still working. They are
    :class:`~alphalab.common.persistent_map.PersistentSet` rather than
    ``frozenset`` so that moving one order between them costs O(1) instead of
    rebuilding both sets, and so that they iterate in insertion order -- which
    is what makes a serialized snapshot of this state deterministic.
    """

    orders: OrderBook = field(default_factory=OrderBook)
    active_orders: PersistentSet[OrderId] = field(default_factory=PersistentSet)
    completed_orders: PersistentSet[OrderId] = field(default_factory=PersistentSet)
    history: AppendOnlyLog[OMSEvent] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[OMSEvent] = field(default_factory=AppendOnlyLog)

    def __serializable__(self) -> Any:
        """Serialize through the explicit :mod:`alphalab.oms.snapshot` projection.

        The order book keys orders by ``OrderId``, so the state has no direct
        JSON form; :func:`alphalab.oms.snapshot.capture` builds one that is
        complete and restorable. Imported here rather than at module scope
        because the snapshot module needs this class to rebuild a state.
        """

        from alphalab.oms.snapshot import capture

        return capture(self)
