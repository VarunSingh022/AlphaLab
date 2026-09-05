"""Global immutable state container for the Allocation Engine."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.events import AllocationEvent
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.order_request import OrderRequest


@dataclass(frozen=True, slots=True)
class AllocationState:
    """Deterministic snapshot of allocation history and budget utilization.

    ``reservations`` is the per-order ledger of capital still held against
    requests that have neither executed nor been released, and
    ``notional_allocated`` is its total. Keeping the ledger, rather than only
    the total, is what makes a release attributable to one order and therefore
    verifiably exactly-once: releasing an order that holds no reservation is an
    error, not a silent subtraction.
    """

    budget: CapitalBudget
    history: AppendOnlyLog[OrderRequest] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[AllocationEvent] = field(default_factory=AppendOnlyLog)
    notional_allocated: Decimal = Decimal("0.00")
    reservations: PersistentMap[str, Decimal] = field(default_factory=PersistentMap)
