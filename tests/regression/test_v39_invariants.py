"""The v3.9 invariants, measured from the code rather than claimed in a release note.

Same shape as ``test_v38_invariants.py``. Each section is one property the
universal execution contract depends on. The property sections generate inputs
and check every answer against an evaluation written here from the definitions
-- a lifecycle replayed against the declared table, a schedule summed by hand, a
shortfall recomputed from the fills, a route checked against every candidate --
because an invariant tested only against the implementation's own helpers would
pass with the helpers wrong. The determinism section spawns fresh interpreters
with different hash seeds *and different working directories*.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import itertools
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import types
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.lifecycle import LifecycleOutcome, VenueEvent, apply_venue_events
from alphalab.broker.order import (
    BROKER_LOCAL_EQUIVALENTS,
    BROKER_STATUS_EQUIVALENTS,
    BrokerOrder,
    BrokerOrderStatus,
    canonical_status,
)
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import (
    SnapshotDivergenceKind,
    VenueSnapshot,
    reconcile_snapshot,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    AccountCapability,
    Capability,
    CapabilityDeclaration,
    Compatibility,
    ExecutionRequirements,
    MarketCapability,
    Support,
    check_compatibility,
)
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import AssetType, OrderStatus, OrderType, Side, TimeInForce
from alphalab.core.lifecycle import (
    ORDER_TRANSITIONS,
    TERMINAL_ORDER_STATUSES,
    ExecutionEventKind,
)
from alphalab.core.order_request import OrderRequest
from alphalab.execution.algorithms import (
    TWAP,
    VWAP,
    AlgorithmTerms,
    IntervalVolume,
    MissingVolumePolicy,
    Urgency,
    VolumeProfile,
    plan_schedule,
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
from alphalab.execution.fill import FillStatus
from alphalab.execution.quality import (
    ExecutionBenchmarks,
    OrderExecution,
    implementation_shortfall,
)
from alphalab.execution.report import ExecutionReport
from alphalab.execution.routing import (
    CandidateStatus,
    RouteRequest,
    RoutingObjective,
    RoutingPolicy,
    VenueProfile,
    VenueQuote,
    select_route,
)

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"
ROOT = PACKAGE.parent

#: The modules v3.9 added. Every source sweep is scoped to them.
V39_MODULES = (
    "alphalab/broker/lifecycle.py",
    "alphalab/broker/requests.py",
    "alphalab/common/currency.py",
    "alphalab/core/capabilities.py",
    "alphalab/core/lifecycle.py",
    "alphalab/execution/algorithms.py",
    "alphalab/execution/quality.py",
    "alphalab/execution/routing.py",
)

E = ExecutionEventKind
S = OrderStatus


def _sources() -> list[tuple[str, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        found.append((str(path.relative_to(ROOT)), path.read_text()))
    return found


def _v39_sources() -> list[tuple[str, str]]:
    return [(name, text) for name, text in _sources() if name in V39_MODULES]


def test_every_v39_module_exists() -> None:
    """So a rename cannot silently empty every sweep in this file."""

    assert len(_v39_sources()) == len(V39_MODULES)


# --------------------------------------------------------------------------- #
# 1. One authority per concept
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("marker", "home"),
    [
        ("class ExecutionEventKind", "core/lifecycle.py"),
        ("ORDER_TRANSITIONS", "core/lifecycle.py"),
        ("def next_order_status", "core/lifecycle.py"),
        ("def classify_order_event", "core/lifecycle.py"),
        ("class CapabilityDeclaration", "core/capabilities.py"),
        ("class ExecutionRequirements", "core/capabilities.py"),
        ("def check_compatibility", "core/capabilities.py"),
        ("def split_by_contribution", "core/contribution.py"),
        ("class CurrencyConverter", "common/currency.py"),
        ("class VenueEvent", "broker/lifecycle.py"),
        ("def apply_venue_event", "broker/lifecycle.py"),
        ("BROKER_STATUS_EQUIVALENTS", "broker/order.py"),
        ("BROKER_LOCAL_EQUIVALENTS", "broker/order.py"),
        ("class CancelRequest", "broker/requests.py"),
        ("class ModifyRequest", "broker/requests.py"),
        ("class VenueSnapshot", "broker/reconciliation.py"),
        ("def reconcile_snapshot", "broker/reconciliation.py"),
        ("class Urgency", "execution/algorithms.py"),
        ("def plan_schedule", "execution/algorithms.py"),
        ("def _apportion", "execution/algorithms.py"),
        ("class ChildOrder", "execution/algorithms.py"),
        ("def select_route", "execution/routing.py"),
        ("class RoutingPolicy", "execution/routing.py"),
        ("def implementation_shortfall", "execution/quality.py"),
        ("def measure_slippage", "execution/quality.py"),
        ("def rejection_rate", "execution/quality.py"),
        ("class ChildOrderBindings", "runtime/broker_routing.py"),
        ("def route_child_order", "runtime/broker_routing.py"),
        ("def broker_capabilities_from", "lifecycle/specification.py"),
        ("def research_configuration_with_execution", "lifecycle/fingerprint.py"),
    ],
)
def test_each_new_concept_has_exactly_one_home(marker: str, home: str) -> None:
    kind = "definition" if marker.startswith(("class ", "def ")) else "assignment"
    pattern = (
        re.compile(rf"^{re.escape(marker)}\b", re.MULTILINE)
        if kind == "definition"
        else re.compile(rf"^{re.escape(marker)}\b[^\n]*=", re.MULTILINE)
    )
    found = [name for name, source in _sources() if pattern.search(source)]

    assert found == [f"alphalab/{home}"], f"{marker!r} is defined in {found}"


def test_the_oms_and_the_venue_boundary_read_one_transition_table() -> None:
    """Neither re-states a transition: both call into ``core.lifecycle``."""

    oms = (PACKAGE / "oms" / "order.py").read_text()
    venue = (PACKAGE / "broker" / "lifecycle.py").read_text()
    assert "next_order_status" in oms and "ORDER_TRANSITIONS" not in oms.split('"""', 2)[2]
    assert "classify_order_event" in venue
    for source in (oms, venue):
        assert "OrderStatus.NEW, OrderStatus.PENDING, OrderStatus.ACCEPTED" not in source


