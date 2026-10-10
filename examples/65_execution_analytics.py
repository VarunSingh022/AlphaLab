"""
AlphaLab Examples
=================

Example 65 : Execution Analytics, End to End

Difficulty : Advanced

Estimated Time : 20 minutes

Prerequisites
-------------

✓ Example 45 (broker reconciliation)
✓ Examples 61-64 (capabilities, the lifecycle, algorithms, routing)

Topics
------

• The whole execution contract in one run: two strategies -> one netted
  parent order -> a VWAP working it in children -> a capability check before
  every child is sent -> normalized venue reports -> fills settled on the
  parent through the canonical execution path -> reconciliation -> analytics
• A child that expired part-filled and was caught up, a redelivered fill that
  changed nothing, and an order the venue rejected
• Two reconciliations: the mirror against the venue's own snapshot, and
  AlphaLab's book against the mirror, children included
• Implementation shortfall with its delay, trading, explicit and opportunity
  components, split between the strategies that asked for the order
• Slippage against three named references, fill quality, latency across two
  clocks, rejection rate and venue quality
• Money never summed across currencies: per-currency totals always, and one
  reporting-currency total only through supplied, recorded FX rates
• Every identity reproduced by a second run of the same morning

What this shows
---------------

Execution analytics are only as good as the evidence under them, so this
example builds that evidence the way a live session would: a strategy's intent
becomes an order, an algorithm works it, each child is checked against what
the venue declared it can do, and every acknowledgement, fill, expiry and
rejection arrives as a normalized report and is applied through one lifecycle.
Both sides are then reconciled -- and only then is the order measured, against
references that are named, with every figure that lacks evidence reported
unavailable rather than guessed. The venue is a scripted stand-in at the
external boundary; everything else is AlphaLab's own path.

Run

    python examples/65_execution_analytics.py
"""

from dataclasses import dataclass, replace
from decimal import Decimal

from _execution_world import (
    ACCOUNT_ID,
    ASSET_ID,
    CLOSE,
    DESK_CASH,
    EAST,
    END_MIDPOINT,
    INTERVALS,
    LISTING,
    NORTH,
    OBSERVED_VOLUME,
    OPEN,
    ScriptedVenue,
    banner,
    clock,
    connected_venue,
    context_factory,
    desk,
    desk_quote,
    interval,
    midpoint,
    number,
    refusal,
    say,
    section,
    short,
    volume_profile,
)

from alphalab.broker import (
    BrokerExecution,
    BrokerPosition,
    BrokerState,
    CapabilityDeclaration,
    CompatibilityReport,
    ExecutionEventKind,
    ExternalOrderMap,
    LifecycleOutcome,
    VenueEvent,
    VenueSnapshot,
    apply_venue_event,
    check_compatibility,
    order_requirements,
    reconcile_snapshot,
)
from alphalab.common.ids import id_scope
from alphalab.core import OrderRequest, StrategyContribution
from alphalab.core.enums import AssetType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.execution import (
    VWAP,
    AlgorithmState,
    AlgorithmTerms,
    ClockSource,
    ExecutionBenchmarks,
    ExecutionQualityReport,
    ExecutionReport,
    FillStatus,
    LifecycleMark,
    MissingVolumePolicy,
    OrderExecution,
    OrderOutcome,
    OrderTimeline,
    ReferencePrice,
    TimelineMark,
    Urgency,
    execution_quality_report,
    fill_quality,
    implementation_shortfall,
    measure_latency,
    measure_slippage,
    record_child_execution,
    record_child_outcome,
    rejection_rate,
    release_children,
    start_algorithm,
    venue_quality,
)
from alphalab.execution.costs import FREE
from alphalab.instrument.record import InstrumentRecord
from alphalab.lifecycle import (
    ReconciliationTolerances,
    SymbolMapping,
    Tolerance,
    reconcile_execution_state,
)
from alphalab.oms import Order as OMSOrder
from alphalab.portfolio.fx import FxRate, FxRates, MissingRateError
from alphalab.runtime import (
    ChildOrderBindings,
    ExecutionPipeline,
    ExecutionPipelineState,
    RoutingConfig,
    apply_broker_execution,
    child_broker_order_id,
    route_child_order,
    route_order,
)

