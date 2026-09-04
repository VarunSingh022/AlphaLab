"""Global immutable state container for the Allocation Engine."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.events import AllocationEvent
from alphalab.common.append_log import AppendOnlyLog
from alphalab.core.order_request import OrderRequest


@dataclass(frozen=True, slots=True)
class AllocationState:
    """Deterministic snapshot of allocation history and budget utilization."""

    budget: CapitalBudget
    history: AppendOnlyLog[OrderRequest] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[AllocationEvent] = field(default_factory=AppendOnlyLog)
    notional_allocated: Decimal = Decimal("0.00")