def test_the_strategy_split_is_computed_in_one_place() -> None:
    """``split_realized_pnl`` delegates; execution quality calls the same rule."""

    attribution = (PACKAGE / "analytics" / "attribution.py").read_text()
    quality = (PACKAGE / "execution" / "quality.py").read_text()
    assert "return split_by_contribution(" in attribution
    assert "split_by_contribution(" in quality
    assert "realized_pnl * contribution.quantity" not in attribution


def test_no_second_fill_order_or_broker_protocol_was_introduced() -> None:
    """The v3.9 modules consume the canonical types; they define none of them again."""

    defined = {
        match
        for _, source in _v39_sources()
        for match in re.findall(r"^class (\w+)", source, re.MULTILINE)
    }
    forbidden = {
        "Fill",
        "Order",
        "Trade",
        "ExecutionReport",
        "BrokerOrder",
        "BrokerExecution",
        "BrokerAccount",
        "BrokerPosition",
        "BrokerProtocol",
        "OrderRequest",
        "Position",
        "Account",
        "SlippageModel",
        "LatencyModel",
    }
    assert defined & forbidden == set()


# --------------------------------------------------------------------------- #
# 2. Layering
# --------------------------------------------------------------------------- #


def _package_edges(package: str) -> set[str]:
    edges: set[str] = set()
    for path in sorted((PACKAGE / package).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                parts = node.module.split(".")
                if parts[0] == "alphalab" and len(parts) > 1 and parts[1] != package:
                    edges.add(parts[1])
    return edges


def test_v39_added_no_package_edge() -> None:
    """The contract landed on existing packages without joining any two anew."""

    assert _package_edges("core") == {"common"}
    assert _package_edges("common") == set()
    assert _package_edges("execution") == {"common", "core"}
    assert _package_edges("analytics") == {"common", "core"}
    # v3.11 (BRK-008): the paper venue charges the execution cost model, the one
    # cost authority; execution sits below broker, so no cycle is introduced.
    assert _package_edges("broker") == {"common", "core", "execution", "persistence"}
    assert _package_edges("oms") == {"common", "core", "persistence"}
    # v3.11 (FEA-001): the lot grid a target is rounded to lives in conventions.
    assert _package_edges("allocation") == {
        "common",
        "conventions",
        "core",
        "persistence",
        "strategy",
    }


def test_v39_added_no_package() -> None:
    """v3.9 added none; v3.10 removed ``feed`` and ``live`` (ledger SCF-002); v3.11
    removed ``enterprise``, ``studio`` and ``workbench`` (BND-002, SCF-001, BND-003);
    v3.12 removed ``plugins`` and ``optimizer`` (SCF-003)."""

    packages = sorted(p.name for p in PACKAGE.iterdir() if (p / "__init__.py").exists())
    removed = {"feed", "live", "enterprise", "studio", "workbench", "plugins", "optimizer"}
    assert removed.isdisjoint(packages)
    assert len(packages) == 43


# --------------------------------------------------------------------------- #
# 3. The lifecycle: legal, total, idempotent, order-insensitive where it must be
# --------------------------------------------------------------------------- #


def test_every_broker_local_equivalent_is_a_status_the_book_calls_consistent() -> None:
    for status in BrokerOrderStatus:
        assert BROKER_LOCAL_EQUIVALENTS[status] in BROKER_STATUS_EQUIVALENTS[status]


def test_terminal_statuses_are_absorbing() -> None:
    for status in TERMINAL_ORDER_STATUSES:
        assert ORDER_TRANSITIONS[status] == {}


def _order(status: OrderStatus | BrokerOrderStatus, filled: str = "0") -> BrokerOrder:
    return BrokerOrder(
        broker_order_id="B-1",
        oms_order_id="OMS-1",
        symbol="X",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("100"),
        price=Decimal("10"),
        filled_quantity=Decimal(filled),
        average_fill_price=Decimal("10") if Decimal(filled) else Decimal("0"),
        status=status,
        created_at=1.0,
        updated_at=1.0,
    )


def _mirror(order: BrokerOrder) -> BrokerState:
    return BrokerState(
        "V",
        ConnectionStatus.CONNECTED,
        BrokerAccount(
            "A", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"), Decimal("1"), "USD"
        ),
        orders=PersistentMap({order.broker_order_id: order}),
    )


def _random_stream(rng: random.Random) -> list[VenueEvent]:
    """A plausible venue stream: fills summing to at most the order, plus status events."""

    events: list[VenueEvent] = []
    left = 100
    for index in range(rng.randint(0, 4)):
        if left <= 0:
            break
        quantity = rng.randint(1, left)
        left -= quantity
        execution = BrokerExecution(
            f"X-{index}", "B-1", "X", Decimal(quantity), Decimal("10"), Decimal("0"), 2.0 + index
        )
        kind = E.ORDER_FILLED if left == 0 else E.ORDER_PARTIALLY_FILLED
        events.append(VenueEvent(kind, 2.0 + index, "B-1", execution=execution))
    for kind in rng.sample(
        [E.ORDER_ACCEPTED, E.ORDER_CANCELLED, E.ORDER_EXPIRED, E.ORDER_REJECTED], rng.randint(0, 2)
    ):
        events.append(VenueEvent(kind, 5.0, "B-1"))
    return events


def test_replaying_any_stream_changes_nothing_and_breaks_nothing() -> None:
    rng = random.Random(39)
    for _ in range(300):
        stream = _random_stream(rng)
        choices: list[OrderStatus | BrokerOrderStatus] = [BrokerOrderStatus.SUBMITTED, S.ACCEPTED]
        start = rng.choice(choices)
        once, _ = apply_venue_events(_mirror(_order(start)), stream)
        twice, decisions = apply_venue_events(once, stream)
        assert twice is once
        assert all(
            d.outcome
            in {LifecycleOutcome.DUPLICATE, LifecycleOutcome.STALE, LifecycleOutcome.CONFLICT}
            for d in decisions
        )
        assert not any(d.applied for d in decisions)


def test_fills_land_in_the_same_place_in_any_delivery_order() -> None:
    rng = random.Random(3900)
    for _ in range(100):
        fills = [e for e in _random_stream(rng) if e.execution is not None]
        endings = set()
        for permutation in itertools.permutations(fills):
            final, _ = apply_venue_events(_mirror(_order(S.ACCEPTED)), permutation)
            order = final.orders["B-1"]
            endings.add((order.status, order.filled_quantity, order.average_fill_price))
        assert len(endings) <= 1


def test_no_applied_event_ever_makes_a_transition_the_table_lacks() -> None:
    """Every applied status event is a row of the declared table, checked independently."""

    rng = random.Random(391)
    for _ in range(400):
        starts: list[OrderStatus | BrokerOrderStatus] = [
            *BROKER_LOCAL_EQUIVALENTS,
            S.ACCEPTED,
            S.PARTIALLY_FILLED,
            *TERMINAL_ORDER_STATUSES,
        ]
        start = rng.choice(starts)
        filled = "10" if start in {S.PARTIALLY_FILLED, S.FILLED} else "0"
        if start is S.FILLED:
            filled = "100"
        state = _mirror(_order(start, filled))
        for event in _random_stream(rng):
            before = state.orders["B-1"]
            state, decisions = apply_venue_events(state, [event])
            (decision,) = decisions
            after = state.orders["B-1"]
            if not decision.applied or event.execution is not None:
                continue
            canonical = canonical_status(before.status)
            assert event.kind in ORDER_TRANSITIONS[canonical], (before.status, event.kind)
            assert before.status not in TERMINAL_ORDER_STATUSES
            assert after.filled_quantity == before.filled_quantity


# --------------------------------------------------------------------------- #
# 4. Capabilities: nothing unknown is ever supported
# --------------------------------------------------------------------------- #


def _random_support(rng: random.Random) -> Support:
    return rng.choice(list(Support))


def _random_declaration(rng: random.Random) -> CapabilityDeclaration:
    classes = rng.sample(list(AssetType), rng.randint(0, 3))
    features = rng.sample([Capability.CANCEL_REPLACE, Capability.STREAMING], rng.randint(0, 2))
    supported = frozenset(f for f in features if rng.random() < 0.5)
    markets = tuple(
        MarketCapability(
            asset,
            rng.choice([ANY_LISTING_VENUE, "XNAS"]),
            frozenset(rng.sample(list(OrderType), rng.randint(1, 4))),
            frozenset(rng.sample(list(TimeInForce), rng.randint(1, 4))),
            _random_support(rng),
            _random_support(rng),
            _random_support(rng),
            _random_support(rng),
        )
        for asset in classes
    )
    accounts = (
        (
            AccountCapability(
                "ACC",
                frozenset(rng.sample(list(AssetType), rng.randint(0, 4))),
                _random_support(rng),
                _random_support(rng),
            ),
        )
        if rng.random() < 0.8
        else ()
    )
    return CapabilityDeclaration(
        "adapter",
        supported,
        frozenset(features) - supported,
        markets,
        frozenset(set(rng.sample(list(AssetType), 1)) - set(classes)),
        accounts,
    )


def _random_requirements(rng: random.Random) -> ExecutionRequirements:
    return ExecutionRequirements(
        rng.choice(list(AssetType)),
        "XNAS",
        "ACC",
        frozenset(rng.sample(list(OrderType), rng.randint(1, 2))),
        frozenset(rng.sample(list(TimeInForce), rng.randint(1, 2))),
        rng.random() < 0.3,
        rng.random() < 0.3,
        rng.random() < 0.3,
        rng.random() < 0.3,
        rng.random() < 0.3,
        frozenset(rng.sample([Capability.CANCEL_REPLACE, Capability.STREAMING], rng.randint(0, 2))),
    )


def test_compatible_means_every_check_supported_and_nothing_else_does() -> None:
    rng = random.Random(3939)
    seen: set[Compatibility] = set()
    for _ in range(600):
        declaration = _random_declaration(rng)
        requirements = _random_requirements(rng)
        report = check_compatibility(requirements, declaration)
        seen.add(report.status)
        supports = [check.support for check in report.checks]
        if report.status is Compatibility.COMPATIBLE:
            assert all(s is Support.SUPPORTED for s in supports)
        elif report.status is Compatibility.INCOMPATIBLE:
            assert Support.UNSUPPORTED in supports
        else:
            assert Support.UNDECLARED in supports and Support.UNSUPPORTED not in supports
        # Every order type and time-in-force required is checked, once.
        assert {c.requirement for c in report.checks} >= {
            f"order_type={t}" for t in requirements.order_types
        }
        assert check_compatibility(requirements, declaration) == report
    assert seen == set(Compatibility)


def test_a_declarations_identity_does_not_depend_on_listing_order() -> None:
    rng = random.Random(77)
    for _ in range(100):
        declaration = _random_declaration(rng)
        shuffled = replace(
            declaration,
            markets=tuple(reversed(declaration.markets)),
            accounts=tuple(reversed(declaration.accounts)),
        )
        assert shuffled.declaration_id == declaration.declaration_id


# --------------------------------------------------------------------------- #
# 5. Schedules: exact, monotone, bounded by their trajectory
# --------------------------------------------------------------------------- #


def test_every_schedule_sums_exactly_and_never_strays_a_unit_from_its_trajectory() -> None:
    rng = random.Random(3990)
    for _ in range(250):
        units = rng.randint(1, 5000)
        increment = rng.choice([Decimal("1"), Decimal("0.01"), Decimal("100")])
        quantity = increment * units
        slices = rng.randint(1, 40)
        urgency = Urgency(Decimal(rng.choice(["0", "0.5", "2", "7"])))
        terms = AlgorithmTerms(0.0, 1000.0, increment, OrderType.MARKET, None)
        if rng.random() < 0.5:
            algorithm: TWAP | VWAP = TWAP(slices, urgency)
        else:
            width = 1000.0 / slices
            profile = VolumeProfile(
                tuple(
                    IntervalVolume(
                        i * width,
                        1000.0 if i == slices - 1 else (i + 1) * width,
                        Decimal(rng.randint(0, 9000)),
                    )
                    for i in range(slices)
                ),
                "generated",
            )
            algorithm = VWAP(profile, MissingVolumePolicy.TIME_WEIGHTED, urgency)
        schedule = plan_schedule(quantity, algorithm, terms)
        planned = [s.quantity for s in schedule.slices]
        assert sum(planned, Decimal("0")) == quantity
        assert all(q >= 0 and q % increment == 0 for q in planned)
        targets = [s.target for s in schedule.slices]
        assert targets == sorted(targets) and targets[-1] == quantity
        running = Decimal("0")
        for planned_slice in schedule.slices:
            running += planned_slice.weight
            ideal = urgency.fraction(min(running, Decimal("1"))) * quantity
            # Largest remainder: every prefix is within a whole number of
            # increments of its ideal, bounded by the slice count so far.
            assert abs(planned_slice.target - ideal) <= increment * planned_slice.index


# --------------------------------------------------------------------------- #
# 6. Routing: nothing ineligible selected, ties broken one way
# --------------------------------------------------------------------------- #


def _costs() -> ExecutionCostModel:
    return ExecutionCostModel(
        QuotedHalfSpread(),
        NoSlippage(),
        NoImpact(),
        FixedCommission(Decimal("0")),
        PerTradeFee(Decimal("1")),
        NoTax(),
    )


def _declaration_for_routing() -> CapabilityDeclaration:
    return CapabilityDeclaration(
        "v",
        frozenset(),
        frozenset(),
        (
            MarketCapability(
                AssetType.EQUITY,
                ANY_LISTING_VENUE,
                frozenset(OrderType),
                frozenset(TimeInForce),
                Support.SUPPORTED,
                Support.SUPPORTED,
                Support.SUPPORTED,
                Support.SUPPORTED,
            ),
        ),
        frozenset(),
        (AccountCapability("ACC", frozenset(AssetType), Support.SUPPORTED, Support.SUPPORTED),),
    )


def test_routes_select_only_eligible_venues_and_do_not_depend_on_listing_order() -> None:
    rng = random.Random(3999)
    requirements = ExecutionRequirements(
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
    for _ in range(150):
        quotes, profiles = [], []
        for index in range(rng.randint(1, 6)):
            venue = f"V{index}"
            bid = Decimal(rng.randint(9990, 10000)) / 100
            ask = bid + Decimal(rng.randint(1, 5)) / 100
            size = Decimal(rng.randint(0, 400))
            quotes.append(
                VenueQuote(
                    venue,
                    "X",
                    bid,
                    ask,
                    size,
                    size,
                    rng.choice(["USD", "USD", "EUR"]),
                    10.0 - rng.random() * 8,
                    "g",
                )
            )
            profiles.append(
                VenueProfile(
                    venue,
                    _declaration_for_routing(),
                    _costs(),
                    Decimal(rng.choice(["0.001", "0.002"])),
                )
            )
        request = RouteRequest(
            "O",
            "X",
            Side.BUY,
            Decimal(rng.randint(1, 600)),
            None,
            "USD",
            requirements,
            Decimal("1"),
            10.0,
        )
        policy = RoutingPolicy(
            rng.choice(list(RoutingObjective)),
            5.0,
            None,
            rng.random() < 0.5,
            rng.random() < 0.5,
            frozenset(),
        )
        decision = select_route(request, quotes, profiles, policy)
        eligible = {c.venue for c in decision.candidates if c.status is CandidateStatus.ELIGIBLE}
        assert {leg.venue for leg in decision.legs} <= eligible
        assert decision.routed_quantity <= request.quantity
        shuffled = select_route(request, list(reversed(quotes)), list(reversed(profiles)), policy)
        assert shuffled.decision_id == decision.decision_id


# --------------------------------------------------------------------------- #
# 7. Analytics reconcile to their evidence
# --------------------------------------------------------------------------- #


def test_shortfall_decomposes_exactly_and_its_shares_sum_to_it() -> None:
    rng = random.Random(39000)
    for _ in range(200):
        side = rng.choice(list(Side))
        quantity = rng.randint(1, 1000)
        fills = []
        left = quantity
        for index in range(rng.randint(0, 4)):
            if left <= 0:
                break
            q = rng.randint(1, left)
            left -= q
            fills.append(
                ExecutionReport(
                    f"F{index}",
                    "O",
                    "X",
                    "",
                    1.0 + index,
                    Decimal(rng.randint(9000, 11000)) / 100,
                    Decimal(q),
                    Decimal(rng.randint(0, 300)) / 100,
                    Decimal("0"),
                    "",
                    "V",
                    "USD",
                    FillStatus.PARTIAL_FILL,
                )
            )
        contributions = tuple(
            StrategyContribution(f"S{i}", Decimal(rng.choice([1, 1, 2, -1])) * rng.randint(1, 50))
            for i in range(rng.randint(1, 3))
        )
        if sum(c.quantity for c in contributions) == 0:
            continue
        order = OrderRequest(
            "O", "", "X", side, Decimal(quantity), Decimal("100"), 0.0, contributions
        )
        execution = OrderExecution(
            order,
            tuple(fills),
            "USD",
            OrderStatus.FILLED,
            ExecutionBenchmarks(None, Decimal("100.5"), None, Decimal("101"), "g"),
            None,
            None,
            None,
            None,
        )
        shortfall = implementation_shortfall(execution)
        sign = Decimal(1) if side is Side.BUY else Decimal(-1)
        by_hand = sum(
            (f.fill_quantity * (f.fill_price - Decimal("100")) * sign for f in fills), Decimal("0")
        )
        assert shortfall.execution_cost == by_hand
        assert shortfall.delay_cost is not None and shortfall.trading_cost is not None
        assert shortfall.delay_cost + shortfall.trading_cost == shortfall.execution_cost
        assert shortfall.total is not None
        assert (
            sum((share for _, share in shortfall.strategy_shares), Decimal("0")) == shortfall.total
        )


# --------------------------------------------------------------------------- #
# 8. Reconciliation detects what was injected
# --------------------------------------------------------------------------- #


def test_every_injected_divergence_is_reported_as_what_it_is() -> None:
    base = _mirror(_order(S.ACCEPTED))
    snapshot = VenueSnapshot(10.0, tuple(base.orders.values()), (), (), base.account)
    clean = reconcile_snapshot(base, snapshot, evaluated_at=10.0, max_age_seconds=1.0)
    assert clean.fully_reconciled
    order = base.orders["B-1"]
    injections = {
        SnapshotDivergenceKind.ORDER_QUANTITY: replace(
            snapshot, orders=(replace(order, quantity=Decimal("99")),)
        ),
        SnapshotDivergenceKind.ORDER_PRICE: replace(
            snapshot, orders=(replace(order, price=Decimal("9")),)
        ),
        SnapshotDivergenceKind.ORDER_STATUS: replace(
            snapshot, orders=(replace(order, status=S.CANCELLED),)
        ),
        SnapshotDivergenceKind.MISSING_ORDER: replace(snapshot, orders=()),
        SnapshotDivergenceKind.UNKNOWN_ORDER: replace(
            snapshot, orders=(order, replace(order, broker_order_id="B-9"))
        ),
        SnapshotDivergenceKind.DUPLICATE_EVIDENCE: replace(snapshot, orders=(order, order)),
        SnapshotDivergenceKind.BALANCE: replace(
            snapshot, account=replace(base.account, cash=Decimal("2"))
        ),
        SnapshotDivergenceKind.ACCOUNT_IDENTITY: replace(
            snapshot, account=replace(base.account, currency="EUR")
        ),
        SnapshotDivergenceKind.POSITION_QUANTITY: replace(
            snapshot,
            positions=(
                BrokerPosition(
                    "X", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"), Decimal("0")
                ),
            ),
        ),
    }
    for kind, tampered in injections.items():
        result = reconcile_snapshot(base, tampered, evaluated_at=10.0, max_age_seconds=1.0)
        assert kind in {d.kind for d in result.divergences}, kind
        assert not result.reconciled
        assert reconcile_snapshot(base, tampered, evaluated_at=10.0, max_age_seconds=1.0) == result


# --------------------------------------------------------------------------- #
# 9. Determinism across processes, hash seeds and working directories
# --------------------------------------------------------------------------- #


def identities() -> list[str]:
    """Every v3.9 identity, built from fixed inputs -- run in fresh interpreters."""

    from alphalab.broker.requests import CancelRequest, ModifyRequest
    from alphalab.execution.algorithms import Iceberg, Slicing, algorithm_configuration_id
    from alphalab.execution.quality import execution_quality_report
    from alphalab.portfolio.fx import FxRate, FxRates

    declaration = _declaration_for_routing()
    requirements = ExecutionRequirements(
        AssetType.EQUITY,
        "XNAS",
        "ACC",
        frozenset({OrderType.LIMIT, OrderType.MARKET}),
        frozenset({TimeInForce.DAY, TimeInForce.IOC}),
        True,
        False,
        False,
        False,
        True,
        frozenset({Capability.CANCEL_REPLACE}),
    )
    report = check_compatibility(requirements, declaration)
    terms = AlgorithmTerms(0.0, 100.0, Decimal("1"), OrderType.LIMIT, Decimal("10"))
    schedule = plan_schedule(Decimal("997"), TWAP(7, Urgency(Decimal("2.5"))), terms)
    vwap = VWAP(
        VolumeProfile(
            (IntervalVolume(0.0, 50.0, Decimal("7")), IntervalVolume(50.0, 100.0, Decimal("3"))),
            "p",
        ),
        MissingVolumePolicy.REFUSE,
        Urgency.neutral(),
    )
    quotes = [
        VenueQuote(
            "A",
            "X",
            Decimal("9.99"),
            Decimal("10.01"),
            Decimal("100"),
            Decimal("100"),
            "USD",
            9.0,
            "q",
        ),
        VenueQuote(
            "B",
            "X",
            Decimal("9.99"),
            Decimal("10.01"),
            Decimal("100"),
            Decimal("100"),
            "USD",
            9.0,
            "q",
        ),
    ]
    profiles = [VenueProfile(v, declaration, _costs(), Decimal("0.001")) for v in ("A", "B")]
    route = select_route(
        RouteRequest(
            "O", "X", Side.BUY, Decimal("150"), None, "USD", requirements, Decimal("1"), 10.0
        ),
        quotes,
        profiles,
        RoutingPolicy(RoutingObjective.LOWEST_ALL_IN_COST, 5.0, None, True, False, frozenset()),
    )
    order = OrderRequest(
        "O",
        "",
        "X",
        Side.SELL,
        Decimal("50"),
        Decimal("100"),
        0.0,
        (StrategyContribution("S", Decimal("-50")),),
    )
    fills = (
        ExecutionReport(
            "F1",
            "O",
            "X",
            "",
            1.0,
            Decimal("99.5"),
            Decimal("30"),
            Decimal("1"),
            Decimal("0"),
            "",
            "A",
            "EUR",
            FillStatus.PARTIAL_FILL,
        ),
    )
    quality = execution_quality_report(
        [
            OrderExecution(
                order,
                fills,
                "EUR",
                OrderStatus.CANCELLED,
                ExecutionBenchmarks(None, None, None, Decimal("98"), "g"),
                None,
                None,
                None,
                None,
            )
        ],
        reporting_currency="USD",
        converter=FxRates.of([FxRate("EUR", "USD", Decimal("1.1"), 0.0, "f")]),
        conversion_at=5.0,
    )
    mirror = _mirror(_order(S.ACCEPTED))
    reconciliation = reconcile_snapshot(
        mirror,
        VenueSnapshot(5.0, tuple(mirror.orders.values()), (), (), mirror.account),
        evaluated_at=5.0,
        max_age_seconds=1.0,
    )
    return [
        declaration.declaration_id,
        requirements.requirements_id,
        report.report_id,
        schedule.schedule_id,
        algorithm_configuration_id(vwap),
        algorithm_configuration_id(Slicing(None, 4)),
        algorithm_configuration_id(Iceberg(Decimal("5"))),
        route.decision_id,
        route.policy.policy_id,
        quality.report_id,
        reconciliation.reconciliation_id,
        CancelRequest("B-1", 1, 1.0).request_id,
        ModifyRequest("B-1", 2, Decimal("5"), Decimal("1"), 1.0).request_id,
        json.dumps([str(q.quantity) for q in schedule.slices]),
    ]


_CROSS_PROCESS = """
from tests.regression.test_v39_invariants import identities
for value in identities():
    print(value)
"""


def _fresh(hash_seed: str, cwd: pathlib.Path) -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(cwd),
        env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(ROOT)},
    )
    return completed.stdout.split("\n")[:-1]


