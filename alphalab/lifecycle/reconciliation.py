"""What AlphaLab believes, against what the broker says -- item by item.

:func:`alphalab.broker.reconciliation.reconcile` has compared one pair since
v2.3, and it is the right comparison for what it is about: ``BrokerState``, which
is *AlphaLab's mirror of the venue*, against records the venue reported. It is
unchanged, it remains the authority for that pair, and this module calls nothing
in it that would change it.

The pair it cannot compare is the one that matters once a strategy is live:

.. code-block:: text

    broker.reconcile      BrokerState  <-> the venue's own records
    this module           AlphaLab's execution state  <->  BrokerState
                          (OMS orders, portfolio positions, cash)

Those are two different disagreements with two different causes. The venue and
the mirror drift because a message was lost; the mirror and the book drift
because a fill was applied to one and not the other. A single function reporting
both would be unable to say which had happened.

Nothing here is a second order, fill, position or account system
-----------------------------------------------------------------

Every value compared is read from an existing authority and none is copied into
a shape of this module's own:

======================== =====================================================
AlphaLab orders          :class:`alphalab.oms.order.Order`, from
                         :class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineState`
AlphaLab fills           :class:`alphalab.execution.report.ExecutionReport`
AlphaLab positions       :class:`alphalab.portfolio.position.Position`
AlphaLab cash            :class:`alphalab.portfolio.cash.CashLedger`
Broker everything        :mod:`alphalab.broker` -- the canonical, vendor-neutral
                         adapter boundary
The join between them    :class:`~alphalab.broker.reconciliation.ExternalOrderMap`,
                         which is already the one authority on which OMS order is
                         which venue handle
======================== =====================================================

The broker side is vendor-neutral by construction. This module imports no
client, opens no connection, holds no credential and names no venue; a
:class:`~alphalab.broker.state.BrokerState` is filled in by whatever adapter an
application has, and AlphaLab ships one simulator and one HTTP contract.

Neither side is authoritative
------------------------------

A :class:`Mismatch` says what each side holds and stops. It does not decide
which is right, and nothing here writes to either state -- the states are frozen
values and the function is pure, so a caller that wants to resolve a break does
so with its own policy, in its own code. ``broker.reconcile`` puts it well and
the same applies twice over here: "deciding what to do about a missing order is
a policy question with no safe default -- re-sending one that actually exists
would duplicate it."

What is compared, and what is deliberately not
-----------------------------------------------

**Only orders the mapping binds.** A working OMS order with no venue handle was
never routed, so AlphaLab expects nothing of the broker for it and its absence
there is not a break. That the order is unrouted at all is a live-driver
question, and :func:`~alphalab.runtime.live.live_health` already answers it.

**Only the currency the broker account names.** A
:class:`~alphalab.broker.account.BrokerAccount` is denominated in one currency.
Every other currency in AlphaLab's ledger is outside what this account can speak
about, and is reported as an :class:`UnreconciledArea` rather than as a
difference of zero.

**Instruments only through a supplied mapping.** A venue names an instrument its
own way and AlphaLab names it by a derived ``asset_id``.
:class:`SymbolMapping` is how the two are joined, it has no default, and
:meth:`SymbolMapping.identity` is a *named* choice a caller makes when the venue
symbols are already asset ids -- which is true of AlphaLab's own routing, and of
nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto

from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState
from alphalab.core.enums import OrderStatus
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.tolerance import Tolerance, ToleranceOutcome
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.execution_pipeline import ExecutionPipelineState

__all__ = [
    "BROKER_STATUS_EQUIVALENTS",
    "Mismatch",
    "MismatchCategory",
    "ReconciliationTolerances",
    "StateReconciliation",
    "SymbolMapping",
    "UnreconciledArea",
    "reconcile_execution_state",
]


class MismatchCategory(Enum):
    """The fourteen ways AlphaLab's execution state and a broker can disagree.

    Each is a separate member because each needs a different fix. A missing
    order may need re-sending; an unexpected one may belong to another session;
    a quantity difference means a fill went to one side and not the other. One
    category covering two of those would be a category nobody can act on.
    """

    #: AlphaLab routed an order and the broker does not hold it.
    MISSING_EXPECTED_ORDER = auto()

    #: The broker holds an order AlphaLab did not route.
    UNEXPECTED_OBSERVED_ORDER = auto()

    #: One order, two ordered quantities.
    ORDER_QUANTITY_MISMATCH = auto()

    #: One order, two prices.
    ORDER_PRICE_MISMATCH = auto()

    #: One order, two statuses that cannot both be true.
    ORDER_STATUS_MISMATCH = auto()

    #: AlphaLab applied a fill the broker does not report.
    MISSING_EXPECTED_FILL = auto()

    #: The broker reports a fill AlphaLab has not applied.
    UNEXPECTED_OBSERVED_FILL = auto()

    #: One fill, two quantities.
    FILL_QUANTITY_MISMATCH = auto()

    #: One fill, two prices, two commissions, or two parent orders.
    EXECUTION_MISMATCH = auto()

    #: One instrument, two position sizes.
    POSITION_QUANTITY_MISMATCH = auto()

    #: The broker holds a position in something AlphaLab holds nothing of.
    UNEXPECTED_POSITION = auto()

    #: A venue symbol that resolves to no AlphaLab instrument, or an order whose
    #: symbol is not the asset its OMS order names.
    INSTRUMENT_MISMATCH = auto()

    #: The account's settled cash differs.
    ACCOUNT_CASH_MISMATCH = auto()

    #: One order is finished on one side and still working on the other, or a
    #: binding names an order that no longer exists.
    LIFECYCLE_STATE_MISMATCH = auto()


#: Which OMS statuses each broker-local operational status is consistent with.
#:
#: :class:`~alphalab.broker.order.BrokerOrderStatus` covers "the states that
#: exist between AlphaLab and the venue and nowhere else", so they have no OMS
#: equivalent and comparing them for equality would report every in-flight order
#: as a break. This states the relation once, in the open, rather than leaving it
#: to a chain of ``if``s -- the same shape
#: :data:`~alphalab.lifecycle.progression.PROGRESSION_MODEL_STAGES` uses for the
#: other pair of vocabularies v3.5 has to relate.
BROKER_STATUS_EQUIVALENTS: Mapping[BrokerOrderStatus, frozenset[OrderStatus]] = {
    BrokerOrderStatus.PENDING_SUBMIT: frozenset(
        {OrderStatus.NEW, OrderStatus.PENDING, OrderStatus.ACCEPTED}
    ),
    BrokerOrderStatus.SUBMITTED: frozenset(
        {OrderStatus.NEW, OrderStatus.PENDING, OrderStatus.ACCEPTED}
    ),
    BrokerOrderStatus.PENDING_CANCEL: frozenset(
        {OrderStatus.CANCEL_PENDING, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
    ),
}


@dataclass(frozen=True, slots=True)
class SymbolMapping:
    """How a venue's instrument names join AlphaLab's derived identities.

    Required, with no default, because a default would be an assumption about
    somebody else's venue. :meth:`identity` is how a caller says "this venue's
    symbols *are* asset ids", which is true of AlphaLab's own routing --
    :func:`~alphalab.runtime.broker_routing.routable` sets
    ``BrokerOrder.symbol`` from ``Order.asset_id`` -- and is a statement the
    caller makes rather than one this module infers.

    Attributes:
        mapping: Venue symbol -> ``asset_id``. A symbol absent from it resolves
            to nothing, which is an :attr:`MismatchCategory.INSTRUMENT_MISMATCH`
            and not an invented instrument.
        assume_identity: Whether an unmapped symbol is its own ``asset_id``. Set
            only by :meth:`identity`.
    """

    mapping: Mapping[str, str] = field(default_factory=dict)
    assume_identity: bool = False

    @classmethod
    def identity(cls) -> SymbolMapping:
        """The venue's symbols are AlphaLab's asset ids. A choice, stated."""

        return cls(mapping={}, assume_identity=True)

    def asset_for(self, symbol: str) -> str | None:
        """The ``asset_id`` a venue symbol names, or ``None`` when it names none."""

        resolved = self.mapping.get(symbol)
        if resolved is not None:
            return resolved
        return symbol if self.assume_identity else None


@dataclass(frozen=True, slots=True)
class ReconciliationTolerances:
    """How close each compared number has to be. Every one required.

    There is no default set and no partially-defaulted one. A quantity tolerance
    that is right for a book of equities is wrong for one of crypto perpetuals
    quoted to eight decimal places, and a cash tolerance that is right for one
    currency's minor unit is wrong for a currency with none.
    """

    order_quantity: Tolerance
    order_price: Tolerance
    fill_quantity: Tolerance
    fill_price: Tolerance
    commission: Tolerance
    position_quantity: Tolerance
    cash: Tolerance


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One disagreement, with both sides of it rendered.

    Attributes:
        category: Which kind.
        key: What it is about -- an ``oms_order_id``, an ``execution_id``, an
            ``asset_id``, a currency code.
        expected: What AlphaLab holds, rendered, or ``None`` when it holds
            nothing.
        observed: What the broker holds, rendered, or ``None`` when it holds
            nothing.
        reason: One sentence naming the difference.

    Both sides are strings rather than typed values, deliberately. A mismatch
    spans quantities, prices, statuses, symbols and currencies, and a field
    typed for one of them would be empty for the rest; the typed values stay on
    the states this was computed from, which the caller already has.
    """

    category: MismatchCategory
    key: str
    expected: str | None
    observed: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class UnreconciledArea:
    """Something that was not compared, and why it could not be.

    The type that stops a narrow reconciliation from reading as a clean one. A
    currency the broker account cannot speak about has not been shown to agree;
    it has not been looked at, and those are different facts.
    """

    area: str
    reason: str