E = ExecutionEventKind

#: The strategies decide on the 09:29 quote; MOMENTUM asks for more at 13:45.
DECISION = OPEN - 60.0
LATE = CLOSE + 900.0
PLANS = {
    "MOMENTUM": {DECISION: Decimal("6000"), LATE: Decimal("2000")},
    "MEANREV": {DECISION: Decimal("4000")},
}
DECISION_MID = Decimal("99.95")
LIMIT = Decimal("100.30")
VENUE = "VENUE-NORTH"
CONFIG = RoutingConfig(venue=VENUE, currency="USD", order_type=OrderType.LIMIT)
ACK_DELAY = 0.180

#: What the venue does with each half hour's child: fills as (quantity, or
#: ``None`` for the rest of the child; cents paid over the half hour's opening
#: midpoint; seconds after release). The 10:30 child expires part-filled.
SCRIPT: dict[int, tuple[tuple[str | None, str, float], ...]] = {
    0: (("1195", "0.01", 120.0), (None, "0.02", 600.0)),
    1: ((None, "0.01", 300.0),),
    2: (("500", "0.01", 400.0),),
    3: ((None, "0.01", 500.0),),
    4: ((None, "0.01", 300.0),),
    5: ((None, "0.01", 300.0),),
    6: ((None, "0.01", 300.0),),
    7: (("1000", "0.01", 300.0), (None, "0.02", 900.0)),
}
EXPIRES = frozenset({2})
REDELIVERED = 6

TOLERANCES = ReconciliationTolerances(
    order_quantity=Tolerance(absolute=Decimal("0")),
    order_price=Tolerance(absolute=Decimal("0.01")),
    fill_quantity=Tolerance(absolute=Decimal("0")),
    fill_price=Tolerance(absolute=Decimal("0")),
    commission=Tolerance(absolute=Decimal("0")),
    position_quantity=Tolerance(absolute=Decimal("0")),
    cash=Tolerance(absolute=Decimal("0.01")),
)


@dataclass
class Morning:
    """Everything the run produced."""

    pipeline: ExecutionPipelineState
    venue: BrokerState
    children: ChildOrderBindings
    direct: ExternalOrderMap
    parent: OrderRequest
    oms_parent: OMSOrder
    algorithm: AlgorithmState
    marks: list[TimelineMark]
    midpoints: dict[str, Decimal]
    outcomes: list[OrderOutcome]
    log: list[str]


def capability(quantity: Decimal, declared: CapabilityDeclaration) -> CompatibilityReport:
    needs = order_requirements(
        asset_class=AssetType.EQUITY,
        listing_venue=LISTING,
        account_id=ACCOUNT_ID,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        side=Side.BUY,
        quantity=quantity,
        position_before=Decimal("0"),
        extended_hours=False,
        bracket_orders=False,
        margin=False,
        features=frozenset(),
    )
    return check_compatibility(needs, declared)


def run() -> Morning:
    """The morning, start to finish. Deterministic: every identifier is minted in a seeded scope."""

    with id_scope(65):
        return _morning()