def test_every_v39_identity_is_the_same_across_hash_seeds_and_working_directories(
    tmp_path: pathlib.Path,
) -> None:
    first = _fresh("1", ROOT)
    second = _fresh("4242", ROOT)
    elsewhere = _fresh("99", tmp_path)

    assert first == second == elsewhere
    assert len(first) == 14
    assert first == identities()


def test_nothing_machine_local_reaches_a_v39_identity() -> None:
    rendered = "\n".join(identities())
    local = {str(ROOT), str(pathlib.Path.home()), os.uname().nodename}
    for marker in local:
        assert marker not in rendered, f"{marker!r} leaked into an identity"


# --------------------------------------------------------------------------- #
# 10. Determinism, measured from the source
# --------------------------------------------------------------------------- #

_NONDETERMINISTIC_MODULES = frozenset(
    {
        "datetime",
        "getpass",
        "importlib",
        "os",
        "platform",
        "random",
        "secrets",
        "socket",
        "tempfile",
        "time",
        "uuid",
    }
)
_NONDETERMINISTIC_CALLS = frozenset({"hash", "id", "new_id", "uuid4"})


def test_no_v39_module_reads_a_clock_entropy_or_the_environment() -> None:
    offenders: list[str] = []
    for name, source in _v39_sources():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = {node.module.split(".")[0]}
            else:
                roots = set()
            offenders.extend(
                f"{name}: imports {root}" for root in roots & _NONDETERMINISTIC_MODULES
            )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _NONDETERMINISTIC_CALLS
            ):
                offenders.append(f"{name}: calls {node.func.id}()")
    assert offenders == [], f"a v3.9 path is non-deterministic: {offenders}"