@dataclass(frozen=True, slots=True)
class StateReconciliation:
    """Everything the two sides disagree about, and everything unexamined.

    Attributes:
        mismatches: In :class:`MismatchCategory` declaration order, then by key.
            Deterministic, so reconciling the same pair of states twice produces
            an equal result and two reconciliations can be diffed.
        unreconciled: What was not compared, in the order it was skipped.
        compared_orders: How many bound orders were examined.
        compared_fills: How many fills were examined.
        compared_positions: How many instruments were examined.
    """

    mismatches: tuple[Mismatch, ...] = ()
    unreconciled: tuple[UnreconciledArea, ...] = ()
    compared_orders: int = 0
    compared_fills: int = 0
    compared_positions: int = 0

    @property
    def reconciled(self) -> bool:
        """Whether the two sides agree about everything that was compared.

        Deliberately **not** "everything is fine": read it with
        :attr:`fully_reconciled`, which also requires that nothing was skipped.
        Keeping them apart is what stops a reconciliation over an empty
        comparison from reading as proof.
        """

        return not self.mismatches

    @property
    def fully_reconciled(self) -> bool:
        """Whether the two sides agree and nothing was left unexamined."""

        return not self.mismatches and not self.unreconciled

    def mismatches_in(self, category: MismatchCategory) -> tuple[Mismatch, ...]:
        """Every mismatch of one kind, in order."""

        return tuple(entry for entry in self.mismatches if entry.category is category)