def _morning() -> Morning:
    log: list[str] = []
    marks: list[TimelineMark] = []
    midpoints: dict[str, Decimal] = {}
    outcomes: list[OrderOutcome] = []
    broker = ScriptedVenue(FREE)
    venue = connected_venue(VENUE, DESK_CASH)
    children = ChildOrderBindings()

    def deliver(event: VenueEvent) -> LifecycleOutcome:
        nonlocal venue
        venue, decision = apply_venue_event(venue, event)
        return decision.outcome

    # 1. Two strategies ask for ACME; allocation nets them into one parent.
    state = desk(PLANS)
    step = ExecutionPipeline.process_quote(
        state, desk_quote(DECISION, DECISION_MID), context_factory
    )
    state = step.state
    (parent,) = step.order_requests
    (oms_parent,) = step.oms_orders
    marks.append(TimelineMark(LifecycleMark.DECISION, DECISION, ClockSource.LOCAL))
    reserved = state.allocation.reservations.get(parent.order_id, Decimal("0"))
    log.append(
        f"{clock(DECISION)}  intents: "
        + ", ".join(f"{i.strategy_id} {number(i.target)}" for i in step.intents)
        + f" -> one parent for {number(parent.quantity)} ACME at {parent.price}, "
        f"{oms_parent.status}; {number(reserved)} USD reserved"
    )

    # 2. A VWAP on the desk's volume profile works it through the morning.
    algorithm = start_algorithm(
        parent,
        VWAP(volume_profile(), MissingVolumePolicy.REFUSE, Urgency.neutral()),
        AlgorithmTerms(OPEN, CLOSE, Decimal("1"), OrderType.LIMIT, LIMIT),
    )

    first_fill: float | None = None
    for index in range(INTERVALS):
        start, end = interval(index)
        algorithm, (child,) = release_children(algorithm, start)
        book_parent = state.oms.orders.find(oms_parent.order_id)

        # 3. The capability check comes before anything is sent.
        if index == 0:
            refused = route_child_order(
                venue,
                broker,
                book_parent,
                child,
                start,
                mapping=ExternalOrderMap(),
                children=children,
                config=CONFIG,
                capability=capability(child.quantity, EAST),
            )
            log.append(
                f"{clock(start)}  child 1 checked against adapter-east: "
                f"{refused.decision.refusal.name if refused.decision.refusal else 'sent'}; "
                f"orders at the venue: {len(refused.broker_state.orders)}"
            )
        routed = route_child_order(
            venue,
            broker,
            book_parent,
            child,
            start,
            mapping=ExternalOrderMap(),
            children=children,
            config=CONFIG,
            capability=capability(child.quantity, NORTH),
        )
        assert routed.decision.routed, routed.decision.reason
        venue, children = routed.broker_state, routed.children
        handle = child_broker_order_id(book_parent, child.sequence)
        if index == 0:
            marks.append(TimelineMark(LifecycleMark.SUBMISSION, start, ClockSource.LOCAL))
            marks.append(
                TimelineMark(LifecycleMark.ACKNOWLEDGEMENT, start + ACK_DELAY, ClockSource.VENUE)
            )

        # 4. What the venue reports, normalized, applied to the mirror and the book.
        deliver(VenueEvent(E.ORDER_ACCEPTED, start + ACK_DELAY, handle))
        reported: list[str] = []
        filled = Decimal("0")
        for sequence, (quantity, cents, after) in enumerate(SCRIPT[index], start=1):
            size = child.quantity - filled if quantity is None else Decimal(quantity)
            filled += size
            price = midpoint(index) + Decimal(cents)
            execution = BrokerExecution(
                f"VX-{index + 1}.{sequence}",
                handle,
                ASSET_ID,
                size,
                price,
                Decimal("0"),
                start + after,
            )
            kind = E.ORDER_FILLED if filled == child.quantity else E.ORDER_PARTIALLY_FILLED
            event = VenueEvent(kind, start + after, handle, execution=execution)
            outcome = deliver(event)
            state, _, _ = apply_broker_execution(
                state, state.oms.orders.find(oms_parent.order_id), execution, CONFIG
            )
            algorithm = record_child_execution(
                algorithm, child.child_id, execution.execution_id, size, start + after
            )
            midpoints[execution.execution_id] = midpoint(index)
            reported.append(f"{number(size)} @ {price} ({outcome})")
            if index == REDELIVERED:
                again = deliver(event)
                state, rebooked, _ = apply_broker_execution(
                    state, state.oms.orders.find(oms_parent.order_id), execution, CONFIG
                )
                reported.append(f"redelivered ({again}), {len(rebooked)} fills booked")
            if first_fill is None:
                first_fill = start + after
        status = OrderStatus.FILLED
        if index in EXPIRES:
            reported.append(f"expired ({deliver(VenueEvent(E.ORDER_EXPIRED, end, handle))})")
            algorithm = record_child_outcome(algorithm, child.child_id, OrderStatus.EXPIRED, end)
            status = OrderStatus.EXPIRED
        outcomes.append(OrderOutcome(handle, VENUE, start, status))
        log.append(
            f"{clock(start)}  child {child.sequence}: {number(child.quantity)}; "
            + "; ".join(reported)
        )

    # The venue's own account of the position and the cash.
    fills = [r for r in state.execution.history if r.order_id == parent.order_id]
    assert first_fill is not None
    last_fill = max(r.timestamp for r in fills)
    marks.append(TimelineMark(LifecycleMark.FIRST_FILL, first_fill, ClockSource.VENUE))
    marks.append(TimelineMark(LifecycleMark.LAST_FILL, last_fill, ClockSource.VENUE))
    marks.append(TimelineMark(LifecycleMark.TERMINAL, last_fill, ClockSource.VENUE))
    held = sum((r.fill_quantity for r in fills), Decimal("0"))
    spent = sum((r.fill_quantity * r.fill_price for r in fills), Decimal("0"))
    deliver(
        VenueEvent(
            E.POSITION_CHANGED,
            CLOSE + 60.0,
            position=BrokerPosition(
                ASSET_ID,
                held,
                spent / held,
                held * END_MIDPOINT,
                held * END_MIDPOINT - spent,
                Decimal("0"),
            ),
        )
    )
    deliver(
        VenueEvent(
            E.BALANCE_CHANGED,
            CLOSE + 60.0,
            account=replace(
                venue.account, cash=DESK_CASH - spent, available_funds=DESK_CASH - spent
            ),
        )
    )

    # 5. MOMENTUM asks for more; the order goes whole to the venue, which rejects it.
    step = ExecutionPipeline.process_quote(state, desk_quote(LATE, END_MIDPOINT), context_factory)
    state = step.state
    (second,) = step.order_requests
    (oms_second,) = step.oms_orders
    direct = route_order(
        venue,
        broker,
        oms_second,
        LATE + 1.0,
        ExternalOrderMap(),
        config=CONFIG,
        capability=capability(oms_second.quantity, NORTH),
    )
    assert direct.decision.routed and direct.order is not None
    venue = direct.broker_state
    rejected = deliver(
        VenueEvent(E.ORDER_REJECTED, LATE + 1.3, direct.order.broker_order_id, reason="price band")
    )
    state = ExecutionPipeline.apply_terminal_outcome(
        state, oms_second, OrderStatus.REJECTED, LATE + 1.3, "price band"
    )
    outcomes.append(
        OrderOutcome(direct.order.broker_order_id, VENUE, LATE + 1.0, OrderStatus.REJECTED)
    )
    log.append(
        f"{clock(LATE)}  MOMENTUM asks for {number(second.quantity)} more: sent whole to "
        f"{VENUE}, which rejects it (price band) -- the rejection {rejected} to the mirror, "
        "the order ended in the book and its reservation freed"
    )

    return Morning(
        pipeline=state,
        venue=venue,
        children=children,
        direct=direct.mapping,
        parent=parent,
        oms_parent=state.oms.orders.find(oms_parent.order_id),
        algorithm=algorithm,
        marks=marks,
        midpoints=midpoints,
        outcomes=outcomes,
        log=log,
    )