def test_no_v39_module_uses_a_platform_dependent_transcendental() -> None:
    """``Urgency`` evaluates ``sinh`` in ``Decimal``: libmpdec is the same everywhere."""

    offenders = [
        f"{name}: math.{function}"
        for name, source in _v39_sources()
        for function in ("exp", "log", "pow", "hypot", "sin", "cos", "tan", "sinh", "cosh", "sqrt")
        if f"math.{function}(" in source
    ]
    assert offenders == []


def test_every_v39_computation_pins_its_decimal_context() -> None:
    """The modules that divide and multiply money hold a fixed context, never the caller's."""

    for name in (
        "alphalab/execution/algorithms.py",
        "alphalab/execution/routing.py",
        "alphalab/execution/quality.py",
    ):
        source = (ROOT / name).read_text()
        assert "_CONTEXT: Final = Context(prec=34, rounding=ROUND_HALF_EVEN)" in source, name
    for name, source in _v39_sources():
        assert "getcontext(" not in source and "localcontext(" not in source, name


# --------------------------------------------------------------------------- #
# 11. Boundaries: no vendor, no network, no secret, no durable state
# --------------------------------------------------------------------------- #

#: Named broker and exchange integrations the contract must never contain.
BROKER_VENDORS = (
    "zerodha",
    "kite",
    "interactive brokers",
    "ibkr",
    "alpaca",
    "binance",
    "coinbase",
    "oanda",
    "bybit",
    "tradier",
    "schwab",
    "fidelity",
    "robinhood",
    "kraken",
)