# --------------------------------------------------------------------------- #
# The comparison
# --------------------------------------------------------------------------- #


def _order_price(order: OMSOrder) -> Decimal | None:
    """The price AlphaLab addressed this order at, or ``None`` if it has none.

    Resolved exactly as :func:`~alphalab.runtime.broker_routing.routable`
    resolves it -- the limit price, else the reference price the allocation
    engine sized it at -- because that is the number that was sent, and
    comparing the broker's price against anything else compares it to a figure
    the venue never saw. ``None`` where ``routable`` falls back to zero: a
    market order with no recorded reference has no price, and calling that zero
    would make every such order look mispriced by its whole value.
    """

    if order.limit_price is not None:
        return order.limit_price
    reference = order.metadata.get("reference_price")
    return None if reference is None else Decimal(reference)


def _status_conflict(local: OMSOrder, remote: BrokerOrder) -> str:
    """Why the two statuses cannot both be true, or ``""``."""

    if isinstance(remote.status, BrokerOrderStatus):
        if local.status in BROKER_STATUS_EQUIVALENTS[remote.status]:
            return ""
        consistent = ", ".join(
            sorted(status.name for status in BROKER_STATUS_EQUIVALENTS[remote.status])
        )
        return (
            f"the broker has it {remote.status.name}, which is consistent with OMS "
            f"{consistent}, and the OMS has it {local.status.name}."
        )
    if local.status is remote.status:
        return ""
    return f"the OMS has it {local.status.name} and the broker has it {remote.status.name}."


