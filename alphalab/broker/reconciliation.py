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

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum, StrEnum, auto, unique
from typing import Final

from alphalab.broker.account import BrokerAccount
from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import (
    BROKER_STATUS_EQUIVALENTS,
    BrokerOrder,
    BrokerOrderStatus,
    canonical_status,
)
from alphalab.broker.position import BrokerPosition
from alphalab.broker.state import BrokerState
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, canonical_text, plain
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus as CoreOrderStatus

__all__ = [
    "SNAPSHOT_RECONCILIATION_SCHEME",
    "VENUE_SNAPSHOT_SCHEME",
    "ExecutionDecision",
    "ExecutionOutcome",
    "ExternalOrderMap",
    "OrderDivergence",
    "PositionDivergence",
    "ReconciliationLog",
    "ReconciliationReport",
    "SnapshotDivergence",
    "SnapshotDivergenceKind",
    "SnapshotFreshness",
    "SnapshotReconciliation",
    "VenueSnapshot",
    "apply_execution",
    "classify_execution",
    "reconcile",
    "reconcile_snapshot",
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

    Both histories are :class:`~alphalab.common.append_log.AppendOnlyLog`s as of
    v3.10, so recording one is O(1). They were tuples rebuilt on every record,
    which made a session that saw ``n`` redeliveries do ``O(n**2)`` copying. A
    tuple passed to the constructor is accepted and adopted.
    """

    breaks: AppendOnlyLog[ExecutionDecision] = field(default_factory=AppendOnlyLog)
    duplicates: AppendOnlyLog[ExecutionDecision] = field(default_factory=AppendOnlyLog)

    def __post_init__(self) -> None:
        for name in ("breaks", "duplicates"):
            value = getattr(self, name)
            if not isinstance(value, AppendOnlyLog):
                object.__setattr__(self, name, AppendOnlyLog(value))

    def record(self, decision: ExecutionDecision) -> ReconciliationLog:
        """Return a log with ``decision`` recorded in the right place."""

        if decision.outcome is ExecutionOutcome.DUPLICATE:
            return replace(self, duplicates=self.duplicates.append(decision))
        if decision.is_break:
            return replace(self, breaks=self.breaks.append(decision))
        return self


@dataclass(frozen=True, slots=True)
class ExternalOrderMap:
    """Bidirectional ``oms_order_id`` <-> ``broker_order_id`` mapping.

    Rebinding either direction is refused. A venue reusing a handle, or AlphaLab
    sending one OMS order under two handles, is a defect that must surface here
    rather than as one order's fills landing on another.

    Both directions are :class:`~alphalab.common.persistent_map.PersistentMap`s
    as of v3.10, so a bind is O(1) amortized; until then each bind copied both
    dictionaries, which is quadratic over a session. A plain mapping passed to
    the constructor is accepted and adopted.
    """

    to_broker: PersistentMap[str, str] = field(default_factory=PersistentMap)
    to_oms: PersistentMap[str, str] = field(default_factory=PersistentMap)

    def __post_init__(self) -> None:
        for name in ("to_broker", "to_oms"):
            value = getattr(self, name)
            if not isinstance(value, PersistentMap):
                object.__setattr__(self, name, PersistentMap(value))

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

        if existing_broker is not None:
            return self  # already bound, identically
        return ExternalOrderMap(
            self.to_broker.set(oms_order_id, broker_order_id),
            self.to_oms.set(broker_order_id, oms_order_id),
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
    # A price's sign is the instrument's question, and a commission is signed --
    # a negative one is a rebate -- since v3.11 (ACC-007).
    if not execution.fill_price.is_finite():
        return ExecutionDecision(
            ExecutionOutcome.INVALID,
            execution,
            f"Fill price must be a finite number, got {execution.fill_price}.",
        )
    if not execution.commission.is_finite():
        return ExecutionDecision(
            ExecutionOutcome.INVALID,
            execution,
            f"Commission must be a finite number, got {execution.commission}.",
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


def _status_after_fill(order: BrokerOrder, filled: Decimal) -> CoreOrderStatus | BrokerOrderStatus:
    """Where a fill leaves an order: complete, still working, or still cancelling.

    A fill that completes the order makes it ``FILLED``. One that does not leaves
    it ``PARTIALLY_FILLED`` -- unless a cancel is in flight, which a partial fill
    does not answer: the order stays ``PENDING_CANCEL`` (or ``CANCEL_PENDING``),
    the row :data:`~alphalab.core.lifecycle.ORDER_TRANSITIONS` states. Until v3.9
    a partial fill dropped the pending marker, and a mirror that forgot a cancel
    was in flight would issue a second one.
    """

    if filled == order.quantity:
        return CoreOrderStatus.FILLED
    if canonical_status(order.status) is CoreOrderStatus.CANCEL_PENDING:
        return order.status
    return CoreOrderStatus.PARTIALLY_FILLED


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
    # The volume-weighted average in the pinned accounting context (ACC-004).
    ctx = ACCOUNTING_CONTEXT
    filled = ctx.add(order.filled_quantity, execution.fill_quantity)
    cost = ctx.add(
        ctx.multiply(order.filled_quantity, order.average_fill_price),
        ctx.multiply(execution.fill_quantity, execution.fill_price),
    )
    updated = replace(
        order,
        filled_quantity=filled,
        average_fill_price=plain(ctx.divide(cost, filled)),
        status=_status_after_fill(order, filled),
        updated_at=max(order.updated_at, execution.timestamp),
    )

    return (
        replace(
            state,
            orders=state.orders.set(order.broker_order_id, updated),
            executions=state.executions.set(execution.execution_id, execution),
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

    Two inputs are refused rather than compared, since v3.9. Until then two
    remote records for one order or one symbol were collapsed silently, the
    last one winning, and a venue account in another currency was subtracted
    from the local cash as though the two were one unit. A
    :class:`ReconciliationReport` has no field that could say either, so both
    raise; :func:`reconcile_snapshot` compares the same pair and reports
    duplicated evidence as a divergence instead.

    Raises:
        BrokerValidationError: If two remote orders share a ``broker_order_id``,
            two remote positions share a symbol, or ``remote_account`` is
            denominated in a currency other than the local account's.
    """

    _refuse_duplicates("order", [order.broker_order_id for order in remote_orders])
    _refuse_duplicates("position", [position.symbol for position in remote_positions])
    if remote_account is not None and remote_account.currency != state.account.currency:
        raise BrokerValidationError(
            f"The venue account is denominated in {remote_account.currency} and the mirror's "
            f"in {state.account.currency}. Their cash is not one quantity, and a difference "
            "between them would be a number with no unit."
        )

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


def _refuse_duplicates(noun: str, keys: Sequence[str]) -> None:
    repeated = sorted({key for key, count in Counter(keys).items() if count > 1})
    if repeated:
        raise BrokerValidationError(
            f"The venue reported more than one {noun} for {repeated}. One record per key is "
            f"the contract; choosing one of them would be a guess. reconcile_snapshot reports "
            "duplicated evidence instead of refusing it."
        )


# --------------------------------------------------------------------------- #
# v3.9: reconciling against a whole venue snapshot
# --------------------------------------------------------------------------- #
#
# ``reconcile`` above compares the mirror against loose remote records and has
# answered one question since v2.3: which orders, positions and cash differ. A
# venue snapshot is those records with two more facts attached -- *when* the
# venue produced them, and *everything* it reported -- and each fact makes a
# question answerable that ``reconcile`` cannot ask:
#
# * **Is the evidence current?** A snapshot taken before the mirror last
#   changed describes a venue the mirror has since heard more from, and every
#   difference it shows may be the mirror being right. So it is not compared.
# * **Is the evidence consistent with itself?** Two records for one order are a
#   venue (or an adapter) contradicting itself, and choosing one is a guess.
# * **Does every compared field agree?** Ordered quantity, price, fill, status,
#   each fill, each position and every balance, each reported separately.
#
# Same pair, same authority, richer evidence: this is the mirror-to-venue
# reconciliation ADR-0012 defined, extended, and it is pinned beside
# ``reconcile`` in ``test_shared_names_stay_distinct.py``.

#: Version 2 (v3.10) renders every amount in its canonical form, so equal
#: evidence has one identity whatever exponent each number arrived with. Version
#: 1 rendered ``str(Decimal)``: ``100`` and ``100.00`` gave two (ledger BRK-001).
VENUE_SNAPSHOT_SCHEME: Final = "alphalab.venue_snapshot.v2"
SNAPSHOT_RECONCILIATION_SCHEME: Final = "alphalab.snapshot_reconciliation.v2"


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _amount(value: Decimal | None) -> str:
    """One text per number (:func:`~alphalab.common.arithmetic.canonical_text`), or ``None``."""

    if value is None:
        return "None"
    return canonical_text(value)


def _render_order(order: BrokerOrder) -> str:
    return (
        f"order={order.broker_order_id!r}|oms={order.oms_order_id!r}|symbol={order.symbol!r}"
        f"|side={order.side}|type={order.order_type}|quantity={_amount(order.quantity)}"
        f"|price={_amount(order.price)}|filled={_amount(order.filled_quantity)}"
        f"|average={_amount(order.average_fill_price)}|status={order.status!s}"
        f"|created={order.created_at!r}|updated={order.updated_at!r}"
        f"|account={order.account_id!r}|tif={order.tif}|stop={_amount(order.stop_price)}"
    )


def _render_execution(execution: BrokerExecution) -> str:
    return (
        f"execution={execution.execution_id!r}|order={execution.broker_order_id!r}"
        f"|symbol={execution.symbol!r}|quantity={_amount(execution.fill_quantity)}"
        f"|price={_amount(execution.fill_price)}|commission={_amount(execution.commission)}"
        f"|at={execution.timestamp!r}|account={execution.account_id!r}"
        f"|external={execution.external_id!r}"
    )


def _render_position(position: BrokerPosition) -> str:
    return (
        f"position={position.symbol!r}|quantity={_amount(position.quantity)}"
        f"|average={_amount(position.average_price)}|value={_amount(position.market_value)}"
        f"|unrealized={_amount(position.unrealized_pnl)}"
        f"|realized={_amount(position.realized_pnl)}"
        f"|account={position.account_id!r}|class={position.asset_class}"
        f"|mark={_amount(position.market_price)}"
    )


def _render_account(account: BrokerAccount) -> str:
    return (
        f"account={account.account_id!r}|currency={account.currency!r}"
        f"|cash={_amount(account.cash)}|equity={_amount(account.equity)}"
        f"|buying_power={_amount(account.buying_power)}|margin={_amount(account.margin)}"
        f"|available={_amount(account.available_funds)}|broker={account.broker_id!r}"
    )


@dataclass(frozen=True, slots=True)
class VenueSnapshot:
    """Everything a venue reported about one account, at one instant.

    Attributes:
        as_of: When the venue produced the snapshot, in Unix seconds, in the
            clock the mirror is stamped in.
        orders: Every order the venue reports. A finished order a venue has
            purged may be absent; a working one may not.
        executions: Every fill the venue reports for the orders above, or
            ``None`` when it reported no fill list -- in which case fills are
            not compared, and the result says so. An empty tuple is the venue
            saying there are none.
        positions: Every position, or ``None`` when none were reported. Empty
            is the venue saying the account holds nothing.
        account: The account's balances, or ``None`` when not reported.

    Duplicates are kept, not collapsed: they are evidence, and
    :func:`reconcile_snapshot` reports them.
    """

    as_of: float
    orders: tuple[BrokerOrder, ...]
    executions: tuple[BrokerExecution, ...] | None
    positions: tuple[BrokerPosition, ...] | None
    account: BrokerAccount | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.as_of):
            raise BrokerValidationError(f"A snapshot as of {self.as_of!r} has no instant.")

    @property
    def snapshot_id(self) -> str:
        """SHA-256 over every record, sorted, duplicates included.

        The same evidence identifies itself the same way whatever order the
        adapter listed it in, so a repeated snapshot is recognisable as one.
        """

        return _digest(
            [
                VENUE_SNAPSHOT_SCHEME,
                f"as_of={self.as_of!r}",
                *sorted(_render_order(order) for order in self.orders),
                (
                    "executions=none"
                    if self.executions is None
                    else f"executions={len(self.executions)}"
                ),
                *sorted(_render_execution(execution) for execution in self.executions or ()),
                "positions=none" if self.positions is None else f"positions={len(self.positions)}",
                *sorted(_render_position(position) for position in self.positions or ()),
                "account=none" if self.account is None else _render_account(self.account),
            ]
        )


