"""Execution algorithms work their children through the historical drivers (v4.1.1, FUT-009).

Every test here runs a parent order through :meth:`BacktestEngine.run` or
:meth:`ReplayBacktest.run` with an :class:`ExecutionPlan`: the strategy's intent
becomes one OMS order, the algorithm releases children at the asset's bars, the
run's own fill policy and cost model fill each child, and the fill settles on the
parent. Nothing is asserted about an algorithm in isolation -- that is
``tests/unit/execution/test_execution_algorithms.py`` -- only about what the run
did with it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation.events import AllocationReservationReleased
from alphalab.backtesting import (
    AlgorithmAssignment,
    AlgorithmicResult,
    BacktestEngine,
    BacktestResult,
    ExecutionPlan,
    ExecutionPlanError,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduledSplit,
    VenueCheck,
)
from alphalab.backtesting.algorithmic import book_from_primitives, capture_book
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.common.order_terms import MARKET, OrderTerms, TimeInForce
from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    AccountCapability,
    Capability,
    CapabilityDeclaration,
    MarketCapability,
    Support,
)
from alphalab.core.enums import AssetType, OrderStatus, OrderType
from alphalab.execution.algorithms import (
    TWAP,
    VWAP,
    AlgorithmStatus,
    AlgorithmTerms,
    Iceberg,
    IncompletePolicy,
    IntervalVolume,
    MissingVolumePolicy,
    Participation,
    Slicing,
    Urgency,
    VolumeProfile,
    record_child_execution,
    release_children,
    start_algorithm,
)
from alphalab.execution.commission import FixedCommission
from alphalab.execution.costs import (
    FREE,
    ExecutionCostModel,
    FixedHalfSpread,
    NoFee,
    NoImpact,
    NoSlippage,
    NoTax,
)
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import (
    FillPolicy,
    FillTiming,
    ImmediateFill,
    LiquidityCappedFill,
    StaticFill,
)
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.corporate_actions import Split
from alphalab.runtime.broker_routing import ChildOrderBindings, RoutingConfig, route_child_order
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting
from alphalab.runtime.run import ExecutionMode
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    START_CASH,
    backtest_config,
    context_factory,
    equity,
    registry_of,
    sized_quote,
)

ACME_RECORD = equity("ACME")
ACME = ACME_RECORD.asset_id
REGISTRY = registry_of(ACME_RECORD)
T0 = 1_700_000_000.0
MINUTE = 60.0
PRICES = tuple(Decimal("100") + Decimal(n) / Decimal("4") for n in range(12))
VOLUMES = (Decimal("400"),) * 12
HALF_SPREAD = Decimal("0.05")
COMMISSION = Decimal("1.00")
COSTS = ExecutionSimulator(
    cost_model=ExecutionCostModel(
        spread_model=FixedHalfSpread(HALF_SPREAD),
        slippage_model=NoSlippage(),
        impact_model=NoImpact(),
        commission_model=FixedCommission(COMMISSION),
        fee_model=NoFee(),
        tax_model=NoTax(),
    )
)
TWAP_4 = AlgorithmAssignment(TWAP(4, Urgency.neutral()), 8 * MINUTE, Decimal("1"))
TWAP_PLAN = ExecutionPlan(TWAP_4)


def at(minute: int) -> float:
    return T0 + minute * MINUTE


class Orders(BaseStrategy):
    """Asks for a scripted delta at chosen bars, on chosen terms."""

    def __init__(self, strategy_id: str, script: Mapping[float, tuple[Decimal, OrderTerms]]):
        self._id = strategy_id
        self._script = dict(script)

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        planned = self._script.get(event.bar.timestamp)
        if planned is None:
            return ()
        quantity, terms = planned
        return (Intent(self._id, ACME, quantity, terms=terms, timestamp=event.bar.timestamp),)


def strategies(
    scripts: Mapping[str, Mapping[float, tuple[Decimal, OrderTerms]]],
) -> StrategyRuntimeState:
    state = create_runtime()
    for strategy_id, script in scripts.items():
        state = register_strategy(state, strategy_id, Orders(strategy_id, script))
    running = {}
    for strategy_id, entry in state.strategies.items():
        configured, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"*"}), 1.2)
        running[strategy_id], _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies=running)


def bars(prices: Iterable[Decimal] = PRICES, volumes: Iterable[Decimal] = VOLUMES) -> MarketDataset:
    return MarketDataset.of(
        "ALGO-BARS",
        [
            Bar(
                ACME,
                at(n),
                p,
                p + Decimal("0.5"),
                p - Decimal("0.5"),
                p,
                v,
                None,
                None,
                TimeFrame.M1,
            )
            for n, (p, v) in enumerate(zip(prices, volumes, strict=True))
        ],
    )


def config(
    fill_policy: FillPolicy | None = None,
    timing: FillTiming = FillTiming.NEXT_EVENT,
    simulator: ExecutionSimulator = COSTS,
    strategy_ids: tuple[str, ...] = ("S",),
) -> Any:
    base = backtest_config(strategy_ids[0], fill_policy=fill_policy, simulator=simulator)
    budget = replace(
        base.pipeline.budget,
        strategy_budgets={sid: START_CASH / len(strategy_ids) for sid in strategy_ids},
    )
    return replace(
        base,
        pipeline=replace(base.pipeline, fill_timing=timing, instruments=REGISTRY, budget=budget),
    )


BUY_100 = {"S": {at(0): (Decimal("100"), MARKET)}}


def run(
    plan: ExecutionPlan = TWAP_PLAN,
    scripts: Mapping[str, Mapping[float, tuple[Decimal, OrderTerms]]] = BUY_100,
    dataset: MarketDataset | None = None,
    schedule: RunSchedule | None = None,
    **settings: Any,
) -> AlgorithmicResult:
    return BacktestEngine.run(
        config(strategy_ids=tuple(scripts), **settings),
        bars() if dataset is None else dataset,
        strategies(scripts),
        context_factory,
        schedule=RunSchedule() if schedule is None else schedule,
        execution=plan,
    )


def whole(**settings: Any) -> BacktestResult:
    """The same run, given no plan: every order filled whole, as before v4.1.1."""

    return BacktestEngine.run(config(**settings), bars(), strategies(BUY_100), context_factory)


def parent_of(result: BacktestResult) -> Any:
    (parent,) = result.state.oms.orders.orders()
    return parent


def parent_id(result: BacktestResult) -> str:
    return str(parent_of(result).order_id.value)


# --------------------------------------------------------------------------- #
# 1-3. Quantities reconcile; children are derived, in order; nothing overfills
# --------------------------------------------------------------------------- #


def test_a_twap_parent_trades_as_four_children_that_reconcile_to_it() -> None:
    result = run()
    parent = parent_of(result)
    book = result.algorithms
    assert book is not None and not book.incomplete
    algorithm = book.algorithm_for(parent_id(result))
    assert algorithm is not None and algorithm.status is AlgorithmStatus.COMPLETED

    # One OMS order, filled once in four pieces; the book, the OMS, the portfolio
    # and the execution reports agree on every unit.
    assert parent.status is OrderStatus.FILLED and parent.filled_quantity == Decimal("100")
    children = [p.child for p in algorithm.children.values()]
    assert [c.child_id for c in children] == [f"{parent_id(result)}/{n}" for n in (1, 2, 3, 4)]
    assert [c.quantity for c in children] == [Decimal("25")] * 4
    assert [c.released_at for c in children] == [at(0), at(2), at(4), at(6)]
    assert sum(f.quantity for f in book.fills) == algorithm.filled == parent.quantity
    assert result.state.portfolio.positions[ACME].quantity == Decimal("100")
    reports = [r for r in result.state.execution.history if r.order_id == parent_id(result)]
    assert [r.execution_id for r in reports] == [f.execution_id for f in book.fills]
    assert [r.status for r in reports] == [FillStatus.PARTIAL_FILL] * 3 + [FillStatus.FULL_FILL]


def test_a_child_trades_only_at_a_bar_after_its_release_and_inside_its_slice() -> None:
    book = run().algorithms
    assert book is not None
    # NEXT_EVENT: released at the start of each two-minute slice, filled at the
    # next bar -- never at the bar it was released at, never after its slice.
    assert [f.timestamp for f in book.fills] == [at(1), at(3), at(5), at(7)]
    for fill in book.fills:
        algorithm = book.algorithm_for(fill.parent_order_id)
        assert algorithm is not None
        child = algorithm.children[fill.child_id].child
        assert child.released_at < fill.timestamp
        assert child.expires_at is not None and fill.timestamp < child.expires_at


def test_children_are_derived_so_a_rerun_names_and_orders_them_identically() -> None:
    first, second = run(), run()
    assert digest_run(first).result_id == digest_run(second).result_id
    assert first.algorithms is not None and second.algorithms is not None
    assert capture_book(first.algorithms) == capture_book(second.algorithms)


def test_a_liquidity_cap_applies_to_each_child_not_to_the_parent() -> None:
    # A tenth of a bar's 400 is 40. Filled whole, the parent takes 40 at one bar
    # and the rest is withdrawn; in four children of 25, each fits under the cap.
    capped = LiquidityCappedFill(participation_rate=Decimal("0.1"))
    filled_whole = parent_of(whole(fill_policy=capped))
    assert filled_whole.status is OrderStatus.CANCELLED
    assert filled_whole.filled_quantity == Decimal("40")

    result = run(fill_policy=capped)
    book = result.algorithms
    assert book is not None
    algorithm = book.algorithm_for(parent_id(result))
    assert algorithm is not None
    assert [p.filled for p in algorithm.children.values()] == [Decimal("25")] * 4
    assert all(f.quantity <= Decimal("40") for f in book.fills)
    assert parent_of(result).status is OrderStatus.FILLED


def test_no_child_ever_executes_more_than_its_parent_left() -> None:
    thin = bars(volumes=(Decimal("30"),) * 12)
    result = run(dataset=thin, fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.5")))
    book = result.algorithms
    assert book is not None
    algorithm = book.algorithm_for(parent_id(result))
    assert algorithm is not None
    parent = parent_of(result)
    # 15 a bar against children of 25: each slice's child fills 15 at the bar
    # after it, and what it did not fill is withdrawn. The schedule tops up at
    # that bar, but the top-up's next bar is its slice's end, so it expires
    # unfilled and the next slice catches it up -- 25, 35, 45, 55.
    progress = list(algorithm.children.values())
    assert [p.child.quantity for p in progress[::2]] == [
        Decimal(q) for q in ("25", "35", "45", "55")
    ]
    assert [p.filled for p in progress[::2]] == [Decimal("15")] * 4
    assert [p.outcome for p in progress[::2]] == [OrderStatus.CANCELLED] * 4
    assert [p.filled for p in progress[1::2]] == [Decimal("0")] * 4
    assert [p.outcome for p in progress[1::2]] == [OrderStatus.EXPIRED] * 4
    # Never more at work than the parent had left.
    running = Decimal("0")
    for p in progress:
        assert p.child.quantity <= parent.quantity - running
        running += p.filled
    assert parent.filled_quantity == Decimal("60") and parent.status is OrderStatus.EXPIRED
    assert algorithm.status is AlgorithmStatus.EXPIRED


# --------------------------------------------------------------------------- #
# 4-5. No liquidity, rejection, cancellation; the reservation released once
# --------------------------------------------------------------------------- #


def _released_once(result: BacktestResult) -> None:
    allocation = result.state.allocation
    assert parent_id(result) not in allocation.reservations
    assert parent_id(result) not in allocation.contributions
    releases = [
        e
        for e in allocation.events
        if isinstance(e, AllocationReservationReleased) and e.order_id == parent_id(result)
    ]
    assert len(releases) == 1


def test_no_liquidity_fills_nothing_and_the_parent_expires_with_its_capital_released() -> None:
    result = run(dataset=bars(volumes=(Decimal("0"),) * 12), fill_policy=LiquidityCappedFill())
    parent = parent_of(result)
    assert parent.status is OrderStatus.EXPIRED and parent.filled_quantity == Decimal("0")
    assert ACME not in result.state.portfolio.positions
    assert result.state.portfolio.cash.balance("USD") == START_CASH
    _released_once(result)


def test_a_partially_executed_parent_releases_its_capital_exactly_once() -> None:
    result = run(
        dataset=bars(volumes=(Decimal("30"),) * 12),
        fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.5")),
    )
    assert parent_of(result).status is OrderStatus.EXPIRED
    _released_once(result)
    assert not result.state.allocation.reservations


def test_a_child_the_venue_rejects_stops_the_algorithm_and_rejects_the_parent() -> None:
    result = run(fill_policy=StaticFill(FillStatus.REJECTED))
    book = result.algorithms
    assert book is not None
    finished = book.finished[parent_id(result)]
    assert finished.status is OrderStatus.REJECTED
    assert finished.algorithm is not None and finished.algorithm.status is AlgorithmStatus.FAILED
    assert parent_of(result).status is OrderStatus.REJECTED
    assert ACME not in result.state.portfolio.positions
    _released_once(result)


def _declaration(order_types: frozenset[OrderType]) -> CapabilityDeclaration:
    return CapabilityDeclaration(
        adapter_id="venue",
        supported_features=frozenset({Capability.CANCEL_REPLACE}),
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
        unsupported_asset_classes=frozenset({AssetType.OPTION}),
        accounts=(
            AccountCapability(
                "ACC", frozenset({AssetType.EQUITY}), Support.UNSUPPORTED, Support.SUPPORTED
            ),
        ),
    )


def test_every_child_is_checked_against_the_venues_declaration_before_it_trades() -> None:
    offers = VenueCheck(_declaration(frozenset({OrderType.MARKET})), "ACC")
    accepted = run(ExecutionPlan(TWAP_4, capabilities=offers))
    assert parent_of(accepted).status is OrderStatus.FILLED

    limits_only = VenueCheck(_declaration(frozenset({OrderType.LIMIT})), "ACC")
    refused = run(ExecutionPlan(TWAP_4, capabilities=limits_only))
    assert refused.algorithms is not None
    finished = refused.algorithms.finished[parent_id(refused)]
    assert finished.status is OrderStatus.REJECTED
    assert "order_type" in finished.reason and "INCOMPATIBLE" in finished.reason.upper()
    assert not refused.algorithms.fills


def test_a_split_cancels_the_parent_and_its_algorithm_and_keeps_what_filled() -> None:
    split = ScheduledSplit(Split(ACME, Decimal("2")), at(4))
    result = run(schedule=RunSchedule.of(splits=[split]))
    parent = parent_of(result)
    assert result.algorithms is not None
    finished = result.algorithms.finished[parent_id(result)]
    assert parent.status is OrderStatus.CANCELLED and finished.status is OrderStatus.CANCELLED
    assert finished.algorithm is not None
    assert finished.algorithm.status is AlgorithmStatus.CANCELLED
    # The two children that filled before the split stand, in the new units.
    assert parent.filled_quantity == Decimal("50")
    assert result.state.portfolio.positions[ACME].quantity == Decimal("100")
    _released_once(result)


def test_a_parent_still_working_when_the_data_ends_is_reported_incomplete() -> None:
    long_window = ExecutionPlan(
        AlgorithmAssignment(TWAP(4, Urgency.neutral()), 40 * MINUTE, Decimal("1"))
    )
    result = run(long_window)
    book = result.algorithms
    assert book is not None
    (open_run,) = book.incomplete
    assert open_run.status is AlgorithmStatus.WORKING and open_run.remaining > Decimal("0")
    parent = parent_of(result)
    assert parent.is_open and parent.filled_quantity == open_run.filled
    # Still committed, as a resting order's capital is.
    assert parent_id(result) in result.state.allocation.reservations


# --------------------------------------------------------------------------- #
# 6. No look-ahead
# --------------------------------------------------------------------------- #


def test_prices_after_an_instant_change_nothing_the_run_did_before_it() -> None:
    base = run()
    shocked_prices = PRICES[:5] + tuple(p * 3 for p in PRICES[5:])
    shocked = run(dataset=bars(prices=shocked_prices))
    assert base.algorithms is not None and shocked.algorithms is not None
    before = [f for f in base.algorithms.fills if f.timestamp < at(5)]
    assert before == [f for f in shocked.algorithms.fills if f.timestamp < at(5)]
    assert [f for f in shocked.algorithms.fills if f.timestamp >= at(5)] != [
        f for f in base.algorithms.fills if f.timestamp >= at(5)
    ]


def test_participation_sizes_only_from_volume_already_printed() -> None:
    plan = ExecutionPlan(
        AlgorithmAssignment(
            Participation(Decimal("0.1"), None, None, IncompletePolicy.LEAVE_UNFILLED),
            8 * MINUTE,
            Decimal("1"),
        )
    )
    volumes = (Decimal("100"), Decimal("200"), Decimal("300"), Decimal("400")) * 3
    result = run(plan, dataset=bars(volumes=volumes))
    book = result.algorithms
    assert book is not None
    algorithm = book.algorithm_for(parent_id(result))
    assert algorithm is not None
    # At each bar the target is a tenth of the volume printed after the parent
    # was placed, through that bar -- the first bar's own volume predates it.
    printed = Decimal("0")
    for p, minute in zip(algorithm.children.values(), range(1, 9), strict=False):
        printed += volumes[minute]
        assert p.child.released_at == at(minute)
        assert p.child.quantity <= printed / 10
    later = run(plan, dataset=bars(volumes=volumes[:4] + (Decimal("9000"),) * 8))
    assert later.algorithms is not None
    early = [p.child for p in algorithm.children.values() if p.child.released_at < at(4)]
    same = later.algorithms.algorithm_for(parent_id(later))
    assert same is not None
    assert early == [p.child for p in same.children.values() if p.child.released_at < at(4)]


# --------------------------------------------------------------------------- #
# 7. Costs, and every report against the books
# --------------------------------------------------------------------------- #


def test_every_child_fill_pays_the_runs_costs_and_the_books_balance() -> None:
    result = run()
    book = result.algorithms
    assert book is not None
    for fill in book.fills:
        close = PRICES[int((fill.timestamp - T0) / MINUTE)]
        assert fill.price == close + HALF_SPREAD
        assert fill.commission == COMMISSION
    spent = sum(f.quantity * f.price + f.commission for f in book.fills)
    assert result.state.portfolio.cash.balance("USD") == START_CASH - spent
    assert len(result.state.trade_records) == len(book.fills)
    assert result.valuation.commission_paid == COMMISSION * len(book.fills)


def test_algorithmic_children_are_costed_against_the_same_run_filling_whole() -> None:
    plain = whole()
    sliced = run()
    assert sliced.algorithms.fills
    (fill,) = tuple(plain.state.execution.history)
    # Filled whole at the next bar, one commission; in four children, four.
    assert plain.valuation.commission_paid == COMMISSION
    assert sliced.valuation.commission_paid == COMMISSION * 4
    assert fill.fill_price == PRICES[1] + HALF_SPREAD


# --------------------------------------------------------------------------- #
# 8. Attribution
# --------------------------------------------------------------------------- #


def test_one_netted_parent_is_attributed_to_both_strategies_on_every_child_fill() -> None:
    scripts = {
        "A": {at(0): (Decimal("60"), MARKET)},
        "B": {at(0): (Decimal("40"), MARKET)},
    }
    result = run(scripts=scripts)
    parent = parent_of(result)
    assert parent.strategy_id == "" and parent.filled_quantity == Decimal("100")
    algorithm = result.algorithms.algorithm_for(parent_id(result)) if result.algorithms else None
    assert algorithm is not None
    assert {c.strategy_id: c.quantity for c in algorithm.parent.contributions} == {
        "A": Decimal("60"),
        "B": Decimal("40"),
    }
    for record in result.state.trade_records:
        assert {c.strategy_id for c in record.contributions} == {"A", "B"}
    positions = result.state.allocation.strategy_positions
    assert positions is not None
    assert positions["A"][ACME] == Decimal("60") and positions["B"][ACME] == Decimal("40")


# --------------------------------------------------------------------------- #
# 9-10. Replay parity, pauses through a payload, restoration
# --------------------------------------------------------------------------- #


def replay(plan: ExecutionPlan = TWAP_PLAN, **settings: Any) -> Any:
    return ReplayBacktest.run(
        config(**settings), bars(), strategies(BUY_100), context_factory, execution=plan
    )


def test_the_replay_works_every_child_as_the_backtest_does() -> None:
    backtest = run()
    replayed = replay()
    assert replayed.algorithms is not None
    assert capture_book(replayed.algorithms) == capture_book(backtest.algorithms)
    assert replayed.backtest.fills == backtest.fills
    assert replayed.backtest.valuation == backtest.valuation


def _fresh_objects(settings: Mapping[str, Any]) -> RunObjects:
    cfg = config(**settings)
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=cfg.pipeline.sizing_model,
            simulator=cfg.pipeline.simulator,
            strategies={"S": Orders("S", BUY_100["S"])},
            instruments=REGISTRY,
        ),
        fill_policy=cfg.fill_policy,
    )


@pytest.mark.parametrize("pause", range(1, 12))
def test_a_replay_paused_with_children_working_resumes_from_its_payload(pause: int) -> None:
    plan = ExecutionPlan(TWAP_4)
    uninterrupted = replay(plan)
    paused = ReplayBacktest.run(
        config(),
        bars(),
        strategies(BUY_100),
        context_factory,
        pause_when=lambda progress: progress.records_replayed == pause,
        execution=plan,
    )
    assert isinstance(paused, PausedReplay)
    payload = serialize(capture_replay(paused))
    restored = restore_replay(
        replay_from_primitives(deserialize(payload)),
        _fresh_objects({}),
        bars(),
        RunSchedule(),
        plan,
    )
    finished = ReplayBacktest.resume(restored, context_factory)
    assert digest_run(finished.backtest).result_id == digest_run(uninterrupted.backtest).result_id
    assert finished.algorithms is not None and uninterrupted.algorithms is not None
    assert capture_book(finished.algorithms) == capture_book(uninterrupted.algorithms)


def test_a_payload_resumed_with_another_plan_or_none_is_refused() -> None:
    plan = ExecutionPlan(TWAP_4)
    paused = ReplayBacktest.run(
        config(),
        bars(),
        strategies(BUY_100),
        context_factory,
        pause_when=lambda progress: progress.records_replayed == 3,
        execution=plan,
    )
    assert isinstance(paused, PausedReplay)
    snapshot = replay_from_primitives(deserialize(serialize(capture_replay(paused))))
    other = ExecutionPlan(AlgorithmAssignment(TWAP(2, Urgency.neutral()), 8 * MINUTE, Decimal("1")))
    with pytest.raises(StateDecodeError, match="another way"):
        restore_replay(snapshot, _fresh_objects({}), bars(), RunSchedule(), other)
    with pytest.raises(StateDecodeError, match="no execution plan was supplied"):
        restore_replay(snapshot, _fresh_objects({}), bars(), RunSchedule())


def test_a_book_round_trips_and_an_altered_one_is_refused() -> None:
    plan = ExecutionPlan(TWAP_4)
    book = run(plan).algorithms
    assert book is not None
    payload = deserialize(serialize(capture_book(book)))
    assert capture_book(book_from_primitives(payload, plan)) == capture_book(book)
    finished = payload["finished"][0]["algorithm"]
    finished["parent"]["quantity"] = "101"
    with pytest.raises(ExecutionPlanError, match="altered"):
        book_from_primitives(payload, plan)


# --------------------------------------------------------------------------- #
# Every algorithm the plan admits, worked through a backtest
# --------------------------------------------------------------------------- #


PROFILE = VolumeProfile(
    tuple(IntervalVolume(at(2 * n), at(2 * n + 2), Decimal(v)) for n, v in enumerate((1, 3, 3, 1))),
    "test profile: front and back light",
)


@pytest.mark.parametrize(
    ("algorithm", "children"),
    [
        (TWAP(4, Urgency.neutral()), ["25", "25", "25", "25"]),
        (VWAP(PROFILE, MissingVolumePolicy.REFUSE, Urgency.neutral()), ["13", "38", "37", "12"]),
        (Slicing(Decimal("30"), None), ["30", "30", "30", "10"]),
        (Iceberg(Decimal("20")), ["20", "20", "20", "20", "20"]),
    ],
    ids=["twap", "vwap", "slicing", "iceberg"],
)
def test_each_schedule_and_slicing_algorithm_works_its_parent_through_the_backtest(
    algorithm: Any, children: list[str]
) -> None:
    result = run(ExecutionPlan(AlgorithmAssignment(algorithm, 8 * MINUTE, Decimal("1"))))
    book = result.algorithms
    assert book is not None
    worked = book.algorithm_for(parent_id(result))
    assert worked is not None and worked.status is AlgorithmStatus.COMPLETED
    assert [p.child.quantity for p in worked.children.values()] == [Decimal(c) for c in children]
    assert parent_of(result).status is OrderStatus.FILLED


def test_a_limit_parents_children_rest_at_its_limit_and_fill_only_when_crossed() -> None:
    limit = Decimal("100.30")
    scripts = {
        "S": {
            at(0): (
                Decimal("100"),
                OrderTerms(OrderType.LIMIT, limit, time_in_force=TimeInForce.GTC),
            )
        }
    }
    result = run(scripts=scripts)
    book = result.algorithms
    assert book is not None
    algorithm = book.algorithm_for(parent_id(result))
    assert algorithm is not None
    assert all(p.child.order_type is OrderType.LIMIT for p in algorithm.children.values())
    assert all(p.child.limit_price == limit for p in algorithm.children.values())
    # A bar whose low reaches the limit fills the child as a maker: at the
    # limit, or at the bar's open when it opened through it. Never above it.
    for fill in book.fills:
        opened = PRICES[int((fill.timestamp - T0) / MINUTE)]
        assert opened - Decimal("0.5") <= limit
        assert fill.price == min(opened, limit) and fill.commission == COMMISSION
    assert book.fills


# --------------------------------------------------------------------------- #
# 11. What is not supported fails explicitly
# --------------------------------------------------------------------------- #


def test_participation_that_trades_after_its_window_is_refused_by_the_plan() -> None:
    with pytest.raises(ExecutionPlanError, match="COMPLETE_AT_END"):
        AlgorithmAssignment(
            Participation(Decimal("0.1"), None, None, IncompletePolicy.COMPLETE_AT_END),
            MINUTE,
            Decimal("1"),
        )


@pytest.mark.parametrize(
    "terms",
    [
        OrderTerms(OrderType.STOP, stop_price=Decimal("90")),
        OrderTerms(OrderType.MARKET, time_in_force=TimeInForce.IOC),
        OrderTerms.at_open(),
    ],
    ids=["stop", "immediate", "auction"],
)
def test_terms_an_algorithm_cannot_work_are_refused_before_an_order_exists(
    terms: OrderTerms,
) -> None:
    result = run(scripts={"S": {at(0): (Decimal("100"), terms)}})
    assert not tuple(result.state.oms.orders.orders())
    assert result.algorithms is not None and not result.algorithms.fills
    assert result.state.portfolio.cash.balance("USD") == START_CASH


def test_a_parent_no_algorithm_can_work_is_rejected_not_filled_whole() -> None:
    lots = ExecutionPlan(AlgorithmAssignment(TWAP(4, Urgency.neutral()), 8 * MINUTE, Decimal("30")))
    result = run(lots)
    assert result.algorithms is not None
    finished = result.algorithms.finished[parent_id(result)]
    assert finished.status is OrderStatus.REJECTED and finished.algorithm is None
    assert "whole number" in finished.reason
    assert ACME not in result.state.portfolio.positions

    elsewhere = ExecutionPlan(by_asset=(("ELSEWHERE", TWAP_4),))
    unassigned = run(elsewhere)
    assert unassigned.algorithms is not None
    assert "No execution algorithm is assigned" in (
        unassigned.algorithms.finished[parent_id(unassigned)].reason
    )


def test_an_algorithmic_routing_without_a_plan_or_outside_history_is_refused() -> None:
    held = replace(
        config(), pipeline=replace(config().pipeline, routing=ExecutionRouting.ALGORITHMIC)
    )
    with pytest.raises(ExecutionPlanError, match="no execution plan"):
        BacktestEngine.run(held, bars(), strategies(BUY_100), context_factory)
    with pytest.raises(RuntimeValidationError, match="cannot route ALGORITHMIC"):
        replace(held, mode=ExecutionMode.LIVE)


# --------------------------------------------------------------------------- #
# 12. Runs given no plan are the runs they were
# --------------------------------------------------------------------------- #


def test_a_run_given_no_plan_is_untouched() -> None:
    plain = whole()
    assert type(plain) is BacktestResult
    assert plain.config.pipeline.routing is ExecutionRouting.SIMULATED
    assert parent_of(plain).filled_quantity == Decimal("100")
    assert len(plain.state.execution.history) == 1
    assert plain.config.execution_assumptions.simulated


# --------------------------------------------------------------------------- #
# The live route works the same children
# --------------------------------------------------------------------------- #


def test_the_backtests_children_are_the_ones_the_live_route_sends() -> None:
    """Child for child, the backtest releases what the live route sends a venue.

    The algorithm is driven by hand at the bars the backtest saw, each child sent
    to a ``PaperBroker`` with ``route_child_order``, as a live session sends it.
    The venue fills each market child in full, as ``ImmediateFill`` does, so both
    runs release the same children, with the same ids and quantities, at the same
    instants. The prices differ by design: the paper venue sees no market data and
    fills at the parent's reference price, where the backtest fills at the bar.
    """

    result = run(fill_policy=ImmediateFill(), simulator=ExecutionSimulator(cost_model=FREE))
    book = result.algorithms
    assert book is not None
    worked = book.algorithm_for(parent_id(result))
    assert worked is not None
    final = parent_of(result)
    parent = replace(
        final,
        status=OrderStatus.ACCEPTED,
        filled_quantity=Decimal("0"),
        remaining_quantity=final.quantity,
    )

    broker = PaperBroker(FREE)
    venue = BrokerEngine.initialize("PAPER", START_CASH, "USD")
    venue, _ = broker.connect(venue, T0)
    bindings = ChildOrderBindings()
    algorithm = start_algorithm(worked.parent, worked.algorithm, worked.terms)
    sent = []
    for minute in range(12):
        now = at(minute)
        algorithm, released = release_children(algorithm, now)
        for child in released:
            routed = route_child_order(
                venue,
                broker,
                parent,
                child,
                now,
                mapping=ExternalOrderMap(),
                children=bindings,
                config=RoutingConfig(venue="PAPER", currency="USD", order_type=OrderType.MARKET),
                capability=None,
            )
            assert routed.decision.routed, routed.decision.reason
            venue, bindings = routed.broker_state, routed.children
            sent.append(child)
            algorithm = record_child_execution(
                algorithm, child.child_id, f"PX-{child.sequence}", child.quantity, now
            )
            parent = replace(parent, remaining_quantity=parent.remaining_quantity - child.quantity)
    assert [(c.child_id, c.quantity, c.released_at) for c in sent] == [
        (p.child.child_id, p.child.quantity, p.child.released_at) for p in worked.children.values()
    ]
    assert algorithm.status is AlgorithmStatus.COMPLETED


def test_terms_are_the_plans_window_from_the_instant_the_parent_was_placed() -> None:
    result = run()
    assert result.algorithms is not None
    worked = result.algorithms.algorithm_for(parent_id(result))
    assert worked is not None
    assert worked.terms == AlgorithmTerms(at(0), at(8), Decimal("1"), OrderType.MARKET, None)


# --------------------------------------------------------------------------- #
# The pipeline's own guards on a child, and the plan's
# --------------------------------------------------------------------------- #


def _held() -> tuple[BacktestResult, Any, Any]:
    """A run whose data ends with a child working: the parent, and that child."""

    window = ExecutionPlan(
        AlgorithmAssignment(TWAP(4, Urgency.neutral()), 40 * MINUTE, Decimal("1"))
    )
    result = run(window, dataset=bars(PRICES[:1], VOLUMES[:1]))
    assert result.algorithms is not None
    (algorithm,) = result.algorithms.incomplete
    (child,) = algorithm.working_children
    return result, algorithm, child


def _event(minute: int, asset: str = ACME) -> Any:
    price = Decimal("101")
    bar = Bar(
        asset, at(minute), price, price, price, price, Decimal("400"), None, None, TimeFrame.M1
    )
    return BarClosed(event_id=f"E-{minute}", timestamp=at(minute), bar=bar)


def test_a_child_cannot_work_more_than_its_parent_has_left() -> None:
    result, _, child = _held()
    remaining = parent_of(result).remaining_quantity
    with pytest.raises(RuntimeValidationError, match="short of filled"):
        ExecutionPipeline.work_child(
            result.state, child, remaining + 1, _event(9), ImmediateFill(), arriving=False
        )
    with pytest.raises(RuntimeValidationError, match="short of filled"):
        ExecutionPipeline.work_child(
            result.state, child, Decimal("0"), _event(9), ImmediateFill(), arriving=False
        )


def test_a_child_is_worked_only_at_an_event_of_its_own_asset() -> None:
    result, _, child = _held()
    with pytest.raises(RuntimeValidationError, match="the event prices"):
        ExecutionPipeline.work_child(
            result.state, child, child.quantity, _event(9, "OTHER"), ImmediateFill(), arriving=False
        )


def test_a_child_names_a_parent_the_run_holds_and_routes_algorithmically() -> None:
    result, _, child = _held()
    stranger = replace(child, parent_order_id="00000000-0000-4000-8000-000000000000")
    with pytest.raises(RuntimeValidationError, match="unknown parent"):
        ExecutionPipeline.work_child(
            result.state, stranger, child.quantity, _event(9), ImmediateFill(), arriving=False
        )
    simulated = replace(
        result.state,
        config=replace(result.state.config, routing=ExecutionRouting.SIMULATED),
    )
    with pytest.raises(RuntimeValidationError, match="only under ALGORITHMIC"):
        ExecutionPipeline.work_child(
            simulated, child, child.quantity, _event(9), ImmediateFill(), arriving=False
        )


def test_a_child_worked_by_hand_settles_on_its_parent_as_the_driver_does() -> None:
    result, _, child = _held()
    before = parent_of(result).filled_quantity
    turn = ExecutionPipeline.work_child(
        result.state, child, child.quantity, _event(9), ImmediateFill(), arriving=False
    )
    (report,) = turn.reports
    assert report.order_id == child.parent_order_id and turn.filled == child.quantity
    assert turn.outcome is OrderStatus.FILLED
    (after,) = turn.state.oms.orders.orders()
    assert after.filled_quantity == before + child.quantity


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: AlgorithmAssignment(TWAP(2, Urgency.neutral()), 0.0, Decimal("1")), "no time"),
        (
            lambda: AlgorithmAssignment(TWAP(2, Urgency.neutral()), float("nan"), Decimal("1")),
            "no time",
        ),
        (lambda: AlgorithmAssignment(TWAP(2, Urgency.neutral()), MINUTE, Decimal("0")), "unit"),
        (lambda: ExecutionPlan(), "assigns no algorithm"),
        (lambda: ExecutionPlan(by_asset=((ACME, TWAP_4), (ACME, TWAP_4))), "repeats"),
        (lambda: VenueCheck(_declaration(frozenset({OrderType.MARKET})), " "), "no account"),
    ],
    ids=["no-window", "nan-window", "no-increment", "empty", "repeated-asset", "no-account"],
)
def test_a_plan_that_cannot_work_anything_is_refused(build: Any, message: str) -> None:
    with pytest.raises(ExecutionPlanError, match=message):
        build()


def test_a_plans_identity_is_its_content() -> None:
    by_asset = ExecutionPlan(by_asset=((ACME, TWAP_4), ("B", TWAP_4)))
    reordered = ExecutionPlan(by_asset=(("B", TWAP_4), (ACME, TWAP_4)))
    assert by_asset.plan_id == reordered.plan_id
    assert by_asset.assignment_for(ACME) is TWAP_4 and by_asset.assignment_for("C") is None
    assert ExecutionPlan(TWAP_4).plan_id != by_asset.plan_id
    slower = AlgorithmAssignment(TWAP(4, Urgency.neutral()), 9 * MINUTE, Decimal("1"))
    assert ExecutionPlan(slower).plan_id != ExecutionPlan(TWAP_4).plan_id
    checked = ExecutionPlan(
        TWAP_4, capabilities=VenueCheck(_declaration(frozenset({OrderType.MARKET})), "ACC")
    )
    assert checked.plan_id != ExecutionPlan(TWAP_4).plan_id


# --------------------------------------------------------------------------- #
# Fill timing: the run's own rule, applied to every child
# --------------------------------------------------------------------------- #


def test_under_same_event_a_child_trades_at_the_bar_it_was_released_at() -> None:
    result = run(timing=FillTiming.SAME_EVENT)
    book = result.algorithms
    assert book is not None
    # Released and filled at each slice's first bar; never filled whole.
    assert [f.timestamp for f in book.fills] == [at(0), at(2), at(4), at(6)]
    assert [f.quantity for f in book.fills] == [Decimal("25")] * 4
    assert [f.price for f in book.fills] == [PRICES[m] + HALF_SPREAD for m in (0, 2, 4, 6)]
    assert result.config.execution_assumptions.optimistic


def test_a_child_never_trades_at_the_instant_it_was_released_under_next_event() -> None:
    inputs: list[Any] = []
    for n, p in enumerate(PRICES):
        inputs.append(Bar(ACME, at(n), p, p, p, p, Decimal("400"), None, None, TimeFrame.M1))
        # A quote at the same instant, after the bar: the same moment, not a later one.
        inputs.append(sized_quote(ACME, at(n), p, Decimal("400")))
    result = run(dataset=MarketDataset.of("ALGO-TWO-A-MINUTE", inputs))
    book = result.algorithms
    assert book is not None and book.fills
    for fill in book.fills:
        algorithm = book.algorithm_for(fill.parent_order_id)
        assert algorithm is not None
        assert algorithm.children[fill.child_id].child.released_at < fill.timestamp
