"""v3.9 end to end: the universal execution contract, through real AlphaLab objects.

Two strategies ask for the same instrument; allocation nets them into one parent
order that the execution path leaves working for a venue. From there the whole
contract is exercised with nothing stubbed but the venue itself:

    strategy -> intent -> netted OrderRequest -> OMS parent (EXTERNAL)
      -> capability check -> TWAP children -> route decision per child
      -> child venue orders -> normalized venue events -> mirror
      -> fills settled on the parent -> positions, cash, reservations
      -> snapshot reconciliation (mirror vs venue) and book vs mirror
      -> execution analytics -> strategy attribution -> identities rebuilt

A rejected order, a cancelled algorithm, a capability mismatch, a redelivered
fill and a disconnect are driven through the same path. The venue is
:class:`ScriptedVenue` -- a deterministic stand-in at the external boundary that
accepts submissions and does nothing else on its own; everything a venue says
afterwards arrives as a :class:`~alphalab.broker.lifecycle.VenueEvent`, exactly
as an application's adapter would deliver it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from alphalab.analytics.attribution import calculate_attribution
from alphalab.broker import (
    BrokerEngine,
    ExternalOrderMap,
    PaperBroker,
    VenueSnapshot,
    reconcile_snapshot,
)
from alphalab.broker.events import BrokerEvent, OrderSubmitted
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.lifecycle import LifecycleOutcome, VenueEvent, apply_venue_event
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import SnapshotDivergenceKind
from alphalab.broker.requests import CancelRequest, RequestLedger, issue_cancel
from alphalab.broker.state import BrokerState
from alphalab.broker.validation import validate_order_submission
from alphalab.common.ids import id_scope, new_id
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
from alphalab.core.enums import AssetType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.core.lifecycle import ExecutionEventKind as E
from alphalab.core.order_request import OrderRequest
from alphalab.execution.algorithms import (
    TWAP,
    AlgorithmState,
    AlgorithmStatus,
    AlgorithmTerms,
    Iceberg,
    Urgency,
    cancel_algorithm,
    record_child_execution,
    record_child_outcome,
    release_children,
    start_algorithm,
)
from alphalab.execution.commission import FixedCommission
from alphalab.execution.costs import (
    FREE,
    ExecutionCostModel,
    NoImpact,
    NoSlippage,
    NoTax,
    ProportionalFee,
    QuotedHalfSpread,
)
from alphalab.execution.quality import (
    ClockSource,
    ExecutionBenchmarks,
    LifecycleMark,
    OrderExecution,
    OrderOutcome,
    OrderTimeline,
    ReferencePrice,
    TimelineMark,
    execution_quality_report,
    implementation_shortfall,
    measure_latency,
    rejection_rate,
    venue_quality,
)
from alphalab.execution.report import ExecutionReport
from alphalab.execution.routing import (
    CandidateStatus,
    RouteDecision,
    RouteRequest,
    RouteStatus,
    RoutingObjective,
    RoutingPolicy,
    VenueProfile,
    VenueQuote,
    select_route,
)
from alphalab.lifecycle import (
    MismatchCategory,
    ReconciliationTolerances,
    SymbolMapping,
    Tolerance,
    reconcile_execution_state,
    research_configuration_with_execution,
)
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.broker_routing import (
    ChildOrderBindings,
    RoutingConfig,
    RoutingRefusal,
    apply_broker_execution,
    child_broker_order_id,
    route_child_order,
    route_order,
)
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    sized_quote,
)

CASH = Decimal("10000000")
LIMIT = Decimal("100.10")
ASSET = "3f5d8c1e-7b64-4c2a-9e0d-5a1b2c3d4e5f"
CONFIG = RoutingConfig(venue="VEN-A", currency="USD", order_type=OrderType.LIMIT)
ACCOUNT = "ACC-1"


# --------------------------------------------------------------------------- #
# The external boundary
# --------------------------------------------------------------------------- #


class ScriptedVenue(PaperBroker):
    """A venue that takes a submission and then waits to be told what happened.

    The reference adapter acknowledges and fills on its own; a real venue does
    neither synchronously. This one records the submission as ``SUBMITTED`` and
    nothing more -- every acknowledgement, fill, rejection and cancel arrives
    afterwards as a normalized event, the way an application's adapter would
    hand them over after translating its vendor's messages.
    """

    def submit_order(
        self, state: BrokerState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_order_submission(state, order)
        submitted = replace(order, status=BrokerOrderStatus.SUBMITTED, updated_at=timestamp)
        event = OrderSubmitted(str(new_id()), timestamp, order.broker_order_id, order.oms_order_id)
        return (
            replace(
                state,
                orders=state.orders.set(order.broker_order_id, submitted),
                events=state.events.append(event),
            ),
            (event,),
        )


def _declaration(name: str, order_types: frozenset[OrderType]) -> CapabilityDeclaration:
    return CapabilityDeclaration(
        adapter_id=name,
        supported_features=frozenset({Capability.CANCEL_REPLACE, Capability.STREAMING}),
        unsupported_features=frozenset(),
        markets=(
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
        unsupported_asset_classes=frozenset({AssetType.OPTION, AssetType.FUTURE}),
        accounts=(
            AccountCapability(
                ACCOUNT, frozenset({AssetType.EQUITY}), Support.UNSUPPORTED, Support.SUPPORTED
            ),
        ),
    )


DECLARATIONS = {
    "VEN-A": _declaration("adapter-a", frozenset({OrderType.MARKET, OrderType.LIMIT})),
    "VEN-B": _declaration("adapter-b", frozenset({OrderType.MARKET, OrderType.LIMIT})),
    "VEN-C": _declaration("adapter-c", frozenset({OrderType.MARKET})),
}


def _costs(fee: str) -> ExecutionCostModel:
    return ExecutionCostModel(
        QuotedHalfSpread(),
        NoSlippage(),
        NoImpact(),
        FixedCommission(Decimal("0")),
        ProportionalFee(Decimal(fee)),
        NoTax(),
    )


PROFILES = (
    VenueProfile("VEN-A", DECLARATIONS["VEN-A"], _costs("0.0001"), Decimal("0.002")),
    VenueProfile("VEN-B", DECLARATIONS["VEN-B"], _costs("0.0001"), Decimal("0.001")),
    VenueProfile("VEN-C", DECLARATIONS["VEN-C"], _costs("0"), Decimal("0.001")),
)
POLICY = RoutingPolicy(RoutingObjective.LOWEST_ALL_IN_COST, 2.0, None, False, False, frozenset())


def _quotes(at: float) -> tuple[VenueQuote, ...]:
    """What each venue shows: A is deep, B is cheaper but thin, C cannot take a limit."""

    return (
        VenueQuote(
            "VEN-A",
            ASSET,
            Decimal("99.98"),
            Decimal("100.02"),
            Decimal("500"),
            Decimal("500"),
            "USD",
            at,
            "tape",
        ),
        VenueQuote(
            "VEN-B",
            ASSET,
            Decimal("99.99"),
            Decimal("100.01"),
            Decimal("10"),
            Decimal("10"),
            "USD",
            at,
            "tape",
        ),
        VenueQuote(
            "VEN-C",
            ASSET,
            Decimal("99.99"),
            Decimal("100.01"),
            Decimal("900"),
            Decimal("900"),
            "USD",
            at,
            "tape",
        ),
    )


def _requirements(
    quantity: Decimal, order_type: OrderType = OrderType.LIMIT
) -> CompatibilityReport:
    requirements = order_requirements(
        asset_class=AssetType.EQUITY,
        listing_venue="XNAS",
        account_id=ACCOUNT,
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        side=Side.BUY,
        quantity=quantity,
        position_before=Decimal("0"),
        extended_hours=False,
        bracket_orders=False,
        margin=False,
        features=frozenset(),
    )
    return check_compatibility(requirements, DECLARATIONS["VEN-A"])


# --------------------------------------------------------------------------- #
# The scenario
# --------------------------------------------------------------------------- #


def _pipeline() -> ExecutionPipelineState:
    runtime = create_runtime()
    plans = {
        "MOMENTUM": {2.0: Decimal("60"), 50.0: Decimal("20")},
        "MEANREV": {2.0: Decimal("40"), 60.0: Decimal("30")},
    }
    for strategy_id, plan in plans.items():
        runtime = register_strategy(
            runtime, strategy_id, ScriptedStrategy(strategy_id, ASSET, plan)
        )
    running = {}
    for strategy_id, strategy_state in runtime.strategies.items():
        configured, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"quotes"}), 1.2)
        started, _ = RuntimeSupervisor.start(subscribed, 1.3)
        running[strategy_id] = started
    config = replace(
        pipeline_config("MOMENTUM", CASH, None, permissive_risk_limits(Decimal("100000000"))),
        routing=ExecutionRouting.EXTERNAL,
    )
    return ExecutionPipeline.initialize(config, replace(runtime, strategies=running), 1.0)


@dataclass
class Scenario:
    """Everything the run produced, for the assertions below."""

    pipeline: ExecutionPipelineState
    venue: BrokerState
    children: ChildOrderBindings
    parent_request: OrderRequest
    parent: OMSOrder
    algorithm: AlgorithmState
    decisions: list[RouteDecision]
    lifecycle: list[tuple[str, LifecycleOutcome]]
    timeline: list[TimelineMark]
    midpoints: dict[str, Decimal]
    rejected_request: OrderRequest
    cancelled_request: OrderRequest
    cancelled_algorithm: AlgorithmState
    capability_refusal: RoutingRefusal | None
    disconnected_refusal: RoutingRefusal | None
    resync_required: bool
    duplicate_book_fills: int
    ledger: RequestLedger


def _deliver(
    venue: BrokerState, event: VenueEvent, log: list[tuple[str, LifecycleOutcome]]
) -> BrokerState:
    venue, decision = apply_venue_event(venue, event)
    log.append((str(event.kind), decision.outcome))
    return venue


def _fill(
    handle: str, execution_id: str, quantity: str, price: str, at: float, complete: bool
) -> tuple[VenueEvent, BrokerExecution]:
    execution = BrokerExecution(
        execution_id, handle, ASSET, Decimal(quantity), Decimal(price), Decimal("0"), at
    )
    kind = E.ORDER_FILLED if complete else E.ORDER_PARTIALLY_FILLED
    return VenueEvent(kind, at, handle, execution=execution), execution


def _run() -> Scenario:
    with id_scope(39):
        return _drive()


def _drive() -> Scenario:
    lifecycle: list[tuple[str, LifecycleOutcome]] = []
    timeline: list[TimelineMark] = []
    midpoints: dict[str, Decimal] = {}
    broker = ScriptedVenue(FREE)
    venue = BrokerEngine.initialize("VEN-A", CASH, "USD")
    venue = _deliver(venue, VenueEvent(E.BROKER_CONNECTED, 1.5), lifecycle)
    children = ChildOrderBindings()

    # 1. Two strategies, one instrument: one netted parent, left working.
    state = _pipeline()
    step = ExecutionPipeline.process_quote(
        state, sized_quote(ASSET, 2.0, Decimal("100"), Decimal("1000")), context_factory
    )
    state = step.state
    (request,) = step.order_requests
    (parent,) = step.oms_orders
    timeline.append(TimelineMark(LifecycleMark.DECISION, request.timestamp, ClockSource.LOCAL))

    # 2. The parent is worked by a TWAP in four ten-second slices.
    algorithm = start_algorithm(
        request,
        TWAP(4, Urgency.neutral()),
        AlgorithmTerms(3.0, 43.0, Decimal("1"), OrderType.LIMIT, LIMIT),
    )
    decisions: list[RouteDecision] = []
    script = {
        # slice start: (venue fills as (id, qty, price, offset, complete), then an ending)
        1: ([("VX-1", "15", "100.03", 1.0, False)], E.ORDER_EXPIRED),
        2: ([("VX-2", "35", "100.04", 1.0, True)], None),
        3: ([("VX-3", "25", "100.05", 1.0, True)], None),
        4: ([("VX-4", "10", "100.05", 1.0, False), ("VX-5", "15", "100.06", 2.0, True)], None),
    }
    duplicate_book_fills = 0
    for sequence, start in enumerate((3.0, 13.0, 23.0, 33.0), start=1):
        algorithm, (child,) = release_children(algorithm, start)
        # Capability first, then where to send it.
        report = _requirements(child.quantity)
        decision = select_route(
            RouteRequest(
                child.child_id,
                ASSET,
                Side.BUY,
                child.quantity,
                LIMIT,
                "USD",
                report.requirements,
                Decimal("1"),
                start,
            ),
            _quotes(start - 0.5),
            PROFILES,
            POLICY,
        )
        decisions.append(decision)
        assert decision.status is RouteStatus.ROUTED and decision.legs[0].venue == "VEN-A"
        routed = route_child_order(
            venue,
            broker,
            state.oms.orders.find(parent.order_id),
            child,
            start,
            mapping=ExternalOrderMap(),
            children=children,
            config=CONFIG,
            capability=report,
        )
        assert routed.decision.routed, routed.decision.reason
        venue, children = routed.broker_state, routed.children
        handle = child_broker_order_id(parent, child.sequence)
        if sequence == 1:
            timeline.append(TimelineMark(LifecycleMark.SUBMISSION, start, ClockSource.LOCAL))
            timeline.append(
                TimelineMark(LifecycleMark.ACKNOWLEDGEMENT, start + 0.25, ClockSource.VENUE)
            )
        venue = _deliver(venue, VenueEvent(E.ORDER_ACCEPTED, start + 0.25, handle), lifecycle)

        fills, ending = script[sequence]
        for execution_id, quantity, price, offset, complete in fills:
            event, execution = _fill(
                handle, execution_id, quantity, price, start + offset, complete
            )
            venue = _deliver(venue, event, lifecycle)
            state, booked, _ = apply_broker_execution(
                state, state.oms.orders.find(parent.order_id), execution, CONFIG
            )
            assert len(booked) == 1
            algorithm = record_child_execution(
                algorithm, child.child_id, execution_id, Decimal(quantity), start + offset
            )
            midpoints[execution_id] = Decimal("100.00")
            if execution_id == "VX-1":
                timeline.append(
                    TimelineMark(LifecycleMark.FIRST_FILL, start + offset, ClockSource.VENUE)
                )
            if execution_id == "VX-2":
                # Redelivered after a reconnect: a no-op at both layers.
                venue = _deliver(venue, event, lifecycle)
                state, again, _ = apply_broker_execution(
                    state, state.oms.orders.find(parent.order_id), execution, CONFIG
                )
                duplicate_book_fills += len(again)
            if execution_id == "VX-5":
                timeline.append(
                    TimelineMark(LifecycleMark.LAST_FILL, start + offset, ClockSource.VENUE)
                )
                timeline.append(
                    TimelineMark(LifecycleMark.TERMINAL, start + offset, ClockSource.VENUE)
                )
        if ending is not None:
            venue = _deliver(venue, VenueEvent(ending, start + 10.0, handle), lifecycle)
            algorithm = record_child_outcome(
                algorithm, child.child_id, OrderStatus.EXPIRED, start + 10.0
            )

    # The venue reports what it now holds and what the account is worth.
    spent = sum(
        (
            r.fill_quantity * r.fill_price
            for r in state.execution.history
            if r.order_id == request.order_id
        ),
        Decimal("0"),
    )
    venue = _deliver(
        venue,
        VenueEvent(
            E.POSITION_CHANGED,
            40.0,
            position=BrokerPosition(
                ASSET, Decimal("100"), Decimal("100"), Decimal("10000"), Decimal("0"), Decimal("0")
            ),
        ),
        lifecycle,
    )
    venue = _deliver(
        venue,
        VenueEvent(E.BALANCE_CHANGED, 40.0, account=replace(venue.account, cash=CASH - spent)),
        lifecycle,
    )

    # 3. A capability mismatch: VEN-C cannot take a limit order.
    mismatch = check_compatibility(_requirements(Decimal("5")).requirements, DECLARATIONS["VEN-C"])
    refused = route_order(venue, broker, parent, 41.0, config=CONFIG, capability=mismatch)
    capability_refusal = refused.decision.refusal

    # 4. A second parent, routed whole, which the venue rejects.
    step = ExecutionPipeline.process_quote(
        state, sized_quote(ASSET, 50.0, Decimal("100"), Decimal("1000")), context_factory
    )
    state = step.state
    (rejected_request,) = step.order_requests
    (rejected_parent,) = step.oms_orders
    direct = route_order(
        venue,
        broker,
        rejected_parent,
        50.0,
        config=CONFIG,
        capability=_requirements(rejected_parent.quantity),
    )
    assert direct.decision.routed
    venue = direct.broker_state
    venue = _deliver(
        venue,
        VenueEvent(
            E.ORDER_REJECTED,
            50.3,
            direct.order.broker_order_id if direct.order else "",
            reason="price band",
        ),
        lifecycle,
    )
    state = ExecutionPipeline.apply_terminal_outcome(
        state, rejected_parent, OrderStatus.REJECTED, 50.3, "price band"
    )

    # 5. A disconnect: nothing is sent until the link is back and reconciled.
    venue = _deliver(
        venue, VenueEvent(E.BROKER_DISCONNECTED, 55.0, reason="socket closed"), lifecycle
    )
    step = ExecutionPipeline.process_quote(
        state, sized_quote(ASSET, 60.0, Decimal("100"), Decimal("1000")), context_factory
    )
    state = step.state
    (cancelled_request,) = step.order_requests
    (third,) = step.oms_orders
    iceberg = start_algorithm(
        cancelled_request,
        Iceberg(Decimal("10")),
        AlgorithmTerms(60.0, 120.0, Decimal("1"), OrderType.LIMIT, LIMIT),
    )
    iceberg, (tranche,) = release_children(iceberg, 60.0)
    blocked = route_child_order(
        venue,
        broker,
        third,
        tranche,
        60.0,
        mapping=ExternalOrderMap(),
        children=children,
        config=CONFIG,
        capability=None,
    )
    disconnected_refusal = blocked.decision.refusal
    venue, reconnected = apply_venue_event(venue, VenueEvent(E.BROKER_CONNECTED, 61.0))
    resync_required = reconnected.resync_required

    # 6. The iceberg works one tranche, then the desk cancels it mid-way.
    ledger = RequestLedger()
    for tranche_number in (1, 2):
        routed = route_child_order(
            venue,
            broker,
            state.oms.orders.find(third.order_id),
            tranche,
            61.0 + tranche_number,
            mapping=ExternalOrderMap(),
            children=children,
            config=CONFIG,
            capability=None,
        )
        assert routed.decision.routed, routed.decision.reason
        venue, children = routed.broker_state, routed.children
        handle = child_broker_order_id(third, tranche.sequence)
        venue = _deliver(
            venue, VenueEvent(E.ORDER_ACCEPTED, 61.5 + tranche_number, handle), lifecycle
        )
        if tranche_number == 1:
            event, execution = _fill(handle, "VX-9", "10", "100.02", 62.5, True)
            venue = _deliver(venue, event, lifecycle)
            state, _, _ = apply_broker_execution(
                state, state.oms.orders.find(third.order_id), execution, CONFIG
            )
            iceberg = record_child_execution(iceberg, tranche.child_id, "VX-9", Decimal("10"), 62.5)
            iceberg, (tranche,) = release_children(iceberg, 63.0)
    iceberg, to_cancel = cancel_algorithm(iceberg, 64.0, "desk decision")
    for child in to_cancel:
        handle = child_broker_order_id(third, child.sequence)
        venue, ledger, sent = issue_cancel(venue, ledger, CancelRequest(handle, 1, 64.0))
        assert sent.send
        venue, ledger, retry = issue_cancel(venue, ledger, CancelRequest(handle, 1, 64.5))
        assert not retry.send, "a retried cancel is the same request"
        venue = _deliver(venue, VenueEvent(E.ORDER_CANCELLED, 65.0, handle), lifecycle)
        iceberg = record_child_outcome(iceberg, child.child_id, OrderStatus.CANCELLED, 65.0)
    state = ExecutionPipeline.apply_terminal_outcome(
        state, state.oms.orders.find(third.order_id), OrderStatus.CANCELLED, 65.0
    )

    # The venue's closing reports: everything it now holds, and the cash left.
    held = sum((r.fill_quantity for r in state.execution.history), Decimal("0"))
    spent = sum((r.fill_quantity * r.fill_price for r in state.execution.history), Decimal("0"))
    venue = _deliver(
        venue,
        VenueEvent(
            E.POSITION_CHANGED,
            66.0,
            position=BrokerPosition(
                ASSET, held, Decimal("100"), held * 100, Decimal("0"), Decimal("0")
            ),
        ),
        lifecycle,
    )
    venue = _deliver(
        venue,
        VenueEvent(E.BALANCE_CHANGED, 66.0, account=replace(venue.account, cash=CASH - spent)),
        lifecycle,
    )

    return Scenario(
        pipeline=state,
        venue=venue,
        children=children,
        parent_request=request,
        parent=state.oms.orders.find(parent.order_id),
        algorithm=algorithm,
        decisions=decisions,
        lifecycle=lifecycle,
        timeline=timeline,
        midpoints=midpoints,
        rejected_request=rejected_request,
        cancelled_request=cancelled_request,
        cancelled_algorithm=iceberg,
        capability_refusal=capability_refusal,
        disconnected_refusal=disconnected_refusal,
        resync_required=resync_required,
        duplicate_book_fills=duplicate_book_fills,
        ledger=ledger,
    )


@pytest.fixture(scope="module")
def run() -> Scenario:
    return _run()


def _reports(run: Scenario, order_id: str) -> tuple[ExecutionReport, ...]:
    return tuple(r for r in run.pipeline.execution.history if r.order_id == order_id)


# --------------------------------------------------------------------------- #
# The successful order
# --------------------------------------------------------------------------- #


def test_two_strategies_net_into_one_parent_that_keeps_both(run: Scenario) -> None:
    assert run.parent_request.quantity == Decimal("100")
    assert {c.strategy_id: c.quantity for c in run.parent_request.contributions} == {
        "MEANREV": Decimal("40"),
        "MOMENTUM": Decimal("60"),
    }
    assert run.parent.status is OrderStatus.FILLED
    assert run.parent.filled_quantity == Decimal("100")


def test_the_twap_caught_up_after_a_partial_expiry_and_completed(run: Scenario) -> None:
    quantities = [p.child.quantity for p in run.algorithm.children.values()]
    assert quantities == [Decimal("25"), Decimal("35"), Decimal("25"), Decimal("25")]
    assert run.algorithm.status is AlgorithmStatus.COMPLETED
    assert all(
        p.child.contributions == run.parent_request.contributions
        for p in run.algorithm.children.values()
    )


def test_every_child_was_routed_by_an_explained_decision(run: Scenario) -> None:
    for decision in run.decisions:
        status = {c.venue: c.status for c in decision.candidates}
        assert status["VEN-C"] is CandidateStatus.INELIGIBLE, "no limit orders"
        assert status["VEN-B"] is CandidateStatus.ELIGIBLE, (
            "eligible, but too thin to take it whole"
        )
        assert decision.legs[0].venue == "VEN-A"
        assert len(decision.explanation()) == 4


def test_the_venue_lifecycle_was_applied_redelivery_included(run: Scenario) -> None:
    outcomes = [outcome for _, outcome in run.lifecycle]
    assert LifecycleOutcome.DUPLICATE in outcomes, "the redelivered fill"
    assert not any(
        outcome
        in {LifecycleOutcome.CONFLICT, LifecycleOutcome.UNKNOWN_ORDER, LifecycleOutcome.INVALID}
        for outcome in outcomes
    )
    assert run.duplicate_book_fills == 0


def test_positions_cash_and_reservations_agree_with_the_fills(run: Scenario) -> None:
    fills = _reports(run, run.parent_request.order_id)
    assert sum((f.fill_quantity for f in fills), Decimal("0")) == Decimal("100")
    position = run.pipeline.portfolio.positions[ASSET]
    assert position.quantity == Decimal("110"), "100 from the TWAP and 10 from the iceberg"
    assert run.pipeline.allocation.reservations == {}, "every parent finished and freed its capital"


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


TOLERANCES = ReconciliationTolerances(
    order_quantity=Tolerance(absolute=Decimal("0")),
    order_price=Tolerance(absolute=Decimal("1")),
    fill_quantity=Tolerance(absolute=Decimal("0")),
    fill_price=Tolerance(absolute=Decimal("0")),
    commission=Tolerance(absolute=Decimal("0")),
    position_quantity=Tolerance(absolute=Decimal("0")),
    cash=Tolerance(absolute=Decimal("0.01")),
)


def _as_venue_reports_it(venue: BrokerState, at: float) -> VenueSnapshot:
    return VenueSnapshot(
        as_of=at,
        orders=tuple(venue.orders.values()),
        executions=tuple(venue.executions.values()),
        positions=tuple(p for p in venue.positions.values()),
        account=venue.account,
    )


def test_the_mirror_reconciles_against_the_venues_own_snapshot(run: Scenario) -> None:
    result = reconcile_snapshot(
        run.venue, _as_venue_reports_it(run.venue, 70.0), evaluated_at=70.0, max_age_seconds=5.0
    )
    assert result.fully_reconciled, result.divergences
    again = reconcile_snapshot(
        run.venue, _as_venue_reports_it(run.venue, 70.0), evaluated_at=70.0, max_age_seconds=5.0
    )
    assert again.reconciliation_id == result.reconciliation_id


def test_a_venue_that_disagrees_is_caught_field_by_field(run: Scenario) -> None:
    snapshot = _as_venue_reports_it(run.venue, 70.0)
    orders = list(snapshot.orders)
    orders[0] = replace(orders[0], quantity=orders[0].quantity + 1)
    stranger = replace(orders[1], broker_order_id="ALB-STRANGER", oms_order_id="OMS-STRANGER")
    tampered = replace(snapshot, orders=(*orders, stranger))
    result = reconcile_snapshot(run.venue, tampered, evaluated_at=70.0, max_age_seconds=5.0)
    kinds = {d.kind for d in result.divergences}
    assert {SnapshotDivergenceKind.ORDER_QUANTITY, SnapshotDivergenceKind.UNKNOWN_ORDER} <= kinds
    stale = reconcile_snapshot(
        run.venue, replace(snapshot, as_of=10.0), evaluated_at=70.0, max_age_seconds=100.0
    )
    assert not stale.reconciled


def test_the_book_reconciles_against_the_mirror_children_included(run: Scenario) -> None:
    result = reconcile_execution_state(
        run.pipeline,
        run.venue,
        _direct_bindings(run),
        SymbolMapping.identity(),
        TOLERANCES,
        children=run.children,
    )
    assert result.mismatches == (), result.mismatches


def _direct_bindings(run: Scenario) -> ExternalOrderMap:
    """The rejected order went to the venue whole; the others went in children."""

    handle = next(
        order.broker_order_id
        for order in run.venue.orders.values()
        if order.oms_order_id == run.rejected_request.order_id
    )
    return ExternalOrderMap().bind(run.rejected_request.order_id, handle)


def test_a_break_between_book_and_mirror_is_reported(run: Scenario) -> None:
    """A fill the venue has and the book never applied; a child on another instrument."""

    child = next(iter(run.children.to_parent))
    orphan = BrokerExecution(
        "VX-LOST", child, ASSET, Decimal("1"), Decimal("100"), Decimal("0"), 67.0
    )
    renamed = replace(run.venue.orders[child], symbol="SOMETHING-ELSE")
    tampered = replace(
        run.venue,
        executions=run.venue.executions.set("VX-LOST", orphan),
        orders=run.venue.orders.set(child, renamed),
    )
    result = reconcile_execution_state(
        run.pipeline,
        tampered,
        _direct_bindings(run),
        SymbolMapping.identity(),
        TOLERANCES,
        children=run.children,
    )
    categories = {m.category for m in result.mismatches}
    assert MismatchCategory.UNEXPECTED_OBSERVED_FILL in categories
    assert MismatchCategory.INSTRUMENT_MISMATCH in categories


# --------------------------------------------------------------------------- #
# Rejection, cancellation, capability, connectivity
# --------------------------------------------------------------------------- #


def test_the_rejected_order_is_terminal_in_the_book_and_the_mirror(run: Scenario) -> None:
    order = run.pipeline.oms.orders.find(
        next(
            o.order_id
            for o in run.pipeline.oms.orders.orders()
            if str(o.order_id.value) == run.rejected_request.order_id
        )
    )
    assert order.status is OrderStatus.REJECTED
    mirror = next(
        o for o in run.venue.orders.values() if o.oms_order_id == run.rejected_request.order_id
    )
    assert mirror.status is OrderStatus.REJECTED


def test_the_cancelled_algorithm_kept_its_fill_and_freed_the_rest(run: Scenario) -> None:
    assert run.cancelled_algorithm.status is AlgorithmStatus.CANCELLED
    assert run.cancelled_algorithm.filled == Decimal("10")
    order = next(
        o
        for o in run.pipeline.oms.orders.orders()
        if str(o.order_id.value) == run.cancelled_request.order_id
    )
    assert order.status is OrderStatus.CANCELLED and order.filled_quantity == Decimal("10")
    assert len(run.ledger.issued) == 1, "the retried cancel was recognised, not re-issued"


def test_capability_and_connectivity_gates_refused_before_anything_was_sent(run: Scenario) -> None:
    assert run.capability_refusal is RoutingRefusal.CAPABILITY_MISMATCH
    assert run.disconnected_refusal is RoutingRefusal.DISCONNECTED
    assert run.resync_required


# --------------------------------------------------------------------------- #
# Execution analytics and attribution
# --------------------------------------------------------------------------- #


def _execution(run: Scenario) -> OrderExecution:
    return OrderExecution(
        order=run.parent_request,
        fills=_reports(run, run.parent_request.order_id),
        currency="USD",
        outcome=run.parent.status,
        benchmarks=ExecutionBenchmarks(
            None, Decimal("100.02"), Decimal("100.04"), Decimal("100.10"), "scripted tape"
        ),
        limit_price=LIMIT,
        timeline=OrderTimeline(run.parent_request.order_id, "VEN-A", tuple(run.timeline)),
        fill_midpoints=run.midpoints,
        account_id=ACCOUNT,
    )


def test_implementation_shortfall_reconciles_to_the_fills(run: Scenario) -> None:
    shortfall = implementation_shortfall(_execution(run))
    fills = _reports(run, run.parent_request.order_id)
    by_hand = sum((f.fill_quantity * (f.fill_price - Decimal("100")) for f in fills), Decimal("0"))
    assert shortfall.execution_cost == by_hand
    assert shortfall.delay_cost is not None and shortfall.trading_cost is not None
    assert shortfall.delay_cost + shortfall.trading_cost == shortfall.execution_cost
    assert shortfall.opportunity_cost == 0
    assert shortfall.venues == ("VEN-A",)
    shares = dict(shortfall.strategy_shares)
    assert set(shares) == {"MEANREV", "MOMENTUM"}
    assert shortfall.total is not None
    assert sum(shares.values(), Decimal("0")) == shortfall.total
    assert shares["MOMENTUM"] == (shortfall.total * Decimal("0.6")).quantize(Decimal("0.0001"))


def test_latency_rejections_and_venue_quality_are_measured_from_the_run(run: Scenario) -> None:
    execution = _execution(run)
    assert execution.timeline is not None
    ack = measure_latency(
        execution.timeline, LifecycleMark.SUBMISSION, LifecycleMark.ACKNOWLEDGEMENT
    )
    assert ack.seconds == Decimal("0.25") and ack.mixed_clocks
    outcomes = [
        OrderOutcome(run.parent_request.order_id, "VEN-A", 3.0, OrderStatus.FILLED),
        OrderOutcome(run.rejected_request.order_id, "VEN-A", 50.0, OrderStatus.REJECTED),
        OrderOutcome(run.cancelled_request.order_id, "VEN-A", 62.0, OrderStatus.CANCELLED),
    ]
    rate = rejection_rate(outcomes, window_start=0.0, window_end=100.0, venue="VEN-A")
    assert (rate.rejected, rate.resolved) == (1, 3)
    (quality,) = venue_quality(
        [execution], outcomes, reference=ReferencePrice.ARRIVAL, window_start=0.0, window_end=100.0
    )
    assert quality.venue == "VEN-A" and quality.filled_quantity == Decimal("100")
    assert quality.acknowledgement_seconds_median == Decimal("0.25")


def test_strategy_attribution_survives_the_algorithm(run: Scenario) -> None:
    records = list(run.pipeline.trade_records)
    assert records, "every child fill produced a trade record"
    assert all({c.strategy_id for c in r.contributions} <= {"MOMENTUM", "MEANREV"} for r in records)
    twap_records = [
        r for r in records if {c.strategy_id for c in r.contributions} == {"MOMENTUM", "MEANREV"}
    ]
    assert len(twap_records) == 5, "five child fills, one parent, both strategies on each"
    metrics = calculate_attribution(records)
    assert set(metrics.pnl_by_strategy) <= {"MOMENTUM", "MEANREV"}


def test_the_quality_report_and_every_identity_reproduce_in_a_second_run(run: Scenario) -> None:
    second = _run()
    first_report = execution_quality_report(
        [_execution(run)], reporting_currency=None, converter=None, conversion_at=None
    )
    second_report = execution_quality_report(
        [_execution(second)], reporting_currency=None, converter=None, conversion_at=None
    )
    assert first_report.report_id == second_report.report_id
    assert [d.decision_id for d in run.decisions] == [d.decision_id for d in second.decisions]
    assert run.algorithm.algorithm_id == second.algorithm.algorithm_id
    assert run.algorithm.schedule is not None and second.algorithm.schedule is not None
    assert run.algorithm.schedule.schedule_id == second.algorithm.schedule.schedule_id
    assert run.lifecycle == second.lifecycle


def test_the_execution_configuration_enters_the_research_identity(run: Scenario) -> None:
    research = research_configuration_with_execution(
        {"strategy": "netted"},
        algorithms={"parent": TWAP(4, Urgency.neutral())},
        routing={"child": POLICY},
        study=None,
    )
    assert (
        research.settings["execution.parent.algorithm"]
        == TWAP(4, Urgency.neutral()).configuration_id
    )
    assert research.settings["routing.child.policy"] == POLICY.policy_id
