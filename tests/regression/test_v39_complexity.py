"""The v3.9 execution-contract paths stay near-linear, measured on every run.

The assertion is on the **growth ratio** between two input sizes, not on a
duration, so it is about the algorithm rather than the machine. Quadrupling the
input quadruples a linear path and multiplies a quadratic one by sixteen; the
bound sits between the two.

Every ratio is read with the stabilized method of
``test_lifecycle_registry_complexity.py`` -- process CPU time where the platform
keeps it finely, the collector disabled while timing, the two sizes interleaved
and the fastest of five samples compared -- imported from there rather than
copied, so there is one measurement method to maintain.

Which paths, and why these
---------------------------

Each is one whose obvious implementation is quadratic, and each was run against
that implementation -- the defect put back into a copy of the package, one
textual change per defect -- and failed:

* **Capability checks against a declaration with many accounts** -- one check per
  account, each finding its account by bisection. A linear account lookup is
  ``accounts x checks``, and duplicate detection by ``list.count`` is
  ``accounts x accounts`` before the first check runs.
* **A venue event stream over many orders** -- each event reads its one order by
  key. A scan of the mirror per event is ``orders x events``.
* **Working a many-slice schedule** -- each release finds its slice by
  bisection over the slice starts. A scan is ``slices x releases``.
* **Slicing by count** -- the share of the slice due is computed directly.
  Re-apportioning the whole parent per release is ``slices x releases``.
* **Selecting a route across many venues** -- candidates judged once and sorted
  once. Rescanning the quotes per candidate is ``venues x venues``.
* **Reconciling a many-order snapshot** -- duplicate evidence found in one pass.
  ``list.count`` per key is ``orders x orders``.
* **Venue quality over many venues** -- outcomes grouped by venue once. A
  rejection-rate scan per venue is ``venues x outcomes``.

Measured on the release machine, the fixed paths read 4.0x to 4.2x for a 4x
input. With each defect put back alone, the same measurement read 16.0x
(declaration identity rendered per check -- a real defect this file found in the
v3.9 draft), 10.5x (linear account lookup), 13.9x (mirror scan per event), 10.1x
(slice scan per release), 15.6x (re-apportioning per slice), 12.4x (quote rescan
per venue), 15.8x (``list.count`` duplicates) and 15.5x (outcome rescan per
venue) -- every one above the bound. Three workloads were reshaped so the
quadratic term dominates -- releases without fill bookkeeping, venues mostly
ruled out cheaply, a larger declaration -- after their first shapes read 7.4x,
6.0x and 8.4x; the bound did not move.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.lifecycle import VenueEvent, apply_venue_events
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.reconciliation import VenueSnapshot, reconcile_snapshot
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    AccountCapability,
    CapabilityDeclaration,
    ExecutionRequirements,
    MarketCapability,
    Support,
    check_compatibility,
)
from alphalab.core.enums import AssetType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.core.lifecycle import ExecutionEventKind
from alphalab.core.order_request import OrderRequest
from alphalab.execution.algorithms import (
    TWAP,
    AlgorithmTerms,
    Slicing,
    Urgency,
    record_child_execution,
    release_children,
    start_algorithm,
)
from alphalab.execution.commission import FixedCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    NoImpact,
    NoSlippage,
    NoTax,
    PerTradeFee,
    QuotedHalfSpread,
)
from alphalab.execution.quality import OrderOutcome, ReferencePrice, venue_quality
from alphalab.execution.routing import (
    RouteRequest,
    RoutingObjective,
    RoutingPolicy,
    VenueProfile,
    VenueQuote,
    select_route,
)
from tests.regression.test_lifecycle_registry_complexity import _CLOCK, _timings

#: A ratio above linear and well below quadratic.
LINEAR_BOUND = 8.0

YES = Support.SUPPORTED
_ACCOUNT = BrokerAccount(
    "A", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"), Decimal("1"), "USD"
)


def _market() -> MarketCapability:
    return MarketCapability(
        AssetType.EQUITY,
        ANY_LISTING_VENUE,
        frozenset(OrderType),
        frozenset(TimeInForce),
        YES,
        YES,
        YES,
        YES,
    )


def _capability_checks(count: int) -> float:
    accounts = tuple(
        AccountCapability(f"ACC-{index:07d}", frozenset({AssetType.EQUITY}), YES, YES)
        for index in range(count)
    )
    requirements = [
        ExecutionRequirements(
            AssetType.EQUITY,
            "XNAS",
            f"ACC-{index:07d}",
            frozenset({OrderType.LIMIT}),
            frozenset({TimeInForce.DAY}),
            True,
            False,
            False,
            False,
            True,
            frozenset(),
        )
        for index in range(count)
    ]
    start = _CLOCK()
    declaration = CapabilityDeclaration(
        "a", frozenset(), frozenset(), (_market(),), frozenset(), accounts
    )
    for wanted in requirements:
        check_compatibility(wanted, declaration)
    return _CLOCK() - start


def _order(index: int) -> BrokerOrder:
    return BrokerOrder(
        f"B-{index:07d}",
        f"O-{index}",
        "X",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("10"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
        BrokerOrderStatus.SUBMITTED,
        1.0,
        1.0,
    )


def _venue_events(count: int) -> float:
    orders = [_order(index) for index in range(count)]
    state = BrokerState(
        "V",
        ConnectionStatus.CONNECTED,
        _ACCOUNT,
        orders=PersistentMap({o.broker_order_id: o for o in orders}),
    )
    events = []
    for order in orders:
        events.append(VenueEvent(ExecutionEventKind.ORDER_ACCEPTED, 2.0, order.broker_order_id))
        execution = BrokerExecution(
            f"X-{order.broker_order_id}",
            order.broker_order_id,
            "X",
            Decimal("10"),
            Decimal("1"),
            Decimal("0"),
            3.0,
        )
        events.append(
            VenueEvent(
                ExecutionEventKind.ORDER_FILLED, 3.0, order.broker_order_id, execution=execution
            )
        )
    start = _CLOCK()
    apply_venue_events(state, events)
    return _CLOCK() - start


def _parent(quantity: int) -> OrderRequest:
    return OrderRequest("P", "S", "X", Side.BUY, Decimal(quantity), Decimal("10"), 0.0)


def _schedule_run(count: int) -> float:
    """One release at every slice start: the lookup of the slice due is the work."""

    terms = AlgorithmTerms(0.0, float(count), Decimal("1"), OrderType.MARKET, None)
    state = start_algorithm(_parent(count), TWAP(count, Urgency.neutral()), terms)
    start = _CLOCK()
    for index in range(count):
        state, _ = release_children(state, float(index))
    return _CLOCK() - start


def _slicing_run(count: int) -> float:
    terms = AlgorithmTerms(0.0, 1.0, Decimal("1"), OrderType.MARKET, None)
    state = start_algorithm(_parent(count * 3), Slicing(None, count), terms)
    start = _CLOCK()
    for index in range(count):
        state, children = release_children(state, 0.5)
        for child in children:
            state = record_child_execution(state, child.child_id, f"x{index}", child.quantity, 0.5)
    return _CLOCK() - start


_COSTS = ExecutionCostModel(
    QuotedHalfSpread(),
    NoSlippage(),
    NoImpact(),
    FixedCommission(Decimal("0")),
    PerTradeFee(Decimal("1")),
    NoTax(),
)
_DECLARATION = CapabilityDeclaration(
    "v",
    frozenset(),
    frozenset(),
    (_market(),),
    frozenset(),
    (AccountCapability("ACC", frozenset({AssetType.EQUITY}), YES, YES),),
)
_REQUIREMENTS = ExecutionRequirements(
    AssetType.EQUITY,
    "XNAS",
    "ACC",
    frozenset({OrderType.LIMIT}),
    frozenset({TimeInForce.DAY}),
    False,
    False,
    False,
    False,
    False,
    frozenset(),
)


def _route_selection(count: int) -> float:
    """Many venues, most quoting another currency: each is ruled out cheaply, so the
    work that remains is finding each venue's evidence -- which must be a lookup."""

    quotes = [
        VenueQuote(
            f"V{index:06d}",
            "X",
            Decimal("9.99"),
            Decimal("10.01") + Decimal(index % 7) / 1000,
            Decimal("5"),
            Decimal("5"),
            "USD" if index % 10 == 0 else "EUR",
            9.0,
            "q",
        )
        for index in range(count)
    ]
    profiles = [
        VenueProfile(f"V{index:06d}", _DECLARATION, _COSTS, Decimal("0.001"))
        for index in range(count)
    ]
    request = RouteRequest(
        "O", "X", Side.BUY, Decimal(count), None, "USD", _REQUIREMENTS, Decimal("1"), 10.0
    )
    policy = RoutingPolicy(RoutingObjective.LOWEST_ALL_IN_COST, 5.0, None, True, True, frozenset())
    start = _CLOCK()
    select_route(request, quotes, profiles, policy)
    return _CLOCK() - start