def _compare_orders(
    pipeline: ExecutionPipelineState,
    broker: BrokerState,
    mapping: ExternalOrderMap,
    symbols: SymbolMapping,
    tolerances: ReconciliationTolerances,
) -> tuple[list[Mismatch], int]:
    mismatches: list[Mismatch] = []
    local_orders = {str(order.order_id.value): order for order in pipeline.oms.orders.orders()}
    compared = 0

    for oms_order_id in sorted(mapping.to_broker):
        broker_order_id = mapping.to_broker[oms_order_id]
        local = local_orders.get(oms_order_id)
        remote = broker.orders.get(broker_order_id)

        if local is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.LIFECYCLE_STATE_MISMATCH,
                    oms_order_id,
                    None,
                    broker_order_id,
                    "the venue binding names an OMS order this execution state does not "
                    "hold; the binding and the book disagree about which orders exist.",
                )
            )
            continue
        if remote is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.MISSING_EXPECTED_ORDER,
                    oms_order_id,
                    f"{local.status.name} {local.quantity} {local.asset_id}",
                    None,
                    f"AlphaLab routed this order as {broker_order_id} and the broker does "
                    "not hold it.",
                )
            )
            continue

        compared += 1

        resolved = symbols.asset_for(remote.symbol)
        if resolved is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.INSTRUMENT_MISMATCH,
                    oms_order_id,
                    local.asset_id,
                    remote.symbol,
                    f"the venue symbol {remote.symbol!r} resolves to no AlphaLab "
                    "instrument under the supplied mapping.",
                )
            )
        elif resolved != local.asset_id:
            mismatches.append(
                Mismatch(
                    MismatchCategory.INSTRUMENT_MISMATCH,
                    oms_order_id,
                    local.asset_id,
                    resolved,
                    f"the venue has this order on {remote.symbol!r}, which is {resolved}, "
                    f"and the OMS has it on {local.asset_id}.",
                )
            )

        if remote.oms_order_id != oms_order_id:
            mismatches.append(
                Mismatch(
                    MismatchCategory.LIFECYCLE_STATE_MISMATCH,
                    oms_order_id,
                    oms_order_id,
                    remote.oms_order_id,
                    "the broker order names a different OMS order than the binding does; "
                    "one order's fills could reach another.",
                )
            )

        if (
            tolerances.order_quantity.outcome(local.quantity, remote.quantity)
            is ToleranceOutcome.MATERIAL
        ):
            mismatches.append(
                Mismatch(
                    MismatchCategory.ORDER_QUANTITY_MISMATCH,
                    oms_order_id,
                    str(local.quantity),
                    str(remote.quantity),
                    "the ordered quantities differ by more than the stated tolerance.",
                )
            )

        local_price = _order_price(local)
        if local_price is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.ORDER_PRICE_MISMATCH,
                    oms_order_id,
                    None,
                    str(remote.price),
                    "the OMS order records neither a limit nor a reference price, so the "
                    "price the broker holds cannot be checked against anything.",
                )
            )
        elif tolerances.order_price.outcome(local_price, remote.price) is ToleranceOutcome.MATERIAL:
            mismatches.append(
                Mismatch(
                    MismatchCategory.ORDER_PRICE_MISMATCH,
                    oms_order_id,
                    str(local_price),
                    str(remote.price),
                    "the order prices differ by more than the stated tolerance.",
                )
            )

        conflict = _status_conflict(local, remote)
        if conflict:
            mismatches.append(
                Mismatch(
                    MismatchCategory.ORDER_STATUS_MISMATCH,
                    oms_order_id,
                    local.status.name,
                    remote.status.name,
                    conflict,
                )
            )

        if local.is_closed != remote.is_terminal:
            mismatches.append(
                Mismatch(
                    MismatchCategory.LIFECYCLE_STATE_MISMATCH,
                    oms_order_id,
                    "closed" if local.is_closed else "working",
                    "terminal" if remote.is_terminal else "working",
                    "one side considers this order finished and the other does not; a "
                    "fill arriving now would be applied by one and refused by the other.",
                )
            )

    for broker_order_id in sorted(broker.orders):
        if mapping.oms_id_for(broker_order_id) is not None:
            continue
        remote = broker.orders[broker_order_id]
        mismatches.append(
            Mismatch(
                MismatchCategory.UNEXPECTED_OBSERVED_ORDER,
                broker_order_id,
                None,
                f"{remote.status.name} {remote.quantity} {remote.symbol}",
                "the broker holds an order no venue binding names; it may belong to "
                "another session, or AlphaLab may have lost an order it sent.",
            )
        )

    return mismatches, compared


