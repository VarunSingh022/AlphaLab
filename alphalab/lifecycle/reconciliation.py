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

One book, several accounts (v3.12)
----------------------------------

A desk that routes one book through several brokers holds one OMS book, one
portfolio and one set of applied fills, and a mirror per account.
:func:`reconcile_execution_state` compares a book with *one* mirror, so until
v3.12 such a desk had to cut its book into per-account pieces by hand (ledger
BRK-004). :func:`reconcile_accounts` takes every account's
:class:`AccountMirror` and a **declared** assignment of orders to accounts, and
reconciles the whole book in one pass:

* **Orders and fills, per account.** Each account is compared exactly as
  :func:`reconcile_execution_state` compares one, over the orders declared for
  it and the fills applied to them. An order bound -- or a fill reported -- at an
  account other than the one it is declared for is an
  :attr:`MismatchCategory.ACCOUNT_ASSIGNMENT_MISMATCH`: the multi-broker way to
  send an order twice.
* **Positions and cash, in total.** The book holds one position per instrument
  and one balance per currency, with no account dimension, so neither can be cut
  per account from the book's own records. Each is compared against the sum over
  the accounts that report it, and a break names every account's share.
* **Undeclared orders are reported, never guessed.** An order the book holds
  fills for, or that some account binds, and that the assignment does not name,
  is listed in :attr:`StateReconciliation.unassigned` and compared to nothing.
  Placing it by its binding would be inferring the very fact the assignment
  exists to state.

Every mismatch found at one account carries that account in
:attr:`Mismatch.account`; a total across accounts carries ``None``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum, auto

from alphalab.broker.order import BROKER_STATUS_EQUIVALENTS, BrokerOrder, BrokerOrderStatus
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState
from alphalab.execution.report import ExecutionReport
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.tolerance import Tolerance, ToleranceOutcome
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.broker_routing import ChildOrderBindings
from alphalab.runtime.execution_pipeline import ExecutionPipelineState

__all__ = [
    "BROKER_STATUS_EQUIVALENTS",
    "AccountMirror",
    "Mismatch",
    "MismatchCategory",
    "ReconciliationTolerances",
    "StateReconciliation",
    "SymbolMapping",
    "UnreconciledArea",
    "reconcile_accounts",
    "reconcile_execution_state",
]


class MismatchCategory(Enum):
    """The fifteen ways AlphaLab's execution state and a broker can disagree.

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

    #: An order bound, or a fill reported, at an account other than the one the
    #: order is declared for (:func:`reconcile_accounts`, v3.12).
    ACCOUNT_ASSIGNMENT_MISMATCH = auto()


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
        account: The account it was found at when a book is reconciled against
            several (:func:`reconcile_accounts`); ``None`` against one account,
            and for a position or a balance compared in total across accounts.

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
    account: str | None = None


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
        unassigned: The OMS orders a reconciliation across accounts could place
            at none, in id order: the book holds fills for each, or an account
            binds it, and the declared assignment names no account for it.
            Nothing about them was compared. Always empty against one account.
    """

    mismatches: tuple[Mismatch, ...] = ()
    unreconciled: tuple[UnreconciledArea, ...] = ()
    compared_orders: int = 0
    compared_fills: int = 0
    compared_positions: int = 0
    unassigned: tuple[str, ...] = ()

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

        return not self.mismatches and not self.unreconciled and not self.unassigned

    def mismatches_in(self, category: MismatchCategory) -> tuple[Mismatch, ...]:
        """Every mismatch of one kind, in order."""

        return tuple(entry for entry in self.mismatches if entry.category is category)

    def mismatches_at(self, account: str | None) -> tuple[Mismatch, ...]:
        """Every mismatch found at one account, in order; ``None`` for the totals."""

        return tuple(entry for entry in self.mismatches if entry.account == account)


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