def the_run(morning: Morning) -> None:
    section("Strategy -> order -> algorithm -> capability -> venue reports")
    for line in morning.log:
        say(line, indent="  ")
    parent = morning.oms_parent
    position = morning.pipeline.portfolio.positions[ASSET_ID]
    print(
        f"  the parent: {parent.status}, {number(parent.filled_quantity)} filled at "
        f"{parent.average_fill_price:.4f}; the VWAP run {morning.algorithm.status}"
    )
    print(
        f"  the book holds {number(position.quantity)} ACME; capital still reserved: "
        f"{dict(morning.pipeline.allocation.reservations.items()) or 'none'}"
    )
    say(
        "The 10:30 child filled 500 of 976 and expired; the 11:00 release topped up to the "
        "trajectory. The fill redelivered at 12:30 was a DUPLICATE at the mirror and booked "
        "nothing in the book. The capability check refused adapter-east before anything was "
        "sent: it takes no limit orders."
    )


def reconciliation(morning: Morning) -> None:
    section("Two reconciliations: mirror vs venue, book vs mirror")
    at = LATE + 10.0
    snapshot = VenueSnapshot(
        as_of=at,
        orders=tuple(morning.venue.orders.values()),
        executions=tuple(morning.venue.executions.values()),
        positions=tuple(morning.venue.positions.values()),
        account=morning.venue.account,
    )
    mirror = reconcile_snapshot(
        morning.venue, snapshot, evaluated_at=at + 1.0, max_age_seconds=30.0
    )
    say(
        f"mirror vs the venue's snapshot: {mirror.freshness}; {mirror.compared_orders} orders, "
        f"{mirror.compared_executions} fills and {mirror.compared_positions} position compared; "
        f"fully reconciled: {mirror.fully_reconciled}"
    )
    book = reconcile_execution_state(
        morning.pipeline,
        morning.venue,
        morning.direct,
        SymbolMapping.identity(),
        TOLERANCES,
        children=morning.children,
    )
    say(
        f"book vs mirror, children joined to their parent: {book.compared_orders} orders, "
        f"{book.compared_fills} fills and {book.compared_positions} position compared; "
        f"mismatches: {len(book.mismatches)}; fully reconciled: {book.fully_reconciled}"
    )
    tampered = replace(
        morning.venue,
        executions=morning.venue.executions.set(
            "VX-LOST",
            BrokerExecution(
                "VX-LOST",
                next(iter(morning.children.to_parent)),
                ASSET_ID,
                Decimal("100"),
                Decimal("100.02"),
                Decimal("0"),
                CLOSE,
            ),
        ),
    )
    broken = reconcile_execution_state(
        morning.pipeline,
        tampered,
        morning.direct,
        SymbolMapping.identity(),
        TOLERANCES,
        children=morning.children,
    )
    print("  the same, had the venue held a fill the book never saw:")
    for mismatch in broken.mismatches:
        say(f"{mismatch.category.name} {mismatch.key}: {mismatch.reason}", indent="    ")