def _compare_fills(
    pipeline: ExecutionPipelineState,
    broker: BrokerState,
    mapping: ExternalOrderMap,
    tolerances: ReconciliationTolerances,
) -> tuple[list[Mismatch], int]:
    """Fills joined on ``execution_id``, which both sides already share.

    :func:`~alphalab.runtime.broker_routing.execution_report_from_broker` copies
    the venue's ``execution_id`` onto the report it builds, so a live fill has
    exactly one identity on both sides and no second key has to be invented.
    """

    mismatches: list[Mismatch] = []
    local_reports = pipeline.execution.reports
    compared = 0

    for execution_id in sorted(local_reports):
        report = local_reports[execution_id]
        remote = broker.executions.get(execution_id)
        if remote is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.MISSING_EXPECTED_FILL,
                    execution_id,
                    f"{report.fill_quantity} {report.asset_id} @ {report.fill_price}",
                    None,
                    "AlphaLab applied this fill and the broker does not report it.",
                )
            )
            continue

        compared += 1

        if (
            tolerances.fill_quantity.outcome(report.fill_quantity, remote.fill_quantity)
            is ToleranceOutcome.MATERIAL
        ):
            mismatches.append(
                Mismatch(
                    MismatchCategory.FILL_QUANTITY_MISMATCH,
                    execution_id,
                    str(report.fill_quantity),
                    str(remote.fill_quantity),
                    "the filled quantities differ by more than the stated tolerance.",
                )
            )
        if (
            tolerances.fill_price.outcome(report.fill_price, remote.fill_price)
            is ToleranceOutcome.MATERIAL
        ):
            mismatches.append(
                Mismatch(
                    MismatchCategory.EXECUTION_MISMATCH,
                    execution_id,
                    str(report.fill_price),
                    str(remote.fill_price),
                    "the fill prices differ by more than the stated tolerance.",
                )
            )
        if (
            tolerances.commission.outcome(report.commission, remote.commission)
            is ToleranceOutcome.MATERIAL
        ):
            mismatches.append(
                Mismatch(
                    MismatchCategory.EXECUTION_MISMATCH,
                    execution_id,
                    str(report.commission),
                    str(remote.commission),
                    "the commissions differ by more than the stated tolerance.",
                )
            )

        bound = mapping.oms_id_for(remote.broker_order_id)
        if bound is not None and bound != report.order_id:
            mismatches.append(
                Mismatch(
                    MismatchCategory.EXECUTION_MISMATCH,
                    execution_id,
                    report.order_id,
                    bound,
                    "AlphaLab applied this fill to one order and the broker reports it "
                    "against another.",
                )
            )

    for execution_id in sorted(broker.executions):
        if execution_id in local_reports:
            continue
        remote = broker.executions[execution_id]
        mismatches.append(
            Mismatch(
                MismatchCategory.UNEXPECTED_OBSERVED_FILL,
                execution_id,
                None,
                f"{remote.fill_quantity} {remote.symbol} @ {remote.fill_price}",
                "the broker reports a fill AlphaLab has not applied; the book is missing "
                "a position the venue believes it holds.",
            )
        )

    return mismatches, compared