def _compare_children(
    local_orders: Mapping[str, OMSOrder],
    broker: BrokerState,
    children: ChildOrderBindings,
    symbols: SymbolMapping,
    tolerances: ReconciliationTolerances,
    parents: Iterable[str] | None = None,
) -> tuple[list[Mismatch], int]:
    """An algorithm's child venue orders against the one OMS order they work.

    Fills are not compared here: each child fill carries the venue's execution
    id onto the parent's execution report, so :func:`_compare_fills` joins it
    exactly as it joins a directly routed fill. ``parents`` limits the parents
    compared, in sorted order; ``None`` compares every one.
    """

    mismatches: list[Mismatch] = []
    compared = 0
    for parent_id in sorted(children.by_parent if parents is None else parents):
        local = local_orders.get(parent_id)
        if local is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.LIFECYCLE_STATE_MISMATCH,
                    parent_id,
                    None,
                    ", ".join(children.children_of(parent_id)),
                    "the child bindings name a parent this execution state does not hold.",
                )
            )
            continue
        working = Decimal("0")
        working_children: list[str] = []
        for handle in children.children_of(parent_id):
            remote = broker.orders.get(handle)
            if remote is None:
                mismatches.append(
                    Mismatch(
                        MismatchCategory.MISSING_EXPECTED_ORDER,
                        handle,
                        f"child of {parent_id}",
                        None,
                        "AlphaLab routed this child and the broker does not hold it.",
                    )
                )
                continue
            compared += 1
            if remote.oms_order_id != parent_id:
                mismatches.append(
                    Mismatch(
                        MismatchCategory.LIFECYCLE_STATE_MISMATCH,
                        handle,
                        parent_id,
                        remote.oms_order_id,
                        "the broker order names a different OMS order than its child binding.",
                    )
                )
            resolved = symbols.asset_for(remote.symbol)
            if resolved != local.asset_id:
                mismatches.append(
                    Mismatch(
                        MismatchCategory.INSTRUMENT_MISMATCH,
                        handle,
                        local.asset_id,
                        remote.symbol if resolved is None else resolved,
                        "a child trades something other than its parent.",
                    )
                )
            if not remote.is_terminal:
                working += remote.remaining_quantity
                working_children.append(handle)
        if (
            tolerances.order_quantity.outcome(
                max(working, local.remaining_quantity), local.remaining_quantity
            )
            is ToleranceOutcome.MATERIAL
        ):
            mismatches.append(
                Mismatch(
                    MismatchCategory.ORDER_QUANTITY_MISMATCH,
                    parent_id,
                    str(local.remaining_quantity),
                    str(working),
                    "the children working at the venue exceed what the parent has left unfilled.",
                )
            )
        if local.is_closed and working_children:
            mismatches.append(
                Mismatch(
                    MismatchCategory.LIFECYCLE_STATE_MISMATCH,
                    parent_id,
                    local.status.name,
                    ", ".join(working_children),
                    "the parent is finished and children are still working at the venue; a "
                    "fill arriving now would land on an order that can no longer take it.",
                )
            )
    return mismatches, compared


def _compare_orders(
    local_orders: Mapping[str, OMSOrder],
    broker: BrokerState,
    mapping: ExternalOrderMap,
    symbols: SymbolMapping,
    tolerances: ReconciliationTolerances,
    children: ChildOrderBindings | None,
    *,
    bound: Iterable[str] | None = None,
    parents: Iterable[str] | None = None,
) -> tuple[list[Mismatch], int]:
    """Every binding's two orders, then every broker order nothing binds.

    ``bound`` and ``parents`` limit the bindings and the algorithm parents
    compared -- across accounts, to those declared for this one; ``None``
    compares every one. A broker order *any* binding here names is never
    unexpected: one bound for an order declared elsewhere is the caller's to
    report.
    """

    mismatches: list[Mismatch] = []
    compared = 0

    for oms_order_id in sorted(mapping.to_broker if bound is None else bound):
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
        if children is not None and children.parent_of(broker_order_id) is not None:
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

    if children is not None:
        child_mismatches, child_compared = _compare_children(
            local_orders, broker, children, symbols, tolerances, parents
        )
        mismatches.extend(child_mismatches)
        compared += child_compared

    return mismatches, compared


def _compare_fills(
    local_reports: Mapping[str, ExecutionReport],
    broker: BrokerState,
    mapping: ExternalOrderMap,
    tolerances: ReconciliationTolerances,
    children: ChildOrderBindings | None,
    book_reports: Mapping[str, ExecutionReport] | None = None,
) -> tuple[list[Mismatch], int]:
    """Fills joined on ``execution_id``, which both sides already share.

    :func:`~alphalab.runtime.broker_routing.execution_report_from_broker` copies
    the venue's ``execution_id`` onto the report it builds, so a live fill has
    exactly one identity on both sides and no second key has to be invented.

    ``local_reports`` are the fills expected at this broker. ``book_reports``,
    when given, are every fill the book holds: a broker fill among them and not
    expected here belongs to an order declared for another account, which the
    caller reports, and is not "a fill AlphaLab has not applied".
    """

    mismatches: list[Mismatch] = []
    applied = local_reports if book_reports is None else book_reports
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
        if bound is None and children is not None:
            bound = children.parent_of(remote.broker_order_id)
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
        if execution_id in applied:
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