def tape_vwap() -> Decimal:
    """The tape's VWAP over the morning, from each half hour's opening midpoint and print."""

    weighted = total = Decimal("0")
    for index, printed in enumerate(OBSERVED_VOLUME):
        if printed is None:
            continue
        weighted += midpoint(index) * Decimal(printed)
        total += Decimal(printed)
    return (weighted / total).quantize(Decimal("0.0001"))


def measured(morning: Morning) -> OrderExecution:
    fills = tuple(
        r for r in morning.pipeline.execution.history if r.order_id == morning.parent.order_id
    )
    return OrderExecution(
        order=morning.parent,
        fills=fills,
        currency="USD",
        outcome=OrderStatus.FILLED,
        benchmarks=ExecutionBenchmarks(
            decision_price=None,
            arrival_price=midpoint(0),
            interval_vwap=tape_vwap(),
            end_price=END_MIDPOINT,
            source="example tape: half-hour midpoints and prints; 11:30 print missing, excluded",
        ),
        limit_price=LIMIT,
        timeline=OrderTimeline(morning.parent.order_id, VENUE, tuple(morning.marks)),
        fill_midpoints=morning.midpoints,
        account_id=ACCOUNT_ID,
    )


def analytics(morning: Morning, execution: OrderExecution) -> None:
    section("Implementation shortfall against the decision price")
    shortfall = implementation_shortfall(execution)
    print(
        f"  {number(shortfall.filled_quantity)} of {number(shortfall.ordered_quantity)} filled "
        f"at {shortfall.average_fill_price:.4f}; decision {shortfall.decision_price}, "
        f"arrival {shortfall.arrival_price}"
    )
    for label, value in (
        ("delay (decision -> arrival)", shortfall.delay_cost),
        ("trading (arrival -> fills)", shortfall.trading_cost),
        ("execution = delay + trading", shortfall.execution_cost),
        ("explicit costs", shortfall.explicit_costs),
        ("opportunity (unfilled)", shortfall.opportunity_cost),
        ("total", shortfall.total),
    ):
        print(f"    {label:<30}{number(value, 2):>12} USD")
    print(f"    {'total, in basis points':<30}{number(shortfall.total_bps, 2):>12}")
    shares = ", ".join(f"{name} {number(share, 2)}" for name, share in shortfall.strategy_shares)
    say(f"split by what each strategy asked for: {shares} USD", indent="    ")
    for assumption in shortfall.assumptions:
        say(f"assumed: {assumption}", indent="    ")

    section("Slippage, fill quality and latency")
    for reference in (
        ReferencePrice.DECISION,
        ReferencePrice.ARRIVAL,
        ReferencePrice.INTERVAL_VWAP,
    ):
        slip = measure_slippage(execution, reference)
        print(
            f"  against {reference!s:<14} {slip.reference_price}: "
            f"{number(slip.per_unit, 4)} a share, {number(slip.amount, 2)} USD, "
            f"{number(slip.bps, 2)} bps"
        )
    quality = fill_quality(execution)
    say(
        f"filled {number(quality.fill_ratio * 100, 1)}% in {quality.fill_count} fills; "
        f"{number(quality.limit_improvement_per_unit, 4)} a share inside the limit; "
        f"effective spread {number(quality.effective_spread_bps, 2)} bps against the midpoint "
        "at each fill"
    )
    timeline = execution.timeline
    assert timeline is not None
    for start, end in (
        (LifecycleMark.DECISION, LifecycleMark.SUBMISSION),
        (LifecycleMark.SUBMISSION, LifecycleMark.ACKNOWLEDGEMENT),
        (LifecycleMark.SUBMISSION, LifecycleMark.LAST_FILL),
    ):
        latency = measure_latency(timeline, start, end)
        clocks = " (across two clocks)" if latency.mixed_clocks else ""
        print(f"  {start} -> {end}: {number(latency.seconds)} s{clocks}")

    section("Rejections and venue quality, from what the venue did")
    rate = rejection_rate(
        morning.outcomes, window_start=OPEN - 3600.0, window_end=LATE + 3600.0, venue=VENUE
    )
    print(
        f"  {VENUE}: {rate.submitted} submitted, {rate.resolved} answered, {rate.rejected} "
        f"rejected -> rejection rate {number(rate.rate, 4)}"
    )
    (venue,) = venue_quality(
        [execution],
        morning.outcomes,
        reference=ReferencePrice.ARRIVAL,
        window_start=OPEN - 3600.0,
        window_end=LATE + 3600.0,
    )
    say(
        f"{venue.venue}: {venue.fills} fills, {number(venue.filled_quantity)} filled, slippage "
        f"{number(venue.slippage_bps, 2)} bps against {venue.slippage_reference}, median "
        f"acknowledgement {number(venue.acknowledgement_seconds_median)} s"
    )
    say(
        "This measures what the venue did. Routing's expectation of the same venue (example 64) "
        "is a prediction; comparing the two is the caller's question, and neither is "
        "substituted for the other."
    )