@unique
class SnapshotFreshness(StrEnum):
    """Whether a snapshot is fit to be compared at all."""

    #: Taken no earlier than the mirror's newest change, and within the stated age.
    CURRENT = "current"

    #: Taken before the mirror last changed. The mirror has heard more since.
    PREDATES_MIRROR = "predates_mirror"

    #: Older, at the evaluation instant, than the maximum age stated.
    TOO_OLD = "too_old"

    #: Dated after the evaluation instant: a look-ahead.
    FROM_THE_FUTURE = "from_the_future"


@unique
class SnapshotDivergenceKind(StrEnum):
    """The ways the mirror and a venue snapshot can disagree, each needing its own fix."""

    #: The mirror holds a working order the venue does not report.
    MISSING_ORDER = "missing_order"

    #: The venue reports an order the mirror does not hold.
    UNKNOWN_ORDER = "unknown_order"

    #: One order, two ordered quantities.
    ORDER_QUANTITY = "order_quantity"

    #: One order, two prices.
    ORDER_PRICE = "order_price"

    #: One order, two filled quantities: a fill reached one side only.
    ORDER_FILLED_QUANTITY = "order_filled_quantity"

    #: One order, two statuses that cannot both be true.
    ORDER_STATUS = "order_status"

    #: The mirror applied a fill the venue does not report.
    MISSING_EXECUTION = "missing_execution"

    #: The venue reports a fill the mirror has not applied.
    UNKNOWN_EXECUTION = "unknown_execution"

    #: One fill, two quantities, prices, commissions or orders.
    EXECUTION_MISMATCH = "execution_mismatch"

    #: One instrument, two position sizes -- including a position one side
    #: does not hold at all.
    POSITION_QUANTITY = "position_quantity"

    #: One balance, two amounts.
    BALANCE = "balance"

    #: Two different accounts, or one account in two currencies.
    ACCOUNT_IDENTITY = "account_identity"

    #: The snapshot reports one key more than once. Not compared.
    DUPLICATE_EVIDENCE = "duplicate_evidence"


