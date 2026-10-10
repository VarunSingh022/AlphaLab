"""The runtime joins of the v3.9 contract: the capability gate, and algorithmic children."""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.broker.exceptions import BrokerValidationError
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState
from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    AccountCapability,
    Capability,
    CapabilityDeclaration,
    CompatibilityReport,
    MarketCapability,
    Support,
    check_compatibility,
    order_requirements,
)
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import AssetType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.core.order_request import OrderRequest
from alphalab.execution.algorithms import (
    AlgorithmTerms,
    ChildOrder,
    Slicing,
    record_child_execution,
    release_children,
    start_algorithm,
)
from alphalab.execution.costs import FREE
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.broker_routing import (
    ChildOrderBindings,
    ChildRoutingResult,
    RoutingConfig,
    RoutingRefusal,
    apply_broker_execution,
    broker_order_id_for,
    child_broker_order_id,
    route_child_order,
    route_order,
)
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
CONFIG = RoutingConfig(venue="VENUE", currency="USD", order_type=OrderType.LIMIT)


def _working(quantity: str = "10") -> tuple[ExecutionPipelineState, OMSOrder]:
    config = replace(pipeline_config(STRATEGY_ID), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal(quantity)})
        ),
        1.0,
    )
    state = ExecutionPipeline.process_quote(
        state, sized_quote(ASSET_ID, 2.0, Decimal("100"), Decimal("100")), context_factory
    ).state
    return state, next(iter(state.oms.orders.open_orders()))


def _venue(connected: bool = True) -> BrokerState:
    state = BrokerEngine.initialize("VENUE", Decimal("1000000"), "USD")
    if connected:
        state, _ = PaperBroker(FREE).connect(state, 1.0)
    return state


def _declaration(order_types: frozenset[OrderType]) -> CapabilityDeclaration:
    return CapabilityDeclaration(
        "venue",
        frozenset({Capability.CANCEL_REPLACE}),
        frozenset(),
        (
            MarketCapability(
                AssetType.EQUITY,
                ANY_LISTING_VENUE,
                order_types,
                frozenset({TimeInForce.DAY}),
                Support.SUPPORTED,
                Support.SUPPORTED,
                Support.UNSUPPORTED,
                Support.UNSUPPORTED,
            ),
        ),
        frozenset(),
        (
            AccountCapability(
                "ACC-1", frozenset({AssetType.EQUITY}), Support.UNSUPPORTED, Support.SUPPORTED
            ),
        ),
    )


def _check(
    order_types: frozenset[OrderType], order_type: OrderType = OrderType.LIMIT
) -> CompatibilityReport:
    requirements = order_requirements(
        asset_class=AssetType.EQUITY,
        listing_venue="XNAS",
        account_id="ACC-1",
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        side=Side.BUY,
        quantity=Decimal("10"),
        position_before=Decimal("0"),
        extended_hours=False,
        bracket_orders=False,
        margin=False,
        features=frozenset(),
    )
    return check_compatibility(requirements, _declaration(order_types))


# --------------------------------------------------------------------------- #
# The capability gate on route_order
# --------------------------------------------------------------------------- #


def test_a_compatible_order_routes_and_no_check_changes_nothing() -> None:
    _, order = _working()
    checked = route_order(
        _venue(),
        PaperBroker(FREE),
        order,
        3.0,
        config=CONFIG,
        capability=_check(frozenset({OrderType.LIMIT})),
    )
    unchecked = route_order(_venue(), PaperBroker(FREE), order, 3.0, config=CONFIG)
    assert checked.decision.routed and unchecked.decision.routed
    assert checked.order == unchecked.order


def test_an_incompatible_order_is_refused_before_anything_is_sent() -> None:
    _, order = _working()
    venue = _venue()
    result = route_order(
        venue,
        PaperBroker(FREE),
        order,
        3.0,
        config=CONFIG,
        capability=_check(frozenset({OrderType.MARKET})),
    )
    assert result.decision.refusal is RoutingRefusal.CAPABILITY_MISMATCH
    assert "order_type" not in result.decision.reason or "limit" in result.decision.reason
    assert result.broker_state is venue and result.order is None
    assert result.mapping == ExternalOrderMap()


def test_an_undetermined_check_refuses_too() -> None:
    _, order = _working()
    report = check_compatibility(
        replace(_check(frozenset({OrderType.LIMIT})).requirements, account_id="UNKNOWN"),
        _declaration(frozenset({OrderType.LIMIT})),
    )
    result = route_order(_venue(), PaperBroker(FREE), order, 3.0, config=CONFIG, capability=report)
    assert result.decision.refusal is RoutingRefusal.CAPABILITY_MISMATCH
    assert "undetermined" in result.decision.reason


# --------------------------------------------------------------------------- #
# Child bindings
# --------------------------------------------------------------------------- #


def test_child_handles_are_derived_from_the_parent() -> None:
    _, order = _working()
    assert child_broker_order_id(order, 3) == f"{broker_order_id_for(order)}.3"


def test_bindings_are_many_to_one_and_refuse_rebinding() -> None:
    bindings = ChildOrderBindings().bind("ALB-P.1", "P").bind("ALB-P.2", "P")
    assert bindings.children_of("P") == ("ALB-P.1", "ALB-P.2")
    assert bindings.parent_of("ALB-P.2") == "P"
    assert bindings.bind("ALB-P.1", "P") is bindings
    with pytest.raises(BrokerValidationError, match="refusing to rebind"):
        bindings.bind("ALB-P.1", "Q")
    with pytest.raises(BrokerValidationError, match="required"):
        bindings.bind("", "P")


# --------------------------------------------------------------------------- #
# Routing children
# --------------------------------------------------------------------------- #


