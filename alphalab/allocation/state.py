"""Global immutable state container for the Allocation Engine."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.events import AllocationEvent
from alphalab.allocation.request import OrderRequest


@dataclass(frozen=True, slots=True)
class AllocationState:
    """Deterministic snapshot of allocation history and budget utilization."""

    budget: CapitalBudget
    history: tuple[OrderRequest, ...] = field(default_factory=tuple)
    events: tuple[AllocationEvent, ...] = field(default_factory=tuple)
    notional_allocated: Decimal = Decimal("0.00")