@dataclass(frozen=True, slots=True)
class SnapshotDivergence:
    """One disagreement, with both sides rendered.

    Attributes:
        kind: Which kind.
        key: What it is about: a ``broker_order_id``, an ``execution_id``, a
            symbol, a balance name.
        local: What the mirror holds, rendered, or ``None`` when it holds nothing.
        remote: What the venue reports, rendered, or ``None``.
        reason: One sentence.
    """

    kind: SnapshotDivergenceKind
    key: str
    local: str | None
    remote: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class SnapshotReconciliation:
    """Everything a mirror and a venue snapshot disagree about, and what was not compared.

    Attributes:
        snapshot_id: The snapshot's identity.
        as_of: When the snapshot was taken.
        evaluated_at: The instant freshness was judged at.
        max_age_seconds: The age budget it was judged against.
        freshness: Whether it was fit to compare. Nothing is compared unless it
            is ``CURRENT``.
        divergences: In :class:`SnapshotDivergenceKind` declaration order, then
            by key. Empty when the snapshot was not compared.
        unexamined: What the snapshot did not report, so could not be compared.
        compared_orders: Orders compared field by field.
        compared_executions: Fills compared field by field.
        compared_positions: Instruments whose positions were compared.
    """

    snapshot_id: str
    as_of: float
    evaluated_at: float
    max_age_seconds: float
    freshness: SnapshotFreshness
    divergences: tuple[SnapshotDivergence, ...]
    unexamined: tuple[str, ...]
    compared_orders: int
    compared_executions: int
    compared_positions: int

    @property
    def reconciled(self) -> bool:
        """Whether a current snapshot agrees with the mirror in everything compared.

        A stale snapshot is never reconciled, whatever it contains: it was not
        compared, and not having looked is not agreeing.
        """

        return self.freshness is SnapshotFreshness.CURRENT and not self.divergences

    @property
    def fully_reconciled(self) -> bool:
        """Reconciled, and nothing left unexamined."""

        return self.reconciled and not self.unexamined

    def of(self, kind: SnapshotDivergenceKind) -> tuple[SnapshotDivergence, ...]:
        """Every divergence of one kind, in order."""

        return tuple(divergence for divergence in self.divergences if divergence.kind is kind)

    @property
    def reconciliation_id(self) -> str:
        """SHA-256 over the snapshot, the judgement and every divergence."""

        return _digest(
            [
                SNAPSHOT_RECONCILIATION_SCHEME,
                f"snapshot={self.snapshot_id}",
                f"evaluated_at={self.evaluated_at!r}",
                f"max_age_seconds={self.max_age_seconds!r}",
                f"freshness={self.freshness}",
                f"unexamined={','.join(self.unexamined)}",
                f"compared={self.compared_orders},{self.compared_executions},"
                f"{self.compared_positions}",
                *(
                    f"divergence={d.kind}|{d.key!r}|{d.local!r}|{d.remote!r}"
                    for d in self.divergences
                ),
            ]
        )


