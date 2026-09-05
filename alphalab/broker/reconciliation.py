"""Making AlphaLab's belief about a venue and the venue's own records agree.

Everything in this module exists because a broker connection is unreliable in
specific, known ways: fills get redelivered after a reconnect, they arrive in a
different order than they happened, they reference orders this process never
sent, and a cancel and a fill can cross in flight. None of those are exceptional
-- they are the normal behaviour of a network -- so each one gets a defined,
deterministic answer rather than an exception at the point of surprise.

Identity
--------

Three identifiers describe one order, and conflating them is what makes
reconciliation impossible:

============== =========================================================
oms_order_id   AlphaLab's order. Survives everything.
broker_order_id The handle AlphaLab addresses the order by at the venue.
external_id    The venue's own identifier for a *fill*, preserved verbatim
               on :class:`~alphalab.broker.execution.BrokerExecution` so a
               report can be traced back to the venue's records.
============== =========================================================

:class:`ExternalOrderMap` holds the first two as a bidirectional mapping, and
refuses to overwrite either direction: silently rebinding an id is how one
order's fills end up on another order.

Applying a fill
---------------

:func:`classify_execution` decides what a fill is before anything is changed,
and :func:`apply_execution` acts on that decision. The classification is total
-- every fill gets exactly one :class:`ExecutionOutcome` -- so nothing is
silently dropped:

==================== =====================================================
APPLIED              Applied to the order, account and position.
DUPLICATE            Already applied, by ``execution_id``. Ignored, and the
                     state is returned unchanged. This is the redelivery
                     case, and it must be a no-op rather than an error
                     because a reconnect makes it routine.
UNKNOWN_ORDER        References an order this state has never seen. Never
                     applied, always surfaced: it may belong to another
                     session, or AlphaLab may have lost an order it sent.
TERMINAL_ORDER       The order can never fill again. Recorded as a break,
                     not applied -- see the cancel/fill race below.
OVERFILL             Would fill more than was ordered. Never applied.
INVALID              Non-positive quantity, or negative price/commission.
==================== =====================================================

Out-of-order fills
------------------

Fills are additive -- a quantity and a volume-weighted price -- so applying two
fills for one order in either order produces the same order, position and cash.
That is a property, not a coincidence, and
``tests/regression/test_broker_reconciliation.py`` holds it. Out-of-order
delivery therefore needs no special handling, which is why there is no
``STALE`` outcome: reordering fills does not change the answer.

The cancel/fill race
--------------------

A cancel and a fill can cross. Both orders of arrival are defined:

* **Fill first, then cancel.** The order reaches FILLED. The cancel is refused
  by :func:`~alphalab.broker.validation.validate_cancel_request`, because a
  filled order has nothing left to cancel.
* **Cancel first, then fill.** The order is already terminal. The fill is *not*
  applied and *not* discarded: it is classified TERMINAL_ORDER and appended to
  :attr:`ReconciliationLog.breaks`. Applying it would resurrect a terminal
  order; dropping it would hide a real position the venue believes AlphaLab
  holds. Surfacing it is the only honest answer, and the caller resolves it with
  a :func:`reconcile` against the venue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum, auto

from alphalab.broker.account import BrokerAccount
from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.broker.state import BrokerState
from alphalab.core.enums import OrderStatus as CoreOrderStatus

__all__ = [
    "ExecutionDecision",
    "ExecutionOutcome",
    "ExternalOrderMap",
    "OrderDivergence",
    "PositionDivergence",
    "ReconciliationLog",
    "ReconciliationReport",
    "apply_execution",
    "classify_execution",
    "reconcile",
]


class ExecutionOutcome(Enum):
    """What a reported fill turned out to be."""

    APPLIED = auto()
    DUPLICATE = auto()
    UNKNOWN_ORDER = auto()
    TERMINAL_ORDER = auto()
    OVERFILL = auto()
    INVALID = auto()


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    """The classification of one reported fill, and why."""

    outcome: ExecutionOutcome
    execution: BrokerExecution
    reason: str

    @property
    def applied(self) -> bool:
        """Whether this fill changed any state."""

        return self.outcome is ExecutionOutcome.APPLIED

    @property
    def is_break(self) -> bool:
        """Whether this fill needs a human or a reconcile to resolve.

        A duplicate is not a break -- redelivery is expected. Everything else
        that was refused is, because it means AlphaLab and the venue disagree.
        """

        return self.outcome not in {ExecutionOutcome.APPLIED, ExecutionOutcome.DUPLICATE}


@dataclass(frozen=True, slots=True)
class ReconciliationLog:
    """Every fill that was refused, kept so none is lost.

    A refused fill is information, not an error to swallow: it is the evidence
    that AlphaLab and the venue disagree, and it is the input to the next
    :func:`reconcile`.
    """

    breaks: tuple[ExecutionDecision, ...] = field(default_factory=tuple)
    duplicates: tuple[ExecutionDecision, ...] = field(default_factory=tuple)

    def record(self, decision: ExecutionDecision) -> ReconciliationLog:
        """Return a log with ``decision`` recorded in the right place."""

        if decision.outcome is ExecutionOutcome.DUPLICATE:
            return replace(self, duplicates=(*self.duplicates, decision))
        if decision.is_break:
            return replace(self, breaks=(*self.breaks, decision))
        return self


@dataclass(frozen=True, slots=True)
class ExternalOrderMap:
    """Bidirectional ``oms_order_id`` <-> ``broker_order_id`` mapping.

    Rebinding either direction is refused. A venue reusing a handle, or AlphaLab
    sending one OMS order under two handles, is a defect that must surface here
    rather than as one order's fills landing on another.
    """

    to_broker: Mapping[str, str] = field(default_factory=dict)
    to_oms: Mapping[str, str] = field(default_factory=dict)

    def bind(self, oms_order_id: str, broker_order_id: str) -> ExternalOrderMap:
        """Record that ``oms_order_id`` is known to the venue as ``broker_order_id``."""

        if not oms_order_id or not broker_order_id:
            raise BrokerValidationError("Both identifiers are required to bind an order.")

        existing_broker = self.to_broker.get(oms_order_id)
        if existing_broker is not None and existing_broker != broker_order_id:
            raise BrokerValidationError(
                f"OMS order {oms_order_id} is already bound to broker order "
                f"{existing_broker}; refusing to rebind it to {broker_order_id}."
            )
        existing_oms = self.to_oms.get(broker_order_id)
        if existing_oms is not None and existing_oms != oms_order_id:
            raise BrokerValidationError(
                f"Broker order {broker_order_id} is already bound to OMS order "
                f"{existing_oms}; refusing to rebind it to {oms_order_id}."
            )

        return ExternalOrderMap(
            {**self.to_broker, oms_order_id: broker_order_id},
            {**self.to_oms, broker_order_id: oms_order_id},
        )

    def bind_order(self, order: BrokerOrder) -> ExternalOrderMap:
        """Bind the two identifiers an order already carries."""

        return self.bind(order.oms_order_id, order.broker_order_id)

    def broker_id_for(self, oms_order_id: str) -> str | None:
        """The venue's handle for an OMS order, if it has one."""

        return self.to_broker.get(oms_order_id)

    def oms_id_for(self, broker_order_id: str) -> str | None:
        """The OMS order a venue handle refers to, if it is known."""

        return self.to_oms.get(broker_order_id)