def _parent_request(order: OMSOrder) -> OrderRequest:
    return OrderRequest(
        order_id=str(order.order_id.value),
        strategy_id=order.strategy_id,
        asset_id=order.asset_id,
        side=order.side,
        quantity=order.quantity,
        price=Decimal("100"),
        timestamp=2.0,
        contributions=(StrategyContribution(STRATEGY_ID, order.quantity),),
    )


def _children(order: OMSOrder, slices: int = 2) -> list[ChildOrder]:
    terms = AlgorithmTerms(2.0, 100.0, Decimal("1"), OrderType.LIMIT, Decimal("100"))
    state = start_algorithm(_parent_request(order), Slicing(None, slices), terms)
    released: list[ChildOrder] = []
    for step in range(slices):
        state, children = release_children(state, 3.0 + step)
        released.extend(children)
        for child in children:
            state = record_child_execution(
                state, child.child_id, f"sim-{child.sequence}", child.quantity, 3.0 + step
            )
    return released


def _route(
    venue: BrokerState,
    order: OMSOrder,
    child: ChildOrder,
    bindings: ChildOrderBindings | None = None,
    mapping: ExternalOrderMap | None = None,
) -> ChildRoutingResult:
    return route_child_order(
        venue,
        PaperBroker(FREE),
        order,
        child,
        4.0,
        mapping=mapping or ExternalOrderMap(),
        children=bindings or ChildOrderBindings(),
        config=CONFIG,
        capability=None,
    )


def test_a_child_is_a_venue_order_for_its_parent_at_the_childs_terms() -> None:
    _, order = _working()
    first, _ = _children(order)
    result = _route(_venue(), order, first)
    assert result.decision.routed
    sent = result.order
    assert sent is not None
    assert sent.broker_order_id == child_broker_order_id(order, 1)
    assert sent.oms_order_id == str(order.order_id.value)
    assert (sent.quantity, sent.order_type, sent.price) == (
        first.quantity,
        OrderType.LIMIT,
        Decimal("100"),
    )
    assert result.children.parent_of(sent.broker_order_id) == str(order.order_id.value)


def test_child_routing_refuses_what_it_must() -> None:
    _, order = _working()
    first, second = _children(order)
    venue = _venue()

    assert _route(_venue(connected=False), order, first).decision.refusal is (
        RoutingRefusal.DISCONNECTED
    )
    direct = ExternalOrderMap().bind(str(order.order_id.value), broker_order_id_for(order))
    assert _route(venue, order, first, mapping=direct).decision.refusal is (
        RoutingRefusal.PARENT_ROUTED_DIRECTLY
    )
    stranger = replace(first, parent_order_id=str(uuid.uuid4()))
    assert _route(venue, order, stranger).decision.refusal is RoutingRefusal.INVALID_CHILD
    too_big = replace(first, quantity=order.quantity + 1)
    assert _route(venue, order, too_big).decision.refusal is RoutingRefusal.INVALID_CHILD

    sent = _route(venue, order, first)
    again = _route(sent.broker_state, order, first, bindings=sent.children)
    assert again.decision.refusal is RoutingRefusal.DUPLICATE_SUBMISSION

    finished = replace(order, status=OrderStatus.CANCELLED)
    assert _route(venue, finished, second).decision.refusal is RoutingRefusal.INVALID_CHILD


def test_children_together_may_not_exceed_what_the_parent_has_left() -> None:
    _, order = _working("10")
    first, second = _children(order)
    sent = _route(_venue(), order, first)
    bigger_second = replace(second, quantity=order.remaining_quantity - first.quantity + 1)
    refused = _route(sent.broker_state, order, bigger_second, bindings=sent.children)
    assert refused.decision.refusal is RoutingRefusal.INVALID_CHILD
    fits = _route(sent.broker_state, order, second, bindings=sent.children)
    assert fits.decision.routed


def test_child_fills_settle_on_the_parent_through_the_canonical_path() -> None:
    state, order = _working("10")
    first, second = _children(order)
    venue, bindings = _venue(), ChildOrderBindings()
    for child in (first, second):
        routed = _route(venue, order, child, bindings=bindings)
        venue, bindings = routed.broker_state, routed.children

    for child in (first, second):
        handle = child_broker_order_id(order, child.sequence)
        fill = BrokerExecution(
            execution_id=f"VX-{child.sequence}",
            broker_order_id=handle,
            symbol=ASSET_ID,
            fill_quantity=child.quantity,
            fill_price=Decimal("100"),
            commission=Decimal("0"),
            timestamp=5.0 + child.sequence,
        )
        parent = state.oms.orders.find(order.order_id)
        state, fills, _ = apply_broker_execution(state, parent, fill, CONFIG)
        assert len(fills) == 1
        assert fills[0].order_id == str(order.order_id.value)

    parent = state.oms.orders.find(order.order_id)
    assert parent.status is OrderStatus.FILLED
    assert parent.filled_quantity == Decimal("10")
    assert state.portfolio.positions[ASSET_ID].quantity == Decimal("10")
    assert state.allocation.reservations == {}, "the parent's reservation is retired once"


def test_the_bindings_rebuild_from_the_mirror_after_a_restart() -> None:
    _, order = _working()
    first, second = _children(order)
    venue, bindings = _venue(), ChildOrderBindings()
    for child in (first, second):
        routed = _route(venue, order, child, bindings=bindings)
        venue, bindings = routed.broker_state, routed.children
    direct = route_order(venue, PaperBroker(FREE), _working()[1], 6.0, config=CONFIG)
    assert direct.decision.routed
    rebuilt = ChildOrderBindings.from_mirror(direct.broker_state)
    assert dict(rebuilt.to_parent) == dict(bindings.to_parent)