def _duplicated(keys: Sequence[str]) -> Mapping[str, int]:
    """Every key that occurs more than once, with how often. One pass."""

    return {key: count for key, count in Counter(keys).items() if count > 1}


def _mirror_high_water(state: BrokerState) -> float | None:
    """The newest instant the mirror has recorded anything at."""

    instants = [order.updated_at for order in state.orders.values()]
    instants.extend(execution.timestamp for execution in state.executions.values())
    return max(instants) if instants else None


def _statuses_consistent(
    local: CoreOrderStatus | BrokerOrderStatus, remote: CoreOrderStatus | BrokerOrderStatus
) -> bool:
    if local == remote:
        return True
    if isinstance(local, BrokerOrderStatus) and not isinstance(remote, BrokerOrderStatus):
        return remote in BROKER_STATUS_EQUIVALENTS[local]
    if isinstance(remote, BrokerOrderStatus) and not isinstance(local, BrokerOrderStatus):
        return local in BROKER_STATUS_EQUIVALENTS[remote]
    return False


def _order_divergences(
    state: BrokerState, snapshot: VenueSnapshot
) -> tuple[list[SnapshotDivergence], int, Mapping[str, int]]:
    found: list[SnapshotDivergence] = []
    ids = [order.broker_order_id for order in snapshot.orders]
    duplicated = _duplicated(ids)
    for order_id in sorted(duplicated):
        found.append(
            SnapshotDivergence(
                SnapshotDivergenceKind.DUPLICATE_EVIDENCE,
                order_id,
                None,
                f"{duplicated[order_id]} orders",
                "The venue reports this order more than once; it is not compared.",
            )
        )
    remote = {o.broker_order_id: o for o in snapshot.orders if o.broker_order_id not in duplicated}

    for order_id in sorted(state.orders):
        held = state.orders[order_id]
        if order_id in remote or order_id in duplicated or held.is_terminal:
            continue
        found.append(
            SnapshotDivergence(
                SnapshotDivergenceKind.MISSING_ORDER,
                order_id,
                f"{held.status!s} {held.quantity} {held.symbol}",
                None,
                "The mirror holds this order working and the venue does not report it.",
            )
        )

    compared = 0
    for order_id in sorted(remote):
        reported = remote[order_id]
        local = state.orders.get(order_id)
        if local is None:
            found.append(
                SnapshotDivergence(
                    SnapshotDivergenceKind.UNKNOWN_ORDER,
                    order_id,
                    None,
                    f"{reported.status!s} {reported.quantity} {reported.symbol}",
                    "The venue reports an order the mirror does not hold.",
                )
            )
            continue
        compared += 1
        for kind, mine, theirs in (
            (SnapshotDivergenceKind.ORDER_QUANTITY, local.quantity, reported.quantity),
            (SnapshotDivergenceKind.ORDER_PRICE, local.price, reported.price),
            (
                SnapshotDivergenceKind.ORDER_FILLED_QUANTITY,
                local.filled_quantity,
                reported.filled_quantity,
            ),
        ):
            if mine != theirs:
                found.append(
                    SnapshotDivergence(kind, order_id, str(mine), str(theirs), f"{kind} differs.")
                )
        if not _statuses_consistent(local.status, reported.status):
            found.append(
                SnapshotDivergence(
                    SnapshotDivergenceKind.ORDER_STATUS,
                    order_id,
                    str(local.status),
                    str(reported.status),
                    "The two statuses cannot both be true of one order.",
                )
            )
    return found, compared, duplicated


