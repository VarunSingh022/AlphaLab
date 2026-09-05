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

Implemented and tested here: the mapping in both directions, the pre-trade
gates, and idempotent submission. Not implemented anywhere in AlphaLab: a
transport to any real venue. There is no vendor connectivity in this
repository, so "live" means this contract driven by an adapter someone else
supplies -- :class:`~alphalab.broker.paper.PaperBroker` is the only adapter
that exists, and it is a simulation. See ``docs/ARCHITECTURE.md`` for the
distinction between implemented, adapter-only, and future work.

Pre-trade gates
---------------

Two refusals, both about not doing damage:

* **DISCONNECTED** -- an order is never sent on a connection that is not
  ``CONNECTED``. A reconnecting adapter has not confirmed what the venue holds,
  so sending into it risks duplicating an order it already has.
* **DUPLICATE_SUBMISSION** -- an OMS order already bound to a venue handle is
  never sent again. This is the idempotency guarantee: retrying a submission
  whose response was lost must not create a second order, and the binding in
  :class:`~alphalab.broker.reconciliation.ExternalOrderMap` is what makes the
  retry observable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.broker.adapter import BrokerAdapter
from alphalab.broker.events import BrokerEvent
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.protocol import BrokerProtocol
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState
from alphalab.core.enums import OrderType
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState

__all__ = [
    "RoutingConfig",
    "RoutingDecision",
    "RoutingRefusal",
    "RoutingResult",
    "apply_broker_execution",
    "broker_order_id_for",
    "execution_report_from_broker",
    "routable",
    "route_order",
]


class RoutingRefusal(Enum):
    """Why an order was not sent to the venue."""

    #: The connection is not in a state that can carry an order.
    DISCONNECTED = auto()

    #: This OMS order is already bound to a venue handle. Sending it again
    #: would create a second order at the venue for one AlphaLab order.
    DUPLICATE_SUBMISSION = auto()


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    """How this session addresses and denominates orders at the venue.

    Attributes:
        venue: Venue label recorded on execution reports.
        currency: Currency execution reports are denominated in.
        order_type: Order instruction used when routing. Defaults to ``MARKET``,
            matching what the execution path submits to the OMS.
    """

    venue: str = "LIVE"
    currency: str = "USD"
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


def broker_order_id_for(oms_order: OMSOrder) -> str:
    """The client handle an OMS order is addressed by at a venue.

    Derived from the OMS order id rather than freshly minted, so a retry after a
    lost response addresses the *same* order at the venue instead of creating a
    second one. Determinism here is a safety property, not a convenience.
    """

    return f"ALB-{oms_order.order_id.value}"


def route_order(
    broker_state: BrokerState,
    broker: BrokerProtocol,
    oms_order: OMSOrder,
    timestamp: float,
    mapping: ExternalOrderMap | None = None,
    config: RoutingConfig | None = None,
) -> RoutingResult:
    """Send one accepted OMS order to the venue, or refuse to.

    Refusing leaves ``broker_state`` and ``mapping`` untouched, so a caller can
    retry after reconnecting without having half-applied anything.
    """

    routing = config if config is not None else RoutingConfig()
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

    broker_order = BrokerAdapter.to_broker_order(
        routable(oms_order), broker_order_id_for(oms_order), routing.order_type, timestamp
    )
    new_state, events = broker.submit_order(broker_state, broker_order, timestamp)

    return RoutingResult(
        new_state,
        identities.bind_order(broker_order),
        broker_order,
        RoutingDecision(True),
        events,
    )


def execution_report_from_broker(
    execution: BrokerExecution,
    oms_order: OMSOrder,
    config: RoutingConfig | None = None,
) -> ExecutionReport:
    """Turn a venue fill into the execution report the portfolio consumes.

    The status is derived from the OMS order rather than taken from the venue:
    a fill is FULL_FILL exactly when it leaves nothing working, which is what
    the OMS lifecycle means by filled. Slippage is ``0`` and the liquidity flag
    is empty because a venue fill measures neither -- absent, not zero.
    """

    routing = config if config is not None else RoutingConfig()
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
    config: RoutingConfig | None = None,
) -> tuple[ExecutionPipelineState, tuple[CoreFill, ...], tuple[CoreTrade, ...]]:
    """Apply a venue fill through the canonical execution path.

    The fill reaches the OMS, the portfolio, the allocation ledger and the
    analytics record by exactly the route a simulated fill takes -- see
    :meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.apply_execution_report`.
    """

    return ExecutionPipeline.apply_execution_report(
        state, oms_order, execution_report_from_broker(execution, oms_order, config)
    )