def _compare_positions(
    pipeline: ExecutionPipelineState,
    broker: BrokerState,
    symbols: SymbolMapping,
    tolerances: ReconciliationTolerances,
) -> tuple[list[Mismatch], int]:
    mismatches: list[Mismatch] = []
    remote_by_asset: dict[str, Decimal] = {}

    for symbol in sorted(broker.positions):
        position = broker.positions[symbol]
        resolved = symbols.asset_for(symbol)
        if resolved is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.INSTRUMENT_MISMATCH,
                    symbol,
                    None,
                    str(position.quantity),
                    f"the broker holds a position in {symbol!r}, which resolves to no "
                    "AlphaLab instrument under the supplied mapping.",
                )
            )
            continue
        if resolved in remote_by_asset:
            mismatches.append(
                Mismatch(
                    MismatchCategory.INSTRUMENT_MISMATCH,
                    resolved,
                    None,
                    symbol,
                    f"two venue symbols resolve to {resolved}; a book holds one position "
                    "per instrument and summing them would double an exposure.",
                )
            )
            continue
        remote_by_asset[resolved] = position.quantity

    local_by_asset = {
        asset_id: position.quantity for asset_id, position in pipeline.portfolio.positions.items()
    }

    compared = 0
    for asset_id in sorted({*local_by_asset, *remote_by_asset}):
        held = local_by_asset.get(asset_id)
        reported = remote_by_asset.get(asset_id)
        compared += 1

        if held is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.UNEXPECTED_POSITION,
                    asset_id,
                    None,
                    str(reported),
                    "the broker holds a position in an instrument AlphaLab's book does "
                    "not carry at all.",
                )
            )
            continue

        # A position the broker does not report is a real zero on its side: a
        # venue that holds nothing in an instrument reports nothing for it. The
        # missing case is a broker state with no position records at all, which
        # the caller can see from the state it passed in.
        observed = Decimal("0") if reported is None else reported
        if tolerances.position_quantity.outcome(held, observed) is ToleranceOutcome.MATERIAL:
            mismatches.append(
                Mismatch(
                    MismatchCategory.POSITION_QUANTITY_MISMATCH,
                    asset_id,
                    str(held),
                    str(observed),
                    (
                        "AlphaLab holds a position the broker does not report."
                        if reported is None
                        else "the position sizes differ by more than the stated tolerance."
                    ),
                )
            )
    return mismatches, compared