def classify_execution(state: BrokerState, execution: BrokerExecution) -> ExecutionDecision:
    """Decide what a reported fill is, without changing anything.

    Pure and total: every fill gets exactly one outcome, so a caller can log,
    alert on or replay the classification without having applied it.
    """

    if execution.execution_id in state.executions:
        return ExecutionDecision(
            ExecutionOutcome.DUPLICATE,
            execution,
            f"Execution {execution.execution_id} was already applied.",
        )

    if execution.fill_quantity <= Decimal("0"):
        return ExecutionDecision(
            ExecutionOutcome.INVALID,
            execution,
            f"Fill quantity must be positive, got {execution.fill_quantity}.",
        )
    if execution.fill_price < Decimal("0"):
        return ExecutionDecision(
            ExecutionOutcome.INVALID,
            execution,
            f"Fill price cannot be negative, got {execution.fill_price}.",
        )
    if execution.commission < Decimal("0"):
        return ExecutionDecision(
            ExecutionOutcome.INVALID,
            execution,
            f"Commission cannot be negative, got {execution.commission}.",
        )

    order = state.orders.get(execution.broker_order_id)
    if order is None:
        return ExecutionDecision(
            ExecutionOutcome.UNKNOWN_ORDER,
            execution,
            f"Execution {execution.execution_id} references unknown broker order "
            f"{execution.broker_order_id}.",
        )

    if order.is_terminal:
        return ExecutionDecision(
            ExecutionOutcome.TERMINAL_ORDER,
            execution,
            f"Order {order.broker_order_id} is terminal ({order.status}); a fill "
            f"reported against it means AlphaLab and the venue disagree.",
        )

    if order.filled_quantity + execution.fill_quantity > order.quantity:
        return ExecutionDecision(
            ExecutionOutcome.OVERFILL,
            execution,
            f"Fill of {execution.fill_quantity} would take order "
            f"{order.broker_order_id} to {order.filled_quantity + execution.fill_quantity} "
            f"against an ordered quantity of {order.quantity}.",
        )

    return ExecutionDecision(ExecutionOutcome.APPLIED, execution, "")


