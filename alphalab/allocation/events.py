"""Immutable domain events describing changes in Allocation State."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class AllocationEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class AllocationStarted(AllocationEvent):
    num_intents: int


@dataclass(frozen=True, slots=True)
class AllocationCompleted(AllocationEvent):
    num_orders_generated: int
    total_notional: Decimal


@dataclass(frozen=True, slots=True)
class NettingCompleted(AllocationEvent):
    asset_id: str
    net_quantity: Decimal
    side: str


@dataclass(frozen=True, slots=True)
class BudgetExceeded(AllocationEvent):
    """A batch was refused because it did not fit the remaining budget.

    Attributes:
        reason: Why the batch was refused.
        requested_notional: The batch's own notional.
        available_budget: What was left to commit -- the configured budget
            *minus* capital already committed to unsettled orders. It is not the
            configured budget: reporting that would say a batch of 900,000 was
            refused against 1,000,000, which reads as a contradiction rather
            than an explanation.
    """

    reason: str
    requested_notional: Decimal
    available_budget: Decimal


@dataclass(frozen=True, slots=True)
class AllocationRejected(AllocationEvent):
    reason: str


@dataclass(frozen=True, slots=True)
class AllocationExecutionApplied(AllocationEvent):
    event_id: str
    timestamp: float
    order_id: str
    executed_notional: Decimal


@dataclass(frozen=True, slots=True)
class AllocationReservationReleased(AllocationEvent):
    event_id: str
    timestamp: float
    order_id: str
    released_notional: Decimal
