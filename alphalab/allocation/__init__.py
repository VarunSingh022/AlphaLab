"""AlphaLab Allocation & Netting Engine.

Intent sizing and netting into ``OrderRequest``, the ``CapitalBudget`` a run
deploys, and the per-order reservation and contribution ledgers (ADR-0015).

Since v3.8, :mod:`~alphalab.allocation.capital` divides capital *between* runs
-- across strategies, markets, brokers, accounts and currencies -- in each
account's own currency, reconciling every account exactly and refusing rather
than silently scaling; it reads committed capital from the reservation ledger
and turns one account's allocation into the budget its run is given
(ADR-0043). A broker and an account are identifiers, never adapters, and FX
reaches the package through a structural protocol rather than an import of
``alphalab.portfolio``.
"""

from alphalab.allocation.allocator import IntentAllocator
from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.capital import (
    CAPITAL_ALLOCATION_SCHEME,
    CAPITAL_PLAN_SCHEME,
    AccountAllocation,
    AllocationRule,
    CapitalAccount,
    CapitalAllocationPlan,
    CapitalAllocationResult,
    CapitalAllocationStatus,
    CapitalConversion,
    CapitalDimension,
    CapitalLimit,
    CapitalPlacement,
    ConversionRecord,
    CurrencyConverter,
    DimensionAllocation,
    EqualWeights,
    FixedAmounts,
    LimitCheck,
    OversubscriptionRule,
    PlacementAllocation,
    PlacementWeights,
    RateRecord,
    allocate_capital,
    capital_budget,
    reserved_capital,
)
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.events import (
    AllocationCompleted,
    AllocationEvent,
    AllocationRejected,
    AllocationStarted,
    BudgetExceeded,
    NettingCompleted,
)
from alphalab.allocation.exceptions import (
    AllocationError,
    AllocationValidationError,
    BudgetExceededError,
    UnknownReservationError,
)
from alphalab.allocation.netting import NettingEngine
from alphalab.allocation.optimizer import AllocationOptimizer
from alphalab.allocation.sizing import (
    EqualWeightSizing,
    FixedDollarSizing,
    FixedQuantitySizing,
    SizingModel,
    TargetWeightSizing,
    VolatilityTargetSizing,
)
from alphalab.allocation.state import AllocationState
from alphalab.allocation.validation import validate_intent, validate_net_quantity
from alphalab.allocation.views import (
    allocation_history,
    current_budget,
    open_reservations,
    recent_orders_for_asset,
    reserved_for_order,
    total_notional_allocated,
)
from alphalab.core.contribution import StrategyContribution, contributions_from
from alphalab.core.order_request import OrderRequest

__all__ = [
    "CAPITAL_ALLOCATION_SCHEME",
    "CAPITAL_PLAN_SCHEME",
    "AccountAllocation",
    "AllocationCompleted",
    "AllocationConstraints",
    "AllocationEngine",
    "AllocationError",
    "AllocationEvent",
    "AllocationOptimizer",
    "AllocationRejected",
    "AllocationRule",
    "AllocationStarted",
    "AllocationState",
    "AllocationValidationError",
    "BudgetExceeded",
    "BudgetExceededError",
    "CapitalAccount",
    "CapitalAllocationPlan",
    "CapitalAllocationResult",
    "CapitalAllocationStatus",
    "CapitalBudget",
    "CapitalConversion",
    "CapitalDimension",
    "CapitalLimit",
    "CapitalPlacement",
    "ConversionRecord",
    "CurrencyConverter",
    "DimensionAllocation",
    "EqualWeightSizing",
    "EqualWeights",
    "FixedAmounts",
    "FixedDollarSizing",
    "FixedQuantitySizing",
    "IntentAllocator",
    "LimitCheck",
    "NettingCompleted",
    "NettingEngine",
    "OrderRequest",
    "OversubscriptionRule",
    "PlacementAllocation",
    "PlacementWeights",
    "RateRecord",
    "SizingModel",
    "StrategyContribution",
    "TargetWeightSizing",
    "UnknownReservationError",
    "VolatilityTargetSizing",
    "allocate_capital",
    "allocation_history",
    "capital_budget",
    "contributions_from",
    "current_budget",
    "open_reservations",
    "recent_orders_for_asset",
    "reserved_capital",
    "reserved_for_order",
    "total_notional_allocated",
    "validate_intent",
    "validate_net_quantity",
]