def _execution_divergences(
    state: BrokerState, snapshot: VenueSnapshot, reported_orders: frozenset[str]
) -> tuple[list[SnapshotDivergence], int]:
    assert snapshot.executions is not None
    found: list[SnapshotDivergence] = []
    ids = [execution.execution_id for execution in snapshot.executions]
    duplicated = _duplicated(ids)
    for execution_id in sorted(duplicated):
        found.append(
            SnapshotDivergence(
                SnapshotDivergenceKind.DUPLICATE_EVIDENCE,
                execution_id,
                None,
                f"{duplicated[execution_id]} fills",
                "The venue reports this fill more than once; it is not compared.",
            )
        )
    remote = {e.execution_id: e for e in snapshot.executions if e.execution_id not in duplicated}

    compared = 0
    for execution_id in sorted(remote):
        other = remote[execution_id]
        local = state.executions.get(execution_id)
        if local is None:
            found.append(
                SnapshotDivergence(
                    SnapshotDivergenceKind.UNKNOWN_EXECUTION,
                    execution_id,
                    None,
                    f"{other.fill_quantity} {other.symbol} @ {other.fill_price}",
                    "The venue reports a fill the mirror has not applied.",
                )
            )
            continue
        compared += 1
        # Compared as numbers, as every other field here is. Until v3.10 these
        # three were compared as text, so a venue reporting ``100`` for a fill
        # the mirror holds as ``100.00`` was an EXECUTION_MISMATCH (BRK-001).
        for label, mine, theirs in (
            ("quantity", local.fill_quantity, other.fill_quantity),
            ("price", local.fill_price, other.fill_price),
            ("commission", local.commission, other.commission),
            ("order", local.broker_order_id, other.broker_order_id),
        ):
            if mine != theirs:
                found.append(
                    SnapshotDivergence(
                        SnapshotDivergenceKind.EXECUTION_MISMATCH,
                        execution_id,
                        str(mine),
                        str(theirs),
                        f"The fill's {label} differs.",
                    )
                )

    # Mirror fills are expected in the snapshot for the orders it reports, and
    # only for those: a venue may report today's orders and their fills, and a
    # fill for an order it no longer lists is not evidence of anything.
    for execution_id in sorted(state.executions):
        local = state.executions[execution_id]
        if execution_id in remote or execution_id in duplicated:
            continue
        if local.broker_order_id not in reported_orders:
            continue
        found.append(
            SnapshotDivergence(
                SnapshotDivergenceKind.MISSING_EXECUTION,
                execution_id,
                f"{local.fill_quantity} {local.symbol} @ {local.fill_price}",
                None,
                "The mirror applied a fill for an order the venue reports, and the venue does "
                "not report the fill.",
            )
        )
    return found, compared