def apply_execution(
    state: BrokerState,
    execution: BrokerExecution,
    log: ReconciliationLog | None = None,
) -> tuple[BrokerState, ExecutionDecision, ReconciliationLog]:
    """Apply a reported fill to the order it belongs to, if it may be applied.

    Returns the next state, the decision, and the log with the decision
    recorded. A refused fill leaves the state untouched -- so calling this with
    the same fill twice is safe, which is the whole point.
    """

    current_log = log if log is not None else ReconciliationLog()
    decision = classify_execution(state, execution)
    if not decision.applied:
        return state, decision, current_log.record(decision)

    order = state.orders[execution.broker_order_id]
    filled = order.filled_quantity + execution.fill_quantity
    cost = (order.filled_quantity * order.average_fill_price) + (
        execution.fill_quantity * execution.fill_price
    )
    updated = replace(
        order,
        filled_quantity=filled,
        average_fill_price=cost / filled,
        status=(
            CoreOrderStatus.FILLED if filled == order.quantity else CoreOrderStatus.PARTIALLY_FILLED
        ),
        updated_at=max(order.updated_at, execution.timestamp),
    )

    return (
        replace(
            state,
            orders={**state.orders, order.broker_order_id: updated},
            executions={**state.executions, execution.execution_id: execution},
        ),
        decision,
        current_log.record(decision),
    )


@dataclass(frozen=True, slots=True)
class OrderDivergence:
    """One order AlphaLab and the venue describe differently."""

    broker_order_id: str
    local: BrokerOrder | None
    remote: BrokerOrder | None
    reason: str


@dataclass(frozen=True, slots=True)
class PositionDivergence:
    """One position AlphaLab and the venue size differently."""

    symbol: str
    local_quantity: Decimal
    remote_quantity: Decimal


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Everything AlphaLab and the venue do not agree on.

    An empty report is the only proof that local state is trustworthy. Each
    field answers a different question, because each needs a different fix:
    a missing order may need re-sending, an unknown one may belong to another
    session, and a divergent fill quantity means a fill was lost in flight.
    """

    missing_at_broker: tuple[OrderDivergence, ...] = field(default_factory=tuple)
    unknown_locally: tuple[OrderDivergence, ...] = field(default_factory=tuple)
    divergent_orders: tuple[OrderDivergence, ...] = field(default_factory=tuple)
    divergent_positions: tuple[PositionDivergence, ...] = field(default_factory=tuple)
    cash_difference: Decimal = field(default=Decimal("0"))

    @property
    def reconciled(self) -> bool:
        """Whether local state matches the venue in every respect checked."""

        return not (
            self.missing_at_broker
            or self.unknown_locally
            or self.divergent_orders
            or self.divergent_positions
            or self.cash_difference != Decimal("0")
        )


def reconcile(
    state: BrokerState,
    remote_orders: Sequence[BrokerOrder],
    remote_positions: Sequence[BrokerPosition] = (),
    remote_account: BrokerAccount | None = None,
) -> ReconciliationReport:
    """Compare what AlphaLab believes against what the venue reports.

    The venue is the authority; this function does not resolve differences, it
    states them. Deciding what to do about a missing order is a policy question
    with no safe default -- re-sending one that actually exists would duplicate
    it -- so that decision stays with the caller.
    """

    remote_by_id = {order.broker_order_id: order for order in remote_orders}

    missing = tuple(
        OrderDivergence(order_id, order, None, "Order is known locally but not at the broker.")
        for order_id, order in state.orders.items()
        if order_id not in remote_by_id and not order.is_terminal
    )
    unknown = tuple(
        OrderDivergence(order_id, None, order, "Broker reports an order AlphaLab does not know.")
        for order_id, order in remote_by_id.items()
        if order_id not in state.orders
    )

    divergent: list[OrderDivergence] = []
    for order_id, local in state.orders.items():
        remote = remote_by_id.get(order_id)
        if remote is None:
            continue
        if local.filled_quantity != remote.filled_quantity:
            divergent.append(
                OrderDivergence(
                    order_id,
                    local,
                    remote,
                    f"Filled quantity differs: local {local.filled_quantity}, "
                    f"broker {remote.filled_quantity}.",
                )
            )
        elif local.status != remote.status:
            divergent.append(
                OrderDivergence(
                    order_id,
                    local,
                    remote,
                    f"Status differs: local {local.status}, broker {remote.status}.",
                )
            )

    remote_quantities = {position.symbol: position.quantity for position in remote_positions}
    symbols = sorted({*state.positions, *remote_quantities})
    positions = tuple(
        PositionDivergence(symbol, local_quantity, remote_quantity)
        for symbol in symbols
        for local_quantity in (
            state.positions[symbol].quantity if symbol in state.positions else Decimal("0"),
        )
        for remote_quantity in (remote_quantities.get(symbol, Decimal("0")),)
        if local_quantity != remote_quantity
    )

    cash_difference = (
        Decimal("0") if remote_account is None else remote_account.cash - state.account.cash
    )

    return ReconciliationReport(missing, unknown, tuple(divergent), positions, cash_difference)