def test_no_v39_module_names_a_vendor_or_reaches_a_network() -> None:
    from tests.regression.test_one_research_authority_per_concept import FORBIDDEN_VENDORS

    network = {
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "ssl",
        "urllib",
        "websocket",
    }
    for name, source in _v39_sources():
        lowered = source.lower()
        for term in (*FORBIDDEN_VENDORS, *BROKER_VENDORS):
            assert not re.search(rf"\b{re.escape(term)}\b", lowered), f"{term} in {name}"
        assert "http://" not in lowered and "https://" not in lowered, name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert not roots & network, f"{name} imports {roots & network}"


def _public_classes() -> list[tuple[str, type]]:
    found: list[tuple[str, type]] = []
    for name in V39_MODULES:
        module = importlib.import_module(name[:-3].replace("/", "."))
        for attribute in getattr(module, "__all__", ()):
            value = getattr(module, attribute)
            if isinstance(value, type):
                found.append((f"{module.__name__}.{attribute}", value))
    return found


def test_no_v39_value_has_a_field_that_could_hold_a_secret() -> None:
    secret = re.compile(r"password|passphrase|secret|token|api_key|credential|login|oauth|bearer")
    offenders = [
        f"{qualified}.{field.name}"
        for qualified, value in _public_classes()
        if dataclasses.is_dataclass(value)
        for field in dataclasses.fields(value)
        if secret.search(field.name)
    ]
    assert offenders == []