def _position_divergences(
    state: BrokerState, snapshot: VenueSnapshot
) -> tuple[list[SnapshotDivergence], int]:
    assert snapshot.positions is not None
    found: list[SnapshotDivergence] = []
    symbols = [position.symbol for position in snapshot.positions]
    duplicated = _duplicated(symbols)
    for symbol in sorted(duplicated):
        found.append(
            SnapshotDivergence(
                SnapshotDivergenceKind.DUPLICATE_EVIDENCE,
                symbol,
                None,
                f"{duplicated[symbol]} positions",
                "The venue reports this position more than once; it is not compared.",
            )
        )
    remote = {p.symbol: p.quantity for p in snapshot.positions if p.symbol not in duplicated}
    local = {symbol: position.quantity for symbol, position in state.positions.items()}

    compared = 0
    for symbol in sorted({*local, *remote} - duplicated.keys()):
        compared += 1
        mine = local.get(symbol, Decimal("0"))
        theirs = remote.get(symbol, Decimal("0"))
        if mine == theirs:
            continue
        reason = (
            "The venue reports a position the mirror does not hold."
            if symbol not in local or mine == 0
            else "The mirror holds a position the venue does not report."
            if symbol not in remote or theirs == 0
            else "The position sizes differ."
        )
        found.append(
            SnapshotDivergence(
                SnapshotDivergenceKind.POSITION_QUANTITY, symbol, str(mine), str(theirs), reason
            )
        )
    return found, compared


