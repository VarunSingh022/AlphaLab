"""High-performance benchmark suite for the v3.9 universal execution contract.

Seven computational paths, each on a workload an execution desk produces:

* **Capability checks** -- orders checked against a declaration with many
  accounts, and flat capability questions answered for a scope.
* **Order-state transitions** -- every status event classified against every
  status, and a venue's event stream applied to a mirror of many orders.
* **Scheduling** -- TWAP and VWAP schedules planned at two sizes, straight and
  front-loaded; the trajectory is evaluated in 34-digit decimal arithmetic.
* **Working algorithms** -- TWAP, VWAP, participation, slicing and an
  iceberg-like tranche released and filled child by child.
* **Route selection** -- one order judged against many venues' evidence, with
  and without a split.
* **Reconciliation** -- a venue snapshot of many orders and fills against the
  mirror, and cancel requests issued through the request ledger.
* **Execution analytics** -- implementation shortfall over many fills, venue
  quality over many outcomes, and a multi-currency quality report.

Each path is measured at two sizes, so the printed ops/sec is readable *and*
the scaling is visible. ``tests/regression/test_v39_complexity.py`` asserts the
near-linear paths' growth ratios; this prints the absolute numbers. Every input
is built before its timer starts, from integer recurrences: no clock beyond the
timer, no random number, no network and no vendor.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from functools import partial

from alphalab.broker import (
    ANY_LISTING_VENUE,
    AccountCapability,
    BrokerAccount,
    BrokerExecution,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerState,
    CancelRequest,
    Capability,
    CapabilityDeclaration,
    ConnectionStatus,
    ExecutionEventKind,
    ExecutionRequirements,
    MarketCapability,
    RequestLedger,
    Support,
    VenueEvent,
    VenueSnapshot,
    apply_venue_events,
    check_compatibility,
    issue_cancel,
    order_requirements,
    reconcile_snapshot,
    supports,
)
from alphalab.common.persistent_map import PersistentMap
from alphalab.core import (
    STATUS_EVENT_KINDS,
    OrderRequest,
    StrategyContribution,
    classify_order_event,
)
from alphalab.core.enums import AssetType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.execution import (
    TWAP,
    VWAP,
    AlgorithmState,
    AlgorithmTerms,
    ExecutionAlgorithm,
    ExecutionBenchmarks,
    ExecutionCostModel,
    ExecutionReport,
    FillStatus,
    FixedCommission,
    Iceberg,
    IncompletePolicy,
    IntervalVolume,
    MissingVolumePolicy,
    NoImpact,
    NoSlippage,
    NoTax,
    OrderExecution,
    OrderOutcome,
    Participation,
    ProportionalFee,
    QuotedHalfSpread,
    ReferencePrice,
    RouteRequest,
    RoutingObjective,
    RoutingPolicy,
    Slicing,
    Urgency,
    VenueProfile,
    VenueQuote,
    VolumeProfile,
    execution_quality_report,
    implementation_shortfall,
    plan_schedule,
    record_child_execution,
    release_children,
    select_route,
    start_algorithm,
    venue_quality,
)
from alphalab.portfolio import FxRate, FxRates

YES, NO = Support.SUPPORTED, Support.UNSUPPORTED
E = ExecutionEventKind
ACCOUNT = BrokerAccount(
    "BENCH-ACC", Decimal("1e9"), Decimal("1e9"), Decimal("1e9"), Decimal("0"), Decimal("1e9"), "USD"
)


def _timed(label: str, count: int, work: Callable[[], object]) -> object:
    start = time.perf_counter()
    result = work()
    duration = time.perf_counter() - start
    print(f"  {label:<64} {duration:.4f}s, {count / max(duration, 1e-9):>12,.0f} ops/sec")
    return result


def _market(asset_class: AssetType) -> MarketCapability:
    return MarketCapability(
        asset_class,
        ANY_LISTING_VENUE,
        frozenset({OrderType.MARKET, OrderType.LIMIT}),
        frozenset({TimeInForce.DAY, TimeInForce.IOC}),
        YES,
        YES,
        NO,
        NO,
    )


def _declaration(accounts: int) -> CapabilityDeclaration:
    return CapabilityDeclaration(
        adapter_id="benchmark-adapter",
        supported_features=frozenset({Capability.STREAMING, Capability.CANCEL_REPLACE}),
        unsupported_features=frozenset(),
        markets=(_market(AssetType.EQUITY), _market(AssetType.FUTURE)),
        unsupported_asset_classes=frozenset({AssetType.OPTION}),
        accounts=tuple(
            AccountCapability(
                f"ACC-{index:07d}",
                frozenset({AssetType.EQUITY}),
                YES if index % 3 else NO,
                YES,
            )
            for index in range(accounts)
        ),
    )


# --------------------------------------------------------------------------- #
# 1. Capability checks
# --------------------------------------------------------------------------- #


def _check_all(declaration: CapabilityDeclaration, orders: list[ExecutionRequirements]) -> None:
    for needs in orders:
        check_compatibility(needs, declaration)


def _ask(declaration: CapabilityDeclaration, count: int) -> None:
    for index in range(count):
        supports(
            declaration,
            Capability.SHORTING,
            asset_class=AssetType.EQUITY,
            listing_venue="XNAS",
            account_id=f"ACC-{index:07d}",
        )


def benchmark_capabilities() -> None:
    print("\n[1] Capability checks: orders against a declaration with many accounts (checks/sec)")
    for accounts in (1_000, 4_000):
        declaration = _declaration(accounts)
        orders = [
            order_requirements(
                asset_class=AssetType.EQUITY,
                listing_venue="XNAS",
                account_id=f"ACC-{index % accounts:07d}",
                order_type=OrderType.LIMIT if index % 2 else OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                side=Side.SELL if index % 4 == 0 else Side.BUY,
                quantity=Decimal(100 + index % 50),
                position_before=Decimal("0"),
                extended_hours=False,
                bracket_orders=False,
                margin=index % 5 == 0,
                features=frozenset(),
            )
            for index in range(accounts)
        ]
        _timed(
            f"check_compatibility ({accounts:,} orders, {accounts:,} accounts declared)",
            accounts,
            partial(_check_all, declaration, orders),
        )
        _timed(
            f"supports: shorting, at market and account ({accounts:,} questions)",
            accounts,
            partial(_ask, declaration, accounts),
        )


# --------------------------------------------------------------------------- #
# 2. Order-state transitions
# --------------------------------------------------------------------------- #


def _classify_everything(rounds: int) -> None:
    pairs = [(status, event) for status in OrderStatus for event in sorted(STATUS_EVENT_KINDS)]
    for round_number in range(rounds):
        filled = Decimal(round_number % 2)
        for status, event in pairs:
            classify_order_event(status, event, filled)


def _order(index: int) -> BrokerOrder:
    return BrokerOrder(
        f"B-{index:07d}",
        f"O-{index:07d}",
        "X",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("10"),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        BrokerOrderStatus.SUBMITTED,
        1.0,
        1.0,
    )


def _mirror(orders: list[BrokerOrder]) -> BrokerState:
    return BrokerState(
        "BENCH",
        ConnectionStatus.CONNECTED,
        ACCOUNT,
        orders=PersistentMap({order.broker_order_id: order for order in orders}),
    )


def _event_stream(orders: list[BrokerOrder]) -> list[VenueEvent]:
    events: list[VenueEvent] = []
    for order in orders:
        handle = order.broker_order_id
        events.append(VenueEvent(E.ORDER_ACCEPTED, 2.0, handle))
        for part in (1, 2):
            execution = BrokerExecution(
                f"X-{handle}-{part}",
                handle,
                "X",
                Decimal("5"),
                Decimal("100"),
                Decimal("0"),
                2.0 + part,
            )
            kind = E.ORDER_FILLED if part == 2 else E.ORDER_PARTIALLY_FILLED
            events.append(VenueEvent(kind, 2.0 + part, handle, execution=execution))
        events.append(VenueEvent(E.ORDER_ACCEPTED, 2.5, handle))  # late: stale
    return events


def benchmark_transitions() -> None:
    print("\n[2] Order-state transitions: the table and a venue's event stream (events/sec)")
    pairs = len(OrderStatus) * len(STATUS_EVENT_KINDS)
    for rounds in (1_000, 4_000):
        _timed(
            f"classify_order_event: every status x event ({rounds:,} rounds)",
            rounds * pairs,
            partial(_classify_everything, rounds),
        )
    for count in (1_000, 4_000):
        orders = [_order(index) for index in range(count)]
        events = _event_stream(orders)
        _timed(
            f"apply_venue_events: ack, two fills, a late ack ({count:,} orders)",
            len(events),
            partial(apply_venue_events, _mirror(orders), events),
        )


# --------------------------------------------------------------------------- #
# 3. Scheduling
# --------------------------------------------------------------------------- #


def _profile(slices: int) -> VolumeProfile:
    return VolumeProfile(
        tuple(
            IntervalVolume(float(index), float(index + 1), Decimal(1_000 + (index * 7919) % 5_000))
            for index in range(slices)
        ),
        "benchmark profile",
    )


def benchmark_scheduling() -> None:
    print("\n[3] Scheduling: TWAP and VWAP trajectories in 34-digit decimals (slices/sec)")
    for slices in (100, 400):
        terms = AlgorithmTerms(0.0, float(slices), Decimal("1"), OrderType.MARKET, None)
        quantity = Decimal(slices * 1_000)
        for label, algorithm in (
            ("TWAP, urgency 0", TWAP(slices, Urgency.neutral())),
            ("TWAP, urgency 3", TWAP(slices, Urgency(Decimal("3")))),
            (
                "VWAP, urgency 0",
                VWAP(_profile(slices), MissingVolumePolicy.REFUSE, Urgency.neutral()),
            ),
            (
                "VWAP, urgency 3",
                VWAP(_profile(slices), MissingVolumePolicy.REFUSE, Urgency(Decimal("3"))),
            ),
        ):
            _timed(
                f"plan_schedule: {label} ({slices:,} slices)",
                slices,
                partial(plan_schedule, quantity, algorithm, terms),
            )


# --------------------------------------------------------------------------- #
# 4. Working algorithms
# --------------------------------------------------------------------------- #


def _parent(quantity: int) -> OrderRequest:
    return OrderRequest(
        "BENCH-PARENT",
        "",
        "X",
        Side.BUY,
        Decimal(quantity),
        Decimal("100"),
        0.0,
        (StrategyContribution("A", Decimal(quantity)),),
    )


def _work(
    state: AlgorithmState, instants: list[float], volumes: list[IntervalVolume] | None
) -> AlgorithmState:
    """Release at each instant and fill every child in full."""

    for position, now in enumerate(instants):
        observed = () if volumes is None else (volumes[position],)
        state, released = release_children(state, now, observed)
        for child in released:
            state = record_child_execution(
                state, child.child_id, f"x{position}", child.quantity, now
            )
    return state


def benchmark_algorithms() -> None:
    print("\n[4] Working algorithms: release, fill, top up (children/sec)")
    for slices in (250, 1_000):
        window = float(slices)
        instants = [float(index) for index in range(slices)]
        terms = AlgorithmTerms(0.0, window, Decimal("1"), OrderType.MARKET, None)
        runs: tuple[tuple[str, ExecutionAlgorithm, list[IntervalVolume] | None, int], ...] = (
            ("TWAP", TWAP(slices, Urgency.neutral()), None, slices * 100),
            (
                "VWAP",
                VWAP(_profile(slices), MissingVolumePolicy.REFUSE, Urgency.neutral()),
                None,
                slices * 100,
            ),
            (
                "participation 10%",
                Participation(Decimal("0.1"), None, None, IncompletePolicy.COMPLETE_AT_END),
                [IntervalVolume(float(i), float(i + 1), Decimal(1_000)) for i in range(slices)],
                slices * 100,
            ),
            ("slicing by count", Slicing(None, slices), None, slices * 100),
            ("iceberg, one tranche at a time", Iceberg(Decimal("100")), None, slices * 100),
        )
        for label, algorithm, volumes, quantity in runs:
            state = start_algorithm(_parent(quantity), algorithm, terms)
            _timed(
                f"{label} ({slices:,} releases)",
                slices,
                partial(_work, state, instants, volumes),
            )


# --------------------------------------------------------------------------- #
# 5. Route selection
# --------------------------------------------------------------------------- #


def _costs(fee: int) -> ExecutionCostModel:
    return ExecutionCostModel(
        QuotedHalfSpread(),
        NoSlippage(),
        NoImpact(),
        FixedCommission(Decimal("0")),
        ProportionalFee(Decimal(fee) / Decimal("100000")),
        NoTax(),
    )


def benchmark_routing() -> None:
    print("\n[5] Route selection: one order against many venues' evidence (venues judged/sec)")
    declaration = _declaration(1)
    requirements = order_requirements(
        asset_class=AssetType.EQUITY,
        listing_venue="XNAS",
        account_id="ACC-0000000",
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        side=Side.BUY,
        quantity=Decimal("5000"),
        position_before=Decimal("0"),
        extended_hours=False,
        bracket_orders=False,
        margin=False,
        features=frozenset(),
    )
    for venues in (100, 400):
        quotes = [
            VenueQuote(
                f"V{index:05d}",
                "X",
                Decimal("99.99") - Decimal(index % 5) / 100,
                Decimal("100.01") + Decimal(index % 7) / 100,
                Decimal(50 + index % 90),
                Decimal(50 + index % 90),
                "USD",
                9.5,
                "benchmark tape",
            )
            for index in range(venues)
        ]
        profiles = [
            VenueProfile(f"V{index:05d}", declaration, _costs(index % 4), Decimal(index % 9) / 1000)
            for index in range(venues)
        ]
        request = RouteRequest(
            "BENCH-ORDER",
            "X",
            Side.BUY,
            Decimal("5000"),
            Decimal("100.10"),
            "USD",
            requirements,
            Decimal("1"),
            10.0,
        )
        for label, split in (("one venue", False), ("split", True)):
            policy = RoutingPolicy(
                RoutingObjective.LOWEST_ALL_IN_COST, 2.0, None, split, True, frozenset()
            )
            _timed(
                f"select_route: lowest all-in cost, {label} ({venues:,} venues)",
                venues,
                partial(select_route, request, quotes, profiles, policy),
            )


# --------------------------------------------------------------------------- #
# 6. Reconciliation and requests
# --------------------------------------------------------------------------- #


def _cancel_all(state: BrokerState, handles: list[str]) -> None:
    ledger = RequestLedger()
    for handle in handles:
        state, ledger, _ = issue_cancel(state, ledger, CancelRequest(handle, 1, 5.0))
        state, ledger, _ = issue_cancel(state, ledger, CancelRequest(handle, 1, 5.5))


def benchmark_reconciliation() -> None:
    print(
        "\n[6] Reconciliation: a venue snapshot against the mirror, and cancel requests (items/sec)"
    )
    for count in (2_000, 8_000):
        orders = [
            replace(_order(index), filled_quantity=Decimal("5"), average_fill_price=Decimal("100"))
            for index in range(count)
        ]
        executions = [
            BrokerExecution(
                f"X-{index:07d}",
                f"B-{index:07d}",
                "X",
                Decimal("5"),
                Decimal("100"),
                Decimal("0"),
                1.0,
            )
            for index in range(count)
        ]
        state = replace(
            _mirror(orders),
            executions=PersistentMap({e.execution_id: e for e in executions}),
        )
        snapshot = VenueSnapshot(5.0, tuple(orders), tuple(executions), (), ACCOUNT)
        _timed(
            f"reconcile_snapshot ({count:,} orders, {count:,} fills)",
            2 * count,
            partial(reconcile_snapshot, state, snapshot, evaluated_at=5.0, max_age_seconds=10.0),
        )
        _timed(
            f"issue_cancel: new then retried ({count:,} orders)",
            2 * count,
            partial(_cancel_all, state, [order.broker_order_id for order in orders]),
        )


# --------------------------------------------------------------------------- #
# 7. Execution analytics
# --------------------------------------------------------------------------- #


def _execution(order_id: str, fills: int, currency: str) -> OrderExecution:
    quantity = fills * 10
    order = OrderRequest(
        order_id,
        "",
        "X",
        Side.BUY,
        Decimal(quantity),
        Decimal("100"),
        0.0,
        (
            StrategyContribution("A", Decimal(quantity * 3 // 5)),
            StrategyContribution("B", Decimal(quantity * 2 // 5)),
        ),
    )
    reports = tuple(
        ExecutionReport(
            f"{order_id}-{index}",
            order_id,
            "X",
            "",
            float(index),
            Decimal("100") + Decimal(index % 13) / 100,
            Decimal("10"),
            Decimal("0.05"),
            Decimal("0"),
            "",
            f"V{index % 4}",
            currency,
            FillStatus.PARTIAL_FILL,
        )
        for index in range(fills)
    )
    return OrderExecution(
        order,
        reports,
        currency,
        OrderStatus.FILLED,
        ExecutionBenchmarks(
            None, Decimal("100.01"), Decimal("100.05"), Decimal("100.10"), "benchmark"
        ),
        None,
        None,
        None,
        None,
    )


def benchmark_analytics() -> None:
    print("\n[7] Execution analytics: shortfall, venue quality, a two-currency report (items/sec)")
    for fills in (2_000, 8_000):
        execution = _execution("BENCH-IS", fills, "USD")
        _timed(
            f"implementation_shortfall ({fills:,} fills)",
            fills,
            partial(implementation_shortfall, execution),
        )
    for count in (5_000, 20_000):
        outcomes = [
            OrderOutcome(
                f"O-{index}",
                f"V{index % 50:03d}",
                float(index % 100),
                OrderStatus.REJECTED if index % 9 == 0 else OrderStatus.FILLED,
            )
            for index in range(count)
        ]
        _timed(
            f"venue_quality ({count:,} outcomes, 50 venues)",
            count,
            partial(
                venue_quality,
                [],
                outcomes,
                reference=ReferencePrice.ARRIVAL,
                window_start=0.0,
                window_end=100.0,
            ),
        )
    rates = FxRates.of([FxRate("EUR", "USD", Decimal("1.0850"), 0.0, "benchmark desk")])
    for orders in (100, 400):
        executions = [
            _execution(f"ORDER-{index:05d}", 20, "EUR" if index % 3 == 0 else "USD")
            for index in range(orders)
        ]
        _timed(
            f"execution_quality_report: USD, two currencies ({orders:,} orders)",
            orders,
            partial(
                execution_quality_report,
                executions,
                reporting_currency="USD",
                converter=rates,
                conversion_at=1.0,
            ),
        )


def run_benchmark() -> None:
    print("=" * 94)
    print(
        "AlphaLab v3.9 -- capabilities, lifecycle, algorithms, routing, reconciliation, analytics"
    )
    print("=" * 94)
    benchmark_capabilities()
    benchmark_transitions()
    benchmark_scheduling()
    benchmark_algorithms()
    benchmark_routing()
    benchmark_reconciliation()
    benchmark_analytics()
    print()


if __name__ == "__main__":
    run_benchmark()