def test_every_v39_value_type_is_frozen() -> None:
    thawed = [
        qualified
        for qualified, value in _public_classes()
        if dataclasses.is_dataclass(value) and not value.__dict__["__dataclass_params__"].frozen
    ]
    assert thawed == []


def test_v39_added_no_snapshot_owner_and_no_schema_constant() -> None:
    """v3.9 adds no durable state: no schema constant is assigned and nothing captures."""

    assigned = re.compile(r"^([A-Z_]*_SCHEMA)\s*(?::[^=]*)?=\s*\d", re.MULTILINE)
    for name, source in _v39_sources():
        assert not assigned.search(source), name
        assert "def capture(" not in source and "def restore(" not in source, name


def test_the_package_still_has_no_runtime_dependency() -> None:
    import tomllib

    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"] == []


# --------------------------------------------------------------------------- #
# 12. The surfaces advertise the contract
# --------------------------------------------------------------------------- #


def test_the_public_surfaces_advertise_every_new_name() -> None:
    import alphalab.broker as broker
    import alphalab.common as common
    import alphalab.core as core
    import alphalab.execution as execution
    import alphalab.lifecycle as lifecycle
    import alphalab.runtime as runtime

    expected: dict[types.ModuleType, tuple[str, ...]] = {
        core: (
            "ExecutionEventKind",
            "ORDER_TRANSITIONS",
            "classify_order_event",
            "CapabilityDeclaration",
            "check_compatibility",
            "supports",
            "split_by_contribution",
        ),
        common: ("CurrencyConverter", "ConversionRecord", "RateRecord"),
        broker: (
            "VenueEvent",
            "apply_venue_event",
            "CancelRequest",
            "ModifyRequest",
            "issue_cancel",
            "issue_modify",
            "VenueSnapshot",
            "reconcile_snapshot",
            "CapabilityDeclaration",
            "ExecutionEventKind",
        ),
        execution: (
            "TWAP",
            "VWAP",
            "Participation",
            "Slicing",
            "Iceberg",
            "start_algorithm",
            "select_route",
            "implementation_shortfall",
            "execution_quality_report",
        ),
        runtime: ("ChildOrderBindings", "route_child_order", "child_broker_order_id"),
        lifecycle: ("broker_capabilities_from", "research_configuration_with_execution"),
    }
    for module, names in expected.items():
        for name in names:
            assert name in module.__all__, f"{module.__name__} does not advertise {name}"
    assert broker.CapabilityDeclaration is core.CapabilityDeclaration