def _account_divergences(state: BrokerState, account: BrokerAccount) -> list[SnapshotDivergence]:
    local = state.account
    if (local.account_id, local.currency) != (account.account_id, account.currency):
        return [
            SnapshotDivergence(
                SnapshotDivergenceKind.ACCOUNT_IDENTITY,
                "account",
                f"{local.account_id} in {local.currency}",
                f"{account.account_id} in {account.currency}",
                "The snapshot describes another account, or this one in another currency; its "
                "balances are not comparable and were not compared.",
            )
        ]
    found: list[SnapshotDivergence] = []
    for name, mine, theirs in (
        ("cash", local.cash, account.cash),
        ("equity", local.equity, account.equity),
        ("buying_power", local.buying_power, account.buying_power),
        ("margin", local.margin, account.margin),
        ("available_funds", local.available_funds, account.available_funds),
    ):
        if mine != theirs:
            found.append(
                SnapshotDivergence(
                    SnapshotDivergenceKind.BALANCE,
                    name,
                    str(mine),
                    str(theirs),
                    f"The {name.replace('_', ' ')} differs, in {local.currency}.",
                )
            )
    return found


def _freshness(
    state: BrokerState, snapshot: VenueSnapshot, evaluated_at: float, max_age_seconds: float
) -> SnapshotFreshness:
    if snapshot.as_of > evaluated_at:
        return SnapshotFreshness.FROM_THE_FUTURE
    if evaluated_at - snapshot.as_of > max_age_seconds:
        return SnapshotFreshness.TOO_OLD
    high_water = _mirror_high_water(state)
    if high_water is not None and snapshot.as_of < high_water:
        return SnapshotFreshness.PREDATES_MIRROR
    return SnapshotFreshness.CURRENT