def euro_order() -> OrderExecution:
    """A second order the desk worked on a euro listing, its fills written out here.

    The dollar order above shows the path; this one only adds a second currency.
    """

    listing = InstrumentRecord("ACME", AssetType.EQUITY, "XETR", "EUR")
    order = OrderRequest(
        order_id="DESK-EUR-1",
        strategy_id="",
        asset_id=listing.asset_id,
        side=Side.SELL,
        quantity=Decimal("3000"),
        price=Decimal("92.40"),
        timestamp=OPEN - 60.0,
        contributions=(StrategyContribution("MEANREV", Decimal("-3000")),),
    )
    fills = tuple(
        ExecutionReport(
            execution_id=f"FX-{n}",
            order_id=order.order_id,
            asset_id=listing.asset_id,
            strategy_id="",
            timestamp=OPEN + n * 600.0,
            fill_price=Decimal(price),
            fill_quantity=Decimal(quantity),
            commission=Decimal("4.50"),
            slippage=Decimal("0"),
            liquidity_flag="",
            venue="VENUE-FRANKFURT",
            currency="EUR",
            status=FillStatus.PARTIAL_FILL if n < 3 else FillStatus.FULL_FILL,
        )
        for n, (quantity, price) in enumerate(
            (("1000", "92.36"), ("1000", "92.33"), ("1000", "92.31")), start=1
        )
    )
    return OrderExecution(
        order=order,
        fills=fills,
        currency="EUR",
        outcome=OrderStatus.FILLED,
        benchmarks=ExecutionBenchmarks(
            None, Decimal("92.38"), None, Decimal("92.30"), "example tape"
        ),
        limit_price=None,
        timeline=None,
        fill_midpoints=None,
        account_id="DESK-ACCOUNT-EU",
    )


