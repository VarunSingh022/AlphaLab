"""Routing an order to a venue, and bringing its fills back.

The execution path ends at an accepted OMS order. What happens next is the only
thing that genuinely differs between a simulated environment and a live one:

* **Backtest, replay, paper** -- the order executes against
  :class:`~alphalab.execution.simulator.ExecutionSimulator`, and a
  :class:`~alphalab.execution.policy.FillPolicy` decides the outcome from the
  liquidity the market event showed.
* **Live** -- the order is sent to a venue through the canonical broker
  boundary, and the venue decides. Its answer arrives later, out of band.

This module is that second path, and it is deliberately two separate halves,
because outbound and inbound are separate in time:

    OMS order --> route_order()          --> BrokerOrder at the venue
    BrokerExecution --> apply_broker_execution() --> Fill, portfolio, analytics

Both halves end in the *same* canonical types the simulated path uses. A live
fill becomes an :class:`~alphalab.execution.report.ExecutionReport` and is
applied by
:meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_execution_report`
-- the function a simulated fill goes through. There is no live-only order
model, no live-only fill model, and no live-only portfolio accounting.

What is implemented, and what is not
------------------------------------

Implemented and tested here, and unchanged since v2.3: the mapping in both
directions, the pre-trade gates, and idempotent submission.

A transport to a real venue is an adapter, and an adapter is the application's:
it holds the venue's credentials and speaks its protocol. From v2.14 to v3.10
the library carried one, a REST adapter over authenticated HTTP; v3.11 moved it
into the test suite as a worked example (ledger BRK-007), where it still routes
through the functions below without their knowing which adapter they have --
because it is a :class:`~alphalab.broker.protocol.BrokerProtocol`.
:class:`~alphalab.broker.paper.PaperBroker` remains the reference simulation.
See ``docs/ARCHITECTURE.md`` and ADR-0031.

Pre-trade gates
---------------

Two refusals, both about not doing damage, and since v3.9 a third that is
about not sending what the venue cannot take:

* **DISCONNECTED** -- an order is never sent on a connection that is not
  ``CONNECTED``. A reconnecting adapter has not confirmed what the venue holds,
  so sending into it risks duplicating an order it already has.
* **DUPLICATE_SUBMISSION** -- an OMS order already bound to a venue handle is
  never sent again. This is the idempotency guarantee: retrying a submission
  whose response was lost must not create a second order, and the binding in
  :class:`~alphalab.broker.reconciliation.ExternalOrderMap` is what makes the
  retry observable.
* **CAPABILITY_MISMATCH** -- the caller checked the order's requirements against
  the venue's declared capabilities
  (:func:`~alphalab.core.capabilities.check_compatibility`) and the report is not
  ``COMPATIBLE``. An ``UNDETERMINED`` report refuses too: a capability nobody
  declared is not one the venue has.

Algorithmic children (v3.9)
---------------------------

An execution algorithm (:mod:`alphalab.execution.algorithms`) works one OMS
order -- the parent, which holds the allocation reservation and the strategy
contributions -- as a sequence of child orders at the venue.
:func:`route_child_order` sends one. Its handle is derived from the parent's and
the child's sequence (:func:`child_broker_order_id`), so a retried child
addresses the same venue order, and its fills settle on the **parent** through
:func:`apply_broker_execution` exactly as a directly routed fill does: the OMS,
the portfolio, the reservation and the attribution see one order, however many
pieces it traded in.

:class:`~alphalab.broker.reconciliation.ExternalOrderMap` stays one-to-one; it
is captured in the broker snapshot, and a parent bound to many handles would
change what that snapshot means. Children are bound in a
:class:`ChildOrderBindings` instead -- many handles to one parent -- which is a
value the caller holds and, because every child handle encodes its parent,
can be rebuilt from the snapshotted mirror with
:meth:`ChildOrderBindings.from_mirror`. One parent is worked one way: a parent
already routed directly takes no children.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto

from alphalab.broker.adapter import BrokerAdapter
from alphalab.broker.events import BrokerEvent
from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.protocol import BrokerProtocol
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.capabilities import CompatibilityReport
from alphalab.core.enums import OrderType
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.algorithms import ChildOrder
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.oms.order import Order as OMSOrder
from alphalab.portfolio.fx import NO_RATES, FxRates
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState

__all__ = [
    "ChildOrderBindings",
    "ChildRoutingResult",
    "RoutingConfig",
    "RoutingDecision",
    "RoutingRefusal",
    "RoutingResult",
    "apply_broker_execution",
    "broker_order_id_for",
    "child_broker_order_id",
    "execution_report_from_broker",
    "routable",
    "route_child_order",
    "route_order",
]


class RoutingRefusal(Enum):
    """Why an order was not sent to the venue."""

    #: The connection is not in a state that can carry an order.
    DISCONNECTED = auto()

    #: This OMS order is already bound to a venue handle. Sending it again
    #: would create a second order at the venue for one AlphaLab order.
    DUPLICATE_SUBMISSION = auto()

    #: The capability check the caller supplied is not ``COMPATIBLE``. (v3.9)
    CAPABILITY_MISMATCH = auto()

    #: The child does not belong to the parent it names, the parent is
    #: finished, or the child would put more at the venue than the parent has
    #: left. (v3.9)
    INVALID_CHILD = auto()

    #: The parent is already at the venue as one order; it is not also worked
    #: in children. (v3.9)
    PARENT_ROUTED_DIRECTLY = auto()


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    """How this session addresses and denominates orders at the venue.

    Attributes:
        venue: Venue label recorded on execution reports.
        currency: Currency execution reports are denominated in. A venue fill
            does not say, so this is what it settles in -- and a currency the
            pipeline does not settle is refused, never converted.
        order_type: The venue order type a *market* order is sent as. Defaults
            to ``MARKET``. Since v3.11 an order that asked for a limit, a stop or
            a stop-limit is sent as what it asked for (ledger EXE-003); until
            then every order was a market order, and this was the type all of
            them were sent as. See :func:`venue_order_type`.

    ``venue`` and ``currency`` are required. Until v3.10 they defaulted to
    ``"LIVE"`` and ``"USD"``, and every routing function took the whole
    configuration as optional, so a venue fill could be labelled with a venue
    nobody named and denominated in a currency nobody chose (ledger API-003).
    """

    venue: str
    currency: str
    order_type: OrderType = OrderType.MARKET


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Whether an order was sent, and why not if it was not."""

    routed: bool
    refusal: RoutingRefusal | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RoutingResult:
    """Everything one routing attempt produced."""

    broker_state: BrokerState
    mapping: ExternalOrderMap
    order: BrokerOrder | None
    decision: RoutingDecision
    events: tuple[BrokerEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class _RoutableOrder:
    """The OMS order as :class:`~alphalab.broker.adapter.OMSOrderProtocol` sees it.

    :mod:`alphalab.broker` deliberately depends on a structural protocol rather
    than importing :class:`alphalab.oms.order.Order`, so the broker layer stays
    usable without the OMS. The real order does not satisfy that protocol as it
    stands -- its ``order_id`` is an :class:`~alphalab.oms.ids.OrderId`, not a
    string, and it has no single ``price`` -- so the translation happens here,
    explicitly, which is what an adapter boundary is for.
    """

    order_id: str
    asset_id: str
    side: str
    quantity: Decimal
    price: Decimal


def routable(oms_order: OMSOrder) -> _RoutableOrder:
    """Project an OMS order onto the shape the broker adapter accepts.

    ``price`` resolves in the order a venue would want it: the order's own limit
    price if it has one, otherwise the reference price the allocation engine
    sized it at (carried in ``metadata``), otherwise zero. A market order has no
    limit, so without the reference price it would route at nothing.
    """

    price = oms_order.limit_price
    if price is None:
        reference = oms_order.metadata.get("reference_price")
        price = Decimal(reference) if reference is not None else Decimal("0")

    return _RoutableOrder(
        order_id=str(oms_order.order_id.value),
        asset_id=oms_order.asset_id,
        side=oms_order.side.name,
        quantity=oms_order.remaining_quantity,
        price=price,
    )


def venue_order_type(oms_order: OMSOrder, routing: RoutingConfig) -> OrderType:
    """The order type a venue is sent an OMS order as.

    A limit, stop or stop-limit order is sent as itself: its terms are what the
    strategy asked for, and a venue executing it as anything else would be
    executing another order (ledger EXE-003). A market order is sent as the
    session's :attr:`RoutingConfig.order_type` -- ``MARKET`` unless the session
    names the type a venue wants a market order expressed as, which is what that
    setting has meant since v2.15.
    """

    if oms_order.order_type is OrderType.MARKET:
        return routing.order_type
    return oms_order.order_type


def broker_order_id_for(oms_order: OMSOrder) -> str:
    """The client handle an OMS order is addressed by at a venue.

    Derived from the OMS order id rather than freshly minted, so a retry after a
    lost response addresses the *same* order at the venue instead of creating a
    second one. Determinism here is a safety property, not a convenience.
    """

    return f"ALB-{oms_order.order_id.value}"


def _capability_refusal(capability: CompatibilityReport | None) -> RoutingDecision | None:
    if capability is None or capability.compatible:
        return None
    return RoutingDecision(
        False,
        RoutingRefusal.CAPABILITY_MISMATCH,
        f"The venue's declared capabilities are {capability.status} for this order: "
        f"{'; '.join(capability.gaps)}",
    )


def route_order(
    broker_state: BrokerState,
    broker: BrokerProtocol,
    oms_order: OMSOrder,
    timestamp: float,
    mapping: ExternalOrderMap | None = None,
    *,
    config: RoutingConfig,
    capability: CompatibilityReport | None = None,
) -> RoutingResult:
    """Send one accepted OMS order to the venue, or refuse to.

    Refusing leaves ``broker_state`` and ``mapping`` untouched, so a caller can
    retry after reconnecting without having half-applied anything.

    ``capability`` is the caller's check of this order against the venue's
    declaration; ``None`` means none was made, which is what every caller before
    v3.9 did, and changes nothing.
    """

    routing = config
    identities = mapping if mapping is not None else ExternalOrderMap()
    oms_order_id = str(oms_order.order_id.value)

    if not broker.status(broker_state).can_trade:
        return RoutingResult(
            broker_state,
            identities,
            None,
            RoutingDecision(
                False,
                RoutingRefusal.DISCONNECTED,
                f"Connection is {broker.status(broker_state).name}; orders are not sent "
                f"until it is CONNECTED.",
            ),
        )

    already = identities.broker_id_for(oms_order_id)
    if already is not None:
        return RoutingResult(
            broker_state,
            identities,
            None,
            RoutingDecision(
                False,
                RoutingRefusal.DUPLICATE_SUBMISSION,
                f"OMS order {oms_order_id} is already at the venue as {already}.",
            ),
        )

    refused = _capability_refusal(capability)
    if refused is not None:
        return RoutingResult(broker_state, identities, None, refused)

    broker_order = BrokerAdapter.to_broker_order(
        routable(oms_order),
        broker_order_id_for(oms_order),
        venue_order_type(oms_order, routing),
        timestamp,
        stop_price=oms_order.stop_price,
        time_in_force=oms_order.time_in_force,
    )
    new_state, events = broker.submit_order(broker_state, broker_order, timestamp)

    return RoutingResult(
        new_state,
        identities.bind_order(broker_order),
        broker_order,
        RoutingDecision(True),
        events,
    )


# --------------------------------------------------------------------------- #
# v3.9: algorithmic children
# --------------------------------------------------------------------------- #


def child_broker_order_id(parent: OMSOrder, sequence: int) -> str:
    """The venue handle of a parent's ``sequence``-th child: ``<parent handle>.<sequence>``.

    Derived, like :func:`broker_order_id_for`, so a child retried after a lost
    response addresses the same venue order -- and so its parent can be read
    back from the handle alone.
    """

    return f"{broker_order_id_for(parent)}.{sequence}"


def _parse_child_handle(handle: str) -> tuple[str, int] | None:
    """``(parent oms_order_id, sequence)`` from a child handle, or ``None``."""

    if not handle.startswith("ALB-") or "." not in handle:
        return None
    parent, _, sequence = handle[len("ALB-") :].rpartition(".")
    if not parent or not sequence.isdigit() or sequence.startswith("0"):
        return None
    return parent, int(sequence)


@dataclass(frozen=True, slots=True)
class ChildOrderBindings:
    """Which venue handles are an algorithm's children, and of which OMS order.

    Many handles to one parent: the relation
    :class:`~alphalab.broker.reconciliation.ExternalOrderMap` deliberately cannot
    hold. Rebinding a handle to a second parent is refused, for the reason the
    map refuses it: one order's fills landing on another.

    Attributes:
        to_parent: Child handle -> parent ``oms_order_id``.
        by_parent: Parent ``oms_order_id`` -> its child handles, in binding order.
    """

    to_parent: PersistentMap[str, str] = field(default_factory=PersistentMap)
    by_parent: PersistentMap[str, tuple[str, ...]] = field(default_factory=PersistentMap)

    def bind(self, handle: str, parent_oms_order_id: str) -> ChildOrderBindings:
        """Record that ``handle`` is a child of ``parent_oms_order_id``."""

        if not handle or not parent_oms_order_id:
            raise BrokerValidationError("Both identifiers are required to bind a child.")
        existing = self.to_parent.get(handle)
        if existing is not None:
            if existing != parent_oms_order_id:
                raise BrokerValidationError(
                    f"Child {handle} is bound to parent {existing}; refusing to rebind it to "
                    f"{parent_oms_order_id}."
                )
            return self
        return ChildOrderBindings(
            self.to_parent.set(handle, parent_oms_order_id),
            self.by_parent.set(
                parent_oms_order_id, (*self.by_parent.get(parent_oms_order_id, ()), handle)
            ),
        )

    def parent_of(self, handle: str) -> str | None:
        """The parent a child handle belongs to, or ``None``."""

        return self.to_parent.get(handle)

    def children_of(self, parent_oms_order_id: str) -> tuple[str, ...]:
        """A parent's child handles, in binding order."""

        return self.by_parent.get(parent_oms_order_id, ())

    @classmethod
    def from_mirror(cls, state: BrokerState) -> ChildOrderBindings:
        """Rebuild the bindings from a mirror, after a restart.

        Every child handle encodes its parent, and every mirror order records the
        OMS order it represents; a mirror order whose handle is a child handle of
        the very parent it names is a child. Nothing else is.
        """

        bindings = cls()
        for handle, order in state.orders.items():
            parsed = _parse_child_handle(handle)
            if parsed is not None and parsed[0] == order.oms_order_id:
                bindings = bindings.bind(handle, order.oms_order_id)
        return bindings


@dataclass(frozen=True, slots=True)
class ChildRoutingResult:
    """Everything one child-routing attempt produced."""

    broker_state: BrokerState
    children: ChildOrderBindings
    order: BrokerOrder | None
    decision: RoutingDecision
    events: tuple[BrokerEvent, ...] = ()


def _working_at_venue(
    broker_state: BrokerState, children: ChildOrderBindings, parent_id: str
) -> Decimal:
    working = Decimal("0")
    for handle in children.children_of(parent_id):
        order = broker_state.orders.get(handle)
        if order is not None and not order.is_terminal:
            working += order.remaining_quantity
    return working


def route_child_order(
    broker_state: BrokerState,
    broker: BrokerProtocol,
    parent: OMSOrder,
    child: ChildOrder,
    timestamp: float,
    *,
    mapping: ExternalOrderMap,
    children: ChildOrderBindings,
    config: RoutingConfig,
    capability: CompatibilityReport | None,
) -> ChildRoutingResult:
    """Send one algorithm child to the venue on behalf of its parent OMS order, or refuse to.

    The gates, in order: the connection must be up; the parent must not be at
    the venue as one order already; the child must be the parent's -- same
    order, asset and side -- the parent must still be working, and the child
    must not put more at the venue than the parent has left unfilled; the child
    must not already be bound; and a supplied capability check must be
    ``COMPATIBLE``. Refusing leaves everything untouched.

    The venue order represents the parent -- its ``oms_order_id`` is the
    parent's -- at the child's quantity, order type and limit.
    """

    parent_id = str(parent.order_id.value)

    def refuse(refusal: RoutingRefusal, reason: str) -> ChildRoutingResult:
        return ChildRoutingResult(
            broker_state, children, None, RoutingDecision(False, refusal, reason)
        )

    if not broker.status(broker_state).can_trade:
        return refuse(
            RoutingRefusal.DISCONNECTED,
            f"Connection is {broker.status(broker_state).name}; children are not sent until "
            "it is CONNECTED.",
        )
    direct = mapping.broker_id_for(parent_id)
    if direct is not None:
        return refuse(
            RoutingRefusal.PARENT_ROUTED_DIRECTLY,
            f"Parent {parent_id} is already at the venue as {direct}; it is not also worked "
            "in children.",
        )
    if (
        child.parent_order_id != parent_id
        or child.asset_id != parent.asset_id
        or child.side is not parent.side
    ):
        return refuse(
            RoutingRefusal.INVALID_CHILD,
            f"Child {child.child_id} names parent {child.parent_order_id} on {child.asset_id} "
            f"{child.side}; the parent is {parent_id} on {parent.asset_id} {parent.side}.",
        )
    if parent.is_closed:
        return refuse(
            RoutingRefusal.INVALID_CHILD,
            f"Parent {parent_id} is {parent.status}; a finished order takes no children.",
        )
    working = _working_at_venue(broker_state, children, parent_id)
    if working + child.quantity > parent.remaining_quantity:
        return refuse(
            RoutingRefusal.INVALID_CHILD,
            f"Child {child.child_id} for {child.quantity} would put "
            f"{working + child.quantity} at the venue for parent {parent_id}, which has "
            f"{parent.remaining_quantity} left unfilled.",
        )
    handle = child_broker_order_id(parent, child.sequence)
    if children.parent_of(handle) is not None or handle in broker_state.orders:
        return refuse(
            RoutingRefusal.DUPLICATE_SUBMISSION,
            f"Child {child.child_id} is already at the venue as {handle}.",
        )
    refused = _capability_refusal(capability)
    if refused is not None:
        return ChildRoutingResult(broker_state, children, None, refused)

    price = child.limit_price
    if price is None:
        reference = parent.metadata.get("reference_price")
        price = Decimal(reference) if reference is not None else Decimal("0")
    projected = _RoutableOrder(
        order_id=parent_id,
        asset_id=parent.asset_id,
        side=parent.side.name,
        quantity=child.quantity,
        price=price,
    )
    broker_order = BrokerAdapter.to_broker_order(projected, handle, child.order_type, timestamp)
    new_state, events = broker.submit_order(broker_state, broker_order, timestamp)
    return ChildRoutingResult(
        new_state,
        children.bind(handle, parent_id),
        broker_order,
        RoutingDecision(True),
        events,
    )


def execution_report_from_broker(
    execution: BrokerExecution,
    oms_order: OMSOrder,
    config: RoutingConfig,
) -> ExecutionReport:
    """Turn a venue fill into the execution report the portfolio consumes.

    The status is derived from the OMS order rather than taken from the venue:
    a fill is FULL_FILL exactly when it leaves nothing working, which is what
    the OMS lifecycle means by filled. Slippage is ``0`` and the liquidity flag
    is empty because a venue fill measures neither -- absent, not zero.
    """

    routing = config
    completes = execution.fill_quantity >= oms_order.remaining_quantity

    return ExecutionReport(
        execution_id=execution.execution_id,
        order_id=str(oms_order.order_id.value),
        asset_id=oms_order.asset_id,
        strategy_id=oms_order.strategy_id,
        timestamp=execution.timestamp,
        fill_price=execution.fill_price,
        fill_quantity=execution.fill_quantity,
        commission=execution.commission,
        slippage=Decimal("0"),
        liquidity_flag="",
        venue=routing.venue,
        currency=routing.currency,
        status=FillStatus.FULL_FILL if completes else FillStatus.PARTIAL_FILL,
    )


def apply_broker_execution(
    state: ExecutionPipelineState,
    oms_order: OMSOrder,
    execution: BrokerExecution,
    config: RoutingConfig,
    rates: FxRates = NO_RATES,
) -> tuple[ExecutionPipelineState, tuple[CoreFill, ...], tuple[CoreTrade, ...]]:
    """Apply a venue fill through the canonical execution path.

    The fill reaches the OMS, the portfolio, the allocation ledger and the
    analytics record by exactly the route a simulated fill takes -- see
    :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_execution_report`.

    ``rates`` is passed straight through to that method and is only read when
    the book this fill lands in holds more than one currency. It defaults to the
    empty table, so a single-currency live run is unchanged.
    """

    return ExecutionPipeline.apply_execution_report(
        state, oms_order, execution_report_from_broker(execution, oms_order, config), rates
    )