def _compare_cash(
    pipeline: ExecutionPipelineState, broker: BrokerState, tolerances: ReconciliationTolerances
) -> tuple[list[Mismatch], list[UnreconciledArea]]:
    currency = broker.account.currency
    mismatches: list[Mismatch] = []
    unreconciled: list[UnreconciledArea] = []

    if not currency.strip():
        return [], [
            UnreconciledArea(
                "account cash",
                "the broker account names no currency, so its cash balance is a number "
                "with no unit and nothing in AlphaLab's ledger can be compared to it.",
            )
        ]

    # ``balance`` rather than a lookup with a fallback: the ledger's own
    # accessor already answers 0.00 for a currency it holds none of, which is a
    # real zero -- a book that has never held yen holds no yen. The *missing*
    # case here is a broker account that names no currency, handled above.
    held = pipeline.portfolio.cash.balance(currency)
    if tolerances.cash.outcome(held, broker.account.cash) is ToleranceOutcome.MATERIAL:
        mismatches.append(
            Mismatch(
                MismatchCategory.ACCOUNT_CASH_MISMATCH,
                currency,
                str(held),
                str(broker.account.cash),
                "the settled cash balances differ by more than the stated tolerance.",
            )
        )

    others = sorted(
        other
        for other, amount in pipeline.portfolio.cash.balances.items()
        if other != currency and amount != Decimal("0.00")
    )
    if others:
        unreconciled.append(
            UnreconciledArea(
                "account cash",
                f"AlphaLab's ledger also holds {others}, and this broker account is "
                f"denominated in {currency}; those balances were not compared to "
                "anything, which is not the same as agreeing.",
            )
        )
    return mismatches, unreconciled


def reconcile_execution_state(
    pipeline: ExecutionPipelineState,
    broker: BrokerState,
    mapping: ExternalOrderMap,
    symbols: SymbolMapping,
    tolerances: ReconciliationTolerances,
) -> StateReconciliation:
    """Compare AlphaLab's execution state against a broker's, and report.

    Pure, total and deterministic. Every mismatch is found on every call, the
    order is fixed, and reconciling the same two states twice returns equal
    results -- which is what lets a caller store a reconciliation and compare it
    against the next one.

    **Nothing is changed.** Both states are frozen values, this function returns
    a report, and no policy about which side is right is applied anywhere in it.

    Args:
        pipeline: AlphaLab's execution state -- the OMS book, the portfolio and
            the execution reports it has applied.
        broker: The normalized broker state an adapter filled in.
        mapping: The OMS-order-to-venue-handle binding. Orders it does not bind
            were never routed and are not expected at the broker; see the module
            docstring.
        symbols: How venue symbols join AlphaLab instruments.
        tolerances: How close each compared number has to be.

    Returns:
        A :class:`StateReconciliation`. An empty one whose
        :attr:`~StateReconciliation.fully_reconciled` is true is the only proof
        that the two sides agree.

    Raises:
        LifecycleInputError: If the binding is internally inconsistent -- an OMS
            order bound to one handle in one direction and another in the other.
            That is a defect in the caller's own mapping and every result
            computed from it would be wrong in a way no mismatch category
            describes.
    """

    for oms_order_id, broker_order_id in mapping.to_broker.items():
        if mapping.to_oms.get(broker_order_id) != oms_order_id:
            raise LifecycleInputError(
                f"The venue binding maps OMS order {oms_order_id} to {broker_order_id} "
                f"and {broker_order_id} back to "
                f"{mapping.to_oms.get(broker_order_id)!r}. A binding that disagrees with "
                "itself cannot say which order is which, and every comparison under it "
                "would be about the wrong pair."
            )

    order_mismatches, compared_orders = _compare_orders(
        pipeline, broker, mapping, symbols, tolerances
    )
    fill_mismatches, compared_fills = _compare_fills(pipeline, broker, mapping, tolerances)
    position_mismatches, compared_positions = _compare_positions(
        pipeline, broker, symbols, tolerances
    )
    cash_mismatches, unreconciled = _compare_cash(pipeline, broker, tolerances)

    mismatches = [
        *order_mismatches,
        *fill_mismatches,
        *position_mismatches,
        *cash_mismatches,
    ]
    order = {category: index for index, category in enumerate(MismatchCategory)}
    mismatches.sort(key=lambda entry: (order[entry.category], entry.key, entry.reason))

    return StateReconciliation(
        mismatches=tuple(mismatches),
        unreconciled=tuple(unreconciled),
        compared_orders=compared_orders,
        compared_fills=compared_fills,
        compared_positions=compared_positions,
    )