def reconcile_snapshot(
    state: BrokerState,
    snapshot: VenueSnapshot,
    *,
    evaluated_at: float,
    max_age_seconds: float,
) -> SnapshotReconciliation:
    """Compare the mirror against a whole venue snapshot, field by field.

    **Freshness first.** A snapshot dated after ``evaluated_at`` is a look-ahead;
    one older than ``max_age_seconds`` at ``evaluated_at`` is out of budget; one
    taken before the mirror's newest change describes a venue the mirror has
    heard more from since. None of the three is compared -- every difference it
    showed could be the mirror being right -- and the result says which it was.
    Both instants and the budget are required, with no default: a freshness
    budget has no universal value, the rule
    :class:`~alphalab.lifecycle.specification.RuntimeRequirements` states.

    **Then everything, separately.** Orders by quantity, price, filled quantity
    and status (a broker-local status is consistent with any canonical status
    :data:`~alphalab.broker.order.BROKER_STATUS_EQUIVALENTS` relates it to);
    fills by quantity, price, commission and order; positions by size, in both
    directions; every balance; the account's identity and currency. A key the
    snapshot reports twice is ``DUPLICATE_EVIDENCE`` and is not compared.

    Pure and deterministic: reconciling the same pair twice gives equal results
    with equal :attr:`~SnapshotReconciliation.reconciliation_id`, which is what
    makes a repeated snapshot harmless. Nothing is resolved here -- the venue is
    the authority, and what to do about a difference stays with the caller, as
    it does for :func:`reconcile`.

    Raises:
        BrokerValidationError: If ``evaluated_at`` is not finite or the budget
            is negative.
    """

    if not math.isfinite(evaluated_at):
        raise BrokerValidationError(f"evaluated_at={evaluated_at!r} is not an instant.")
    if not max_age_seconds >= 0:
        raise BrokerValidationError(
            f"max_age_seconds={max_age_seconds!r}; a negative budget makes every snapshot old."
        )

    unexamined = tuple(
        area
        for area, value in (
            ("executions", snapshot.executions),
            ("positions", snapshot.positions),
            ("account", snapshot.account),
        )
        if value is None
    )
    freshness = _freshness(state, snapshot, evaluated_at, max_age_seconds)
    if freshness is not SnapshotFreshness.CURRENT:
        return SnapshotReconciliation(
            snapshot_id=snapshot.snapshot_id,
            as_of=snapshot.as_of,
            evaluated_at=evaluated_at,
            max_age_seconds=max_age_seconds,
            freshness=freshness,
            divergences=(),
            unexamined=unexamined,
            compared_orders=0,
            compared_executions=0,
            compared_positions=0,
        )

    divergences, compared_orders, duplicated_orders = _order_divergences(state, snapshot)
    compared_executions = compared_positions = 0
    if snapshot.executions is not None:
        reported = frozenset(
            order.broker_order_id
            for order in snapshot.orders
            if order.broker_order_id not in duplicated_orders
        )
        found, compared_executions = _execution_divergences(state, snapshot, reported)
        divergences.extend(found)
    if snapshot.positions is not None:
        found, compared_positions = _position_divergences(state, snapshot)
        divergences.extend(found)
    if snapshot.account is not None:
        divergences.extend(_account_divergences(state, snapshot.account))

    order = {kind: index for index, kind in enumerate(SnapshotDivergenceKind)}
    divergences.sort(key=lambda d: (order[d.kind], d.key, d.reason))
    return SnapshotReconciliation(
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        evaluated_at=evaluated_at,
        max_age_seconds=max_age_seconds,
        freshness=freshness,
        divergences=tuple(divergences),
        unexamined=unexamined,
        compared_orders=compared_orders,
        compared_executions=compared_executions,
        compared_positions=compared_positions,
    )
