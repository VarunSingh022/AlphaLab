"""Global immutable state container for the Allocation Engine."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.events import AllocationEvent
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.contribution import StrategyContribution
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

    ``contributions`` is the parallel ledger of who asked for each order. It is
    keyed and retired exactly like ``reservations``, because it has exactly the
    same lifetime: it exists from the moment allocation emits a request until
    that request's order reaches a terminal state. It is an *index* -- the same
    tuples travel on the emitted ``OrderRequest`` and are recorded in
    ``history`` -- and it exists because ``history`` is an append-only log that
    cannot be looked up by order id without a linear scan. Post-trade
    attribution reads it at fill time, which is what lets a venue fill arriving
    through ``apply_execution_report`` produce the same record a simulated fill
    does. See ADR-0015 decision 5.
    """

    budget: CapitalBudget
    history: AppendOnlyLog[OrderRequest] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[AllocationEvent] = field(default_factory=AppendOnlyLog)
    notional_allocated: Decimal = Decimal("0.00")
    reservations: PersistentMap[str, Decimal] = field(default_factory=PersistentMap)
    contributions: PersistentMap[str, tuple[StrategyContribution, ...]] = field(
        default_factory=PersistentMap
    )