def currencies(execution: OrderExecution) -> ExecutionQualityReport:
    section("Two currencies, converted only at a stated rate")
    rates = FxRates.of([FxRate("EUR", "USD", Decimal("1.0850"), LATE, "example desk rate sheet")])
    report = execution_quality_report(
        [execution, euro_order()],
        reporting_currency="USD",
        converter=rates,
        conversion_at=LATE + 60.0,
    )
    for currency, total in report.total_by_currency.items():
        by_strategy = ", ".join(
            f"{name} {number(amount, 2)}"
            for name, amount in report.strategy_totals[currency].items()
        )
        print(f"  {currency}: shortfall {number(total, 2)}  ({by_strategy})")
    for conversion in report.conversions:
        rate = conversion.rate
        if rate.base == report.reporting_currency:
            say(f"{number(conversion.amount, 2)} {rate.base} is already in {rate.base}")
            continue
        say(
            f"converted {number(conversion.amount, 4)} {rate.base} at {rate.rate} "
            f"({rate.source}) = {number(conversion.converted, 2)} {rate.quote}"
        )
    print(f"  one total, in USD: {number(report.reporting_total, 2)}")
    try:
        execution_quality_report(
            [execution, euro_order()],
            reporting_currency="GBP",
            converter=rates,
            conversion_at=LATE + 60.0,
        )
    except MissingRateError as error:
        refusal("the same total in sterling, with no sterling rate held", error)
    return report


def main() -> None:
    banner(65, "Execution Analytics, End to End")
    morning = run()
    the_run(morning)
    reconciliation(morning)
    execution = measured(morning)
    analytics(morning, execution)
    report = currencies(execution)

    section("The same morning, run again")
    again = run()
    second = execution_quality_report(
        [measured(again), euro_order()],
        reporting_currency="USD",
        converter=FxRates.of(
            [FxRate("EUR", "USD", Decimal("1.0850"), LATE, "example desk rate sheet")]
        ),
        conversion_at=LATE + 60.0,
    )
    for label, first_id, second_id in (
        ("parent order", morning.parent.order_id, again.parent.order_id),
        ("VWAP run", morning.algorithm.algorithm_id, again.algorithm.algorithm_id),
        ("quality report", report.report_id, second.report_id),
    ):
        print(f"  {label:<16}{short(first_id)}  {short(second_id)}  same: {first_id == second_id}")
    print()


if __name__ == "__main__":
    main()