def _resolved_positions(
    broker: BrokerState, symbols: SymbolMapping
) -> tuple[dict[str, Decimal], list[Mismatch]]:
    """One broker's positions by ``asset_id``, and the symbols that would not resolve."""

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
    return remote_by_asset, mismatches


def _compare_positions(
    local_by_asset: Mapping[str, Decimal],
    remote_by_asset: Mapping[str, Decimal],
    tolerances: ReconciliationTolerances,
    shares: Mapping[str, str] | None = None,
) -> tuple[list[Mismatch], int]:
    """The book's position in each instrument against what the broker side holds.

    ``shares``, across accounts, renders each instrument's per-account split,
    which a break then names.
    """

    mismatches: list[Mismatch] = []
    compared = 0
    for asset_id in sorted({*local_by_asset, *remote_by_asset}):
        held = local_by_asset.get(asset_id)
        reported = remote_by_asset.get(asset_id)
        compared += 1

        split = "" if shares is None else f" The accounts report {shares.get(asset_id, 'none')}."
        if held is None:
            mismatches.append(
                Mismatch(
                    MismatchCategory.UNEXPECTED_POSITION,
                    asset_id,
                    None,
                    str(reported),
                    "the broker holds a position in an instrument AlphaLab's book does "
                    f"not carry at all.{split}",
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
                    )
                    + split,
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
    *,
    children: ChildOrderBindings | None = None,
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
        children: The venue handles an execution algorithm sent on behalf of an
            OMS order (v3.9), or ``None`` when no order was worked in children --
            which is every run before v3.9 and changes nothing. With it, each
            child is expected at the venue, joined to its parent, and held to the
            parent's remaining quantity and lifecycle; without it, a child would
            be reported as an order no binding names.

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

    _require_consistent(mapping)

    order_mismatches, compared_orders = _compare_orders(
        _orders_by_id(pipeline), broker, mapping, symbols, tolerances, children
    )
    fill_mismatches, compared_fills = _compare_fills(
        pipeline.execution.reports, broker, mapping, tolerances, children
    )
    remote_by_asset, symbol_mismatches = _resolved_positions(broker, symbols)
    position_mismatches, compared_positions = _compare_positions(
        _positions_by_asset(pipeline), remote_by_asset, tolerances
    )
    cash_mismatches, unreconciled = _compare_cash(pipeline, broker, tolerances)

    return StateReconciliation(
        mismatches=_in_order(
            [
                *order_mismatches,
                *fill_mismatches,
                *symbol_mismatches,
                *position_mismatches,
                *cash_mismatches,
            ]
        ),
        unreconciled=tuple(unreconciled),
        compared_orders=compared_orders,
        compared_fills=compared_fills,
        compared_positions=compared_positions,
    )


def _require_consistent(mapping: ExternalOrderMap, account: str | None = None) -> None:
    """Refuse a binding that disagrees with itself; see :func:`reconcile_execution_state`."""

    where = "" if account is None else f"Account {account!r}: "
    for oms_order_id, broker_order_id in mapping.to_broker.items():
        if mapping.to_oms.get(broker_order_id) != oms_order_id:
            raise LifecycleInputError(
                f"{where}The venue binding maps OMS order {oms_order_id} to {broker_order_id} "
                f"and {broker_order_id} back to "
                f"{mapping.to_oms.get(broker_order_id)!r}. A binding that disagrees with "
                "itself cannot say which order is which, and every comparison under it "
                "would be about the wrong pair."
            )


def _orders_by_id(pipeline: ExecutionPipelineState) -> dict[str, OMSOrder]:
    return {str(order.order_id.value): order for order in pipeline.oms.orders.orders()}


def _positions_by_asset(pipeline: ExecutionPipelineState) -> dict[str, Decimal]:
    return {
        asset_id: position.quantity for asset_id, position in pipeline.portfolio.positions.items()
    }


_CATEGORY_ORDER = {category: index for index, category in enumerate(MismatchCategory)}


def _in_order(mismatches: list[Mismatch]) -> tuple[Mismatch, ...]:
    """Category declaration order, then key, then account, then reason."""

    mismatches.sort(
        key=lambda entry: (
            _CATEGORY_ORDER[entry.category],
            entry.key,
            entry.account or "",
            entry.reason,
        )
    )
    return tuple(mismatches)


# --------------------------------------------------------------------------- #
# One book, several accounts (v3.12, ledger BRK-004)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AccountMirror:
    """One broker account's side of a book spread across several.

    Exactly the arguments :func:`reconcile_execution_state` takes for its one
    account, held per account, because each venue has its own handles, its own
    instrument names and its own algorithm children.

    Attributes:
        broker: The normalized state the adapter for this account filled in.
        mapping: This account's OMS-order-to-venue-handle binding.
        symbols: How this venue's symbols join AlphaLab instruments.
        children: This venue's child handles of algorithm-worked orders, or
            ``None`` when no order was worked in children here.
    """

    broker: BrokerState
    mapping: ExternalOrderMap
    symbols: SymbolMapping
    children: ChildOrderBindings | None = None


def _assignment_mismatch(key: str, declared: str, account: str, what: str) -> Mismatch:
    return Mismatch(
        MismatchCategory.ACCOUNT_ASSIGNMENT_MISMATCH,
        key,
        declared,
        account,
        f"{what} at account {account!r} and the order is declared for account "
        f"{declared!r}; one order working at two venues can be filled twice.",
        account,
    )


def _shares(by_account: Mapping[str, Mapping[str, Decimal]], key: str) -> str:
    """``"a: 10, b: 5"`` -- each account's share of one total, in account order."""

    return ", ".join(
        f"{account}: {values[key]}" for account, values in by_account.items() if key in values
    )


def _compare_cash_totals(
    pipeline: ExecutionPipelineState,
    accounts: Mapping[str, AccountMirror],
    tolerances: ReconciliationTolerances,
    cash_by_currency: Mapping[str, Tolerance],
) -> tuple[list[Mismatch], list[UnreconciledArea]]:
    """The book's balance in each currency against the sum of its accounts' cash."""

    mismatches: list[Mismatch] = []
    unreconciled: list[UnreconciledArea] = []
    totals: dict[str, Decimal] = {}
    held_at: dict[str, dict[str, Decimal]] = {}
    for account, mirror in accounts.items():
        currency = mirror.broker.account.currency
        if not currency.strip():
            unreconciled.append(
                UnreconciledArea(
                    f"account {account} cash",
                    "the broker account names no currency, so its cash balance is a number "
                    "with no unit and nothing in AlphaLab's ledger can be compared to it.",
                )
            )
            continue
        totals[currency] = totals.get(currency, Decimal("0")) + mirror.broker.account.cash
        held_at.setdefault(account, {})[currency] = mirror.broker.account.cash

    for currency in sorted(totals):
        held = pipeline.portfolio.cash.balance(currency)
        tolerance = cash_by_currency.get(currency, tolerances.cash)
        if tolerance.outcome(held, totals[currency]) is ToleranceOutcome.MATERIAL:
            mismatches.append(
                Mismatch(
                    MismatchCategory.ACCOUNT_CASH_MISMATCH,
                    currency,
                    str(held),
                    str(totals[currency]),
                    "the settled cash balances differ by more than the stated tolerance; the "
                    f"accounts hold {_shares(held_at, currency)}.",
                )
            )

    others = sorted(
        currency
        for currency, amount in pipeline.portfolio.cash.balances.items()
        if currency not in totals and amount != Decimal("0.00")
    )
    if others:
        unreconciled.append(
            UnreconciledArea(
                "account cash",
                f"AlphaLab's ledger also holds {others}, and no account supplied is "
                "denominated in them; those balances were not compared to anything, which "
                "is not the same as agreeing.",
            )
        )
    return mismatches, unreconciled


def reconcile_accounts(
    pipeline: ExecutionPipelineState,
    accounts: Mapping[str, AccountMirror],
    assignment: Mapping[str, str],
    tolerances: ReconciliationTolerances,
    *,
    cash_by_currency: Mapping[str, Tolerance] | None = None,
) -> StateReconciliation:
    """Compare one book against every broker account it is spread across.

    Pure, total and deterministic, like :func:`reconcile_execution_state`, which
    this applies account by account; see the module docstring for what is
    compared per account and what in total.

    Args:
        pipeline: AlphaLab's execution state -- the one book.
        accounts: Every account the book is spread across, by account id. All of
            them: positions and cash are compared in total, so an account left
            out would read as a break in everything it holds.
        assignment: ``oms_order_id`` -> the account the order was sent to. The
            caller's own record, stated rather than inferred from the bindings.
        tolerances: How close each compared number has to be, for the whole book.
        cash_by_currency: A cash tolerance per currency, where minor units
            differ; a currency it does not name is judged by ``tolerances.cash``.

    Returns:
        A :class:`StateReconciliation` whose mismatches each name the account
        they were found at (``None`` for a total), and whose
        :attr:`~StateReconciliation.unassigned` lists the orders the assignment
        did not place.

    Raises:
        LifecycleInputError: If no account is supplied, an account id is blank,
            the assignment names an account not supplied, or an account's binding
            disagrees with itself.
    """

    if not accounts:
        raise LifecycleInputError(
            "reconcile_accounts needs at least one account; a book compared with no "
            "broker at all has been compared with nothing."
        )
    for account in accounts:
        if not account.strip():
            raise LifecycleInputError("An account id must not be blank.")
    stray = sorted({account for account in assignment.values() if account not in accounts})
    if stray:
        raise LifecycleInputError(
            f"The assignment sends orders to {stray}, which are not among the accounts "
            f"supplied ({sorted(accounts)}). A book reconciled without one of its accounts "
            "would report everything that account holds as a break."
        )
    for account in sorted(accounts):
        _require_consistent(accounts[account].mapping, account)

    local_orders = _orders_by_id(pipeline)
    reports = pipeline.execution.reports
    expected: dict[str, dict[str, ExecutionReport]] = {account: {} for account in accounts}
    unassigned: set[str] = set()
    for execution_id, report in reports.items():
        declared = assignment.get(report.order_id)
        if declared is None:
            unassigned.add(report.order_id)
        else:
            expected[declared][execution_id] = report

    mismatches: list[Mismatch] = []
    compared_orders = compared_fills = 0
    remote_by_account: dict[str, dict[str, Decimal]] = {}
    for account in sorted(accounts):
        mirror = accounts[account]
        found: list[Mismatch] = []

        bound: list[str] = []
        for oms_order_id in mirror.mapping.to_broker:
            declared = assignment.get(oms_order_id)
            if oms_order_id not in local_orders or declared == account:
                bound.append(oms_order_id)
            elif declared is None:
                unassigned.add(oms_order_id)
            else:
                found.append(
                    _assignment_mismatch(
                        oms_order_id,
                        declared,
                        account,
                        f"the order is bound as {mirror.mapping.to_broker[oms_order_id]}",
                    )
                )
        parents: list[str] = []
        if mirror.children is not None:
            for parent_id, handles in mirror.children.by_parent.items():
                declared = assignment.get(parent_id)
                if parent_id not in local_orders or declared == account:
                    parents.append(parent_id)
                elif declared is None:
                    unassigned.add(parent_id)
                else:
                    found.append(
                        _assignment_mismatch(
                            parent_id,
                            declared,
                            account,
                            f"the order's children {', '.join(handles)} are working",
                        )
                    )

        order_mismatches, compared = _compare_orders(
            local_orders,
            mirror.broker,
            mirror.mapping,
            mirror.symbols,
            tolerances,
            mirror.children,
            bound=bound,
            parents=parents,
        )
        compared_orders += compared
        fill_mismatches, compared = _compare_fills(
            expected[account],
            mirror.broker,
            mirror.mapping,
            tolerances,
            mirror.children,
            book_reports=reports,
        )
        compared_fills += compared
        for execution_id in sorted(mirror.broker.executions):
            held = reports.get(execution_id)
            if held is None or execution_id in expected[account]:
                continue
            declared = assignment.get(held.order_id)
            if declared is not None:
                found.append(
                    _assignment_mismatch(
                        execution_id,
                        declared,
                        account,
                        f"the fill was reported against order {held.order_id}",
                    )
                )

        remote_by_account[account], symbol_mismatches = _resolved_positions(
            mirror.broker, mirror.symbols
        )
        found.extend((*order_mismatches, *fill_mismatches, *symbol_mismatches))
        mismatches.extend(replace(entry, account=account) for entry in found)

    totals: dict[str, Decimal] = {}
    for held_here in remote_by_account.values():
        for asset_id, quantity in held_here.items():
            totals[asset_id] = totals.get(asset_id, Decimal("0")) + quantity
    position_mismatches, compared_positions = _compare_positions(
        _positions_by_asset(pipeline),
        totals,
        tolerances,
        {asset_id: _shares(remote_by_account, asset_id) for asset_id in totals},
    )
    cash_mismatches, unreconciled = _compare_cash_totals(
        pipeline, accounts, tolerances, cash_by_currency or {}
    )

    return StateReconciliation(
        mismatches=_in_order([*mismatches, *position_mismatches, *cash_mismatches]),
        unreconciled=tuple(unreconciled),
        compared_orders=compared_orders,
        compared_fills=compared_fills,
        compared_positions=compared_positions,
        unassigned=tuple(sorted(unassigned)),
    )