def _snapshot_reconciliation(count: int) -> float:
    orders = [_order(index) for index in range(count)]
    state = BrokerState(
        "V",
        ConnectionStatus.CONNECTED,
        _ACCOUNT,
        orders=PersistentMap({o.broker_order_id: o for o in orders}),
    )
    snapshot = VenueSnapshot(5.0, tuple(orders), (), (), _ACCOUNT)
    start = _CLOCK()
    reconcile_snapshot(state, snapshot, evaluated_at=5.0, max_age_seconds=10.0)
    return _CLOCK() - start


def _venue_quality(count: int) -> float:
    outcomes = [
        OrderOutcome(
            f"O-{index}",
            f"V{index // 2:06d}",
            float(index % 100),
            OrderStatus.REJECTED if index % 5 == 0 else OrderStatus.FILLED,
        )
        for index in range(count)
    ]
    start = _CLOCK()
    venue_quality(
        [], outcomes, reference=ReferencePrice.DECISION, window_start=0.0, window_end=100.0
    )
    return _CLOCK() - start


def _ratio(measure: Callable[[int], float], small: int) -> float:
    fast_small, fast_large = _timings(measure, small, small * 4)
    return fast_large / fast_small


def test_capability_checks_grow_linearly_in_the_accounts_declared() -> None:
    assert _ratio(_capability_checks, 2_000) < LINEAR_BOUND


def test_a_venue_event_stream_grows_linearly_in_the_orders_it_touches() -> None:
    assert _ratio(_venue_events, 1_000) < LINEAR_BOUND


def test_working_a_schedule_grows_linearly_in_its_slices() -> None:
    assert _ratio(_schedule_run, 1_000) < LINEAR_BOUND


def test_slicing_by_count_grows_linearly_in_its_slices() -> None:
    assert _ratio(_slicing_run, 500) < LINEAR_BOUND


def test_route_selection_grows_linearly_in_the_venues_considered() -> None:
    assert _ratio(_route_selection, 1_000) < LINEAR_BOUND


def test_snapshot_reconciliation_grows_linearly_in_the_orders_reported() -> None:
    assert _ratio(_snapshot_reconciliation, 2_000) < LINEAR_BOUND


def test_venue_quality_grows_linearly_in_the_venues_and_outcomes() -> None:
    assert _ratio(_venue_quality, 2_000) < LINEAR_BOUND
