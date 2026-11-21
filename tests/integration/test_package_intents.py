"""A package trades its legs together, all or none, at one instant (ADR-0062, GAP-26).

Every figure here is calculated by hand from the quotes below: two assets, A and
B, quoted at the same instants, and a strategy that asks at B's first quote --
once both legs are priced -- for ten packages of one A bought and one B sold.

* **One order, one instant.** Under ``SAME_EVENT`` both legs fill at the close of
  the instant the package was decided in, A at 100 and B at 50; under
  ``NEXT_EVENT`` (and ``NEXT_OPEN``, a quote having no open) at the next
  instant, 101 and 49. With B unquoted at the second instant the package waits
  for the third, 102 and 48.
* **All or none.** A cap letting B fill only the 4 it shows fills neither leg,
  and both are withdrawn, holding nothing. Under a resting remainder of one
  event the package keeps one more turn, and fills there.
* **Whole.** Risk refusing one leg refuses the package; packages net across
  strategies, ten from six and four, and never with an order for one leg; a
  cancel naming a leg is refused.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from alphalab.backtesting import (
    AlgorithmAssignment,
    BacktestEngine,
    ExecutionPlan,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    ReplayResult,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import MARKET, OrderTerms
from alphalab.core.enums import OrderStatus
from alphalab.execution.algorithms import TWAP, Urgency
from alphalab.execution.policy import FillTiming, LiquidityCappedFill, RestRemainder
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, Intent, IntentKind, Leg, OrderEvent, PackageRefused
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    backtest_config,
    context_factory,
    permissive_risk_limits,
    sized_quote,
)

D = Decimal
S = str(UUID(int=0x201))
OTHER = str(UUID(int=0x204))
A = str(UUID(int=0x202, version=4))
B = str(UUID(int=0x203, version=4))
PAIR = (Leg(A, D("1")), Leg(B, D("-1")))


class Pair(BaseStrategy):
    """Asks for ``target`` packages at B's quote at 1.0 -- a decision from the event alone."""

    def __init__(
        self,
        strategy_id: str = S,
        target: str = "10",
        legs: tuple[Leg, ...] = PAIR,
        terms: OrderTerms | None = None,
        also: Iterable[Intent] = (),
        cancel_at: float | None = None,
    ) -> None:
        self._id = strategy_id
        self._target = D(target)
        self._legs = legs
        self._terms = terms
        self._also = tuple(also)
        self._cancel_at = cancel_at
        self.heard: list[Any] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote = event.quote
        if (
            self._cancel_at is not None
            and quote.timestamp == self._cancel_at
            and quote.asset_id == A
        ):
            return (Intent(self._id, A, D("0"), timestamp=quote.timestamp, kind=IntentKind.CANCEL),)
        if quote.timestamp != 1.0 or quote.asset_id != B:
            return ()
        package = Intent(
            self._id,
            "PAIR",
            self._target,
            terms=MARKET if self._terms is None else self._terms,
            timestamp=quote.timestamp,
            kind=IntentKind.PACKAGE,
            legs=self._legs,
        )
        return (package, *self._also)

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        self.heard.append(event)
        return ()

    def on_order(self, context: StrategyContext, event: OrderEvent) -> Iterable[Intent]:
        self.heard.append(event)
        return ()


QUOTED = (
    (1.0, A, "100", "1000"),
    (1.0, B, "50", "1000"),
    (2.0, A, "101", "1000"),
    (2.0, B, "49", "1000"),
    (3.0, A, "102", "1000"),
    (3.0, B, "48", "1000"),
)


def _data(rows: Iterable[tuple[float, str, str, str]]) -> MarketDataset:
    quotes = [sized_quote(asset, at, D(mid), D(size)) for at, asset, mid, size in rows]
    return MarketDataset.of("PAIRS", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))


def _strategies(*strategies: Pair) -> StrategyRuntimeState:
    state = create_runtime()
    for strategy in strategies:
        state = register_strategy(state, strategy._id, strategy)
    running = {}
    for strategy_id, entry in state.strategies.items():
        configured, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"*"}), 1.2)
        running[strategy_id], _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies=running)


def _config(timing: FillTiming = FillTiming.SAME_EVENT, **kw: Any) -> Any:
    config = backtest_config(S, **kw)
    budget = config.pipeline.budget
    half = budget.global_capital / 2
    return replace(
        config,
        pipeline=replace(
            config.pipeline,
            fill_timing=timing,
            budget=replace(budget, strategy_budgets={S: half, OTHER: half}),
        ),
    )


def _run(
    *strategies: Pair,
    rows: Iterable[tuple[float, str, str, str]] = QUOTED,
    timing: FillTiming = FillTiming.SAME_EVENT,
    remainder: RestRemainder | None = None,
    execution: ExecutionPlan | None = None,
    **kw: Any,
) -> Any:
    config = _config(timing, **kw)
    config = replace(config, pipeline=replace(config.pipeline, remainder=remainder))
    return BacktestEngine.run(
        config,
        _data(rows),
        _strategies(*(strategies or (Pair(),))),
        context_factory,
        execution=execution,
    )


def _fills(result: Any) -> list[tuple[str, str, Decimal, Decimal, float]]:
    return [(f.asset_id, f.side.value, f.quantity, f.price, f.filled_at) for f in result.fills]


def _refusals(result: Any) -> list[str]:
    return [e.reason for e in result.state.strategy.events if isinstance(e, PackageRefused)]


# --------------------------------------------------------------------------- #
# One order, one instant
# --------------------------------------------------------------------------- #


def test_a_pair_trades_as_one_order_at_one_instant() -> None:
    result = _run()
    assert _fills(result) == [
        (A, "buy", D("10"), D("100"), 1.0),
        (B, "sell", D("10"), D("50"), 1.0),
    ]
    legs = result.state.oms.orders.orders()
    assert [o.status for o in legs] == [OrderStatus.FILLED, OrderStatus.FILLED]
    (package,) = {o.metadata["package_id"] for o in legs}
    assert package == str(legs[0].order_id.value)
    assert {o.metadata["package_label"] for o in legs} == {"PAIR"}
    assert {o.metadata["package_legs"] for o in legs} == {
        ",".join(str(o.order_id.value) for o in legs)
    }
    assert not result.state.allocation.reservations


@pytest.mark.parametrize("timing", [FillTiming.NEXT_EVENT, FillTiming.NEXT_OPEN])
def test_the_fill_timing_applies_to_the_package(timing: FillTiming) -> None:
    assert _fills(_run(timing=timing)) == [
        (A, "buy", D("10"), D("101"), 2.0),
        (B, "sell", D("10"), D("49"), 2.0),
    ]


def test_a_package_waits_for_an_instant_that_prices_every_leg() -> None:
    rows = [row for row in QUOTED if row[:2] != (2.0, B)]
    assert _fills(_run(rows=rows, timing=FillTiming.NEXT_EVENT)) == [
        (A, "buy", D("10"), D("102"), 3.0),
        (B, "sell", D("10"), D("48"), 3.0),
    ]


# --------------------------------------------------------------------------- #
# All or none
# --------------------------------------------------------------------------- #

THIN_B = tuple(
    (at, asset, mid, "4" if asset == B and at == 1.0 else size) for at, asset, mid, size in QUOTED
)


def test_a_cap_that_would_fill_one_leg_in_part_fills_neither() -> None:
    result = _run(rows=THIN_B, fill_policy=LiquidityCappedFill(D("1")))
    assert _fills(result) == []
    assert [o.status for o in result.state.oms.orders.orders()] == [
        OrderStatus.CANCELLED,
        OrderStatus.CANCELLED,
    ]
    assert not result.state.allocation.reservations
    assert result.valuation.cash == result.config.pipeline.starting_cash


def test_under_a_resting_remainder_the_package_keeps_its_turns() -> None:
    result = _run(
        rows=THIN_B, fill_policy=LiquidityCappedFill(D("1")), remainder=RestRemainder(events=1)
    )
    assert _fills(result) == [
        (A, "buy", D("10"), D("101"), 2.0),
        (B, "sell", D("10"), D("49"), 2.0),
    ]
    thin_twice = tuple(
        (at, asset, mid, "4" if asset == B and at < 3.0 else size)
        for at, asset, mid, size in QUOTED
    )
    spent = _run(
        rows=thin_twice, fill_policy=LiquidityCappedFill(D("1")), remainder=RestRemainder(events=1)
    )
    assert _fills(spent) == []  # its one more turn, at 2.0, fills nothing either
    assert {o.status for o in spent.state.oms.orders.orders()} == {OrderStatus.CANCELLED}


# --------------------------------------------------------------------------- #
# Whole
# --------------------------------------------------------------------------- #


def test_risk_refusing_one_leg_refuses_the_package() -> None:
    lopsided = (Leg(A, D("1")), Leg(B, D("-2")))
    result = _run(Pair(legs=lopsided), risk_limits=permissive_risk_limits(D("15")))
    assert result.state.oms.orders.orders() == ()
    (reason,) = _refusals(result)
    assert reason.startswith(f"Risk refused leg {B}")
    assert not result.state.allocation.reservations


def test_packages_net_across_strategies_and_never_with_an_order_for_one_leg() -> None:
    single = Intent(OTHER, A, D("3"), timestamp=1.0)
    result = _run(Pair(S, "6"), Pair(OTHER, "4", also=(single,)))
    by_leg = [o for o in result.state.oms.orders.orders() if "package_id" in o.metadata]
    alone = [o for o in result.state.oms.orders.orders() if "package_id" not in o.metadata]
    assert [(o.asset_id, o.quantity) for o in by_leg] == [(A, D("10")), (B, D("10"))]
    assert [(o.asset_id, o.quantity) for o in alone] == [(A, D("3"))]
    # Each leg's fill is the strategies' by their packages: six and four of ten.
    reports = {r.execution_id: r.order_id for r in result.state.execution.history}
    legs = {str(o.order_id.value): o.asset_id for o in by_leg}
    shares = {
        (c.strategy_id, legs[reports[record.trade_id]]): c.quantity
        for record in result.state.trade_records
        if reports[record.trade_id] in legs
        for c in record.contributions
    }
    assert shares == {(S, A): D("6"), (OTHER, A): D("4"), (S, B): D("-6"), (OTHER, B): D("-4")}


def test_a_cancel_naming_a_leg_is_refused_and_the_package_trades() -> None:
    result = _run(Pair(cancel_at=2.0), timing=FillTiming.NEXT_EVENT)
    refused = [e for e in result.state.strategy.events if type(e).__name__ == "CancelRefused"]
    assert len(refused) == 1 and "leg of a package" in refused[0].reason
    assert [p for _, _, _, p, _ in _fills(result)] == [D("101"), D("49")]


@pytest.mark.parametrize(
    ("strategy", "kw", "reason"),
    [
        (Pair(legs=(Leg(A, D("1")),)), {}, "two legs or more"),
        (Pair(legs=(Leg(A, D("1")), Leg(A, D("2")))), {}, "appears twice"),
        (Pair(legs=(Leg(A, D("1")), Leg(B, D("0")))), {}, "is not zero"),
        (Pair(legs=(Leg(A, D("1")), Leg(str(UUID(int=0x2FF, version=4)), D("1")))), {}, "no price"),
        (Pair(terms=OrderTerms.limit(D("99"))), {}, "a market order"),
        (Pair(target="3", legs=(Leg(A, D("1")), Leg(B, D("0.5")))), {"whole": True}, "whole units"),
    ],
    ids=["one-leg", "twice", "zero-ratio", "unpriced", "limit", "off-units"],
)
def test_a_package_that_cannot_be_traded_is_refused_whole(
    strategy: Pair, kw: dict[str, bool], reason: str
) -> None:
    config = _config()
    if kw.get("whole"):
        constraints = replace(
            config.pipeline.allocation_constraints, enforce_integer_quantities=True
        )
        config = replace(
            config, pipeline=replace(config.pipeline, allocation_constraints=constraints)
        )
    result = BacktestEngine.run(config, _data(QUOTED), _strategies(strategy), context_factory)
    (refusal,) = _refusals(result)
    assert reason in refusal
    assert result.state.oms.orders.orders() == ()
    assert not result.state.allocation.reservations


def test_a_package_under_an_execution_plan_is_refused() -> None:
    plan = ExecutionPlan(AlgorithmAssignment(TWAP(1, Urgency.neutral()), 60.0, D("1")))
    (refusal,) = _refusals(_run(execution=plan))
    assert "routes ALGORITHMIC" in refusal


# --------------------------------------------------------------------------- #
# What the strategy hears, and the replay
# --------------------------------------------------------------------------- #


def test_each_legs_fill_and_order_event_carries_its_package() -> None:
    strategy = Pair()
    result = _run(strategy)
    package = result.state.oms.orders.orders()[0].metadata["package_id"]
    fills = [e for e in strategy.heard if isinstance(e, FillEvent)]
    orders = [e for e in strategy.heard if isinstance(e, OrderEvent)]
    assert [(e.instrument, e.attributed_quantity) for e in fills] == [(A, D("10")), (B, D("-10"))]
    assert {*(e.package_id for e in fills), *(e.package_id for e in orders)} == {package}


def test_a_replay_paused_with_a_package_working_resumes_to_the_same_result() -> None:
    config = _config(FillTiming.NEXT_EVENT)

    def replay(pause: int | None) -> ReplayResult | PausedReplay:
        return ReplayBacktest.run(
            config,
            _data(QUOTED),
            _strategies(Pair()),
            context_factory,
            pause_when=lambda progress: pause is not None and progress.records_replayed == pause,
        )

    whole = replay(None)
    assert isinstance(whole, ReplayResult)
    for pause in range(1, len(QUOTED)):
        paused = replay(pause)
        assert isinstance(paused, PausedReplay)
        pipeline = paused.run.config.pipeline
        objects = RunObjects(
            RuntimeObjects(
                pipeline.sizing_model, pipeline.simulator, {S: Pair()}, pipeline.instruments
            ),
            paused.run.config.fill_policy,
        )
        payload = dict(deserialize(serialize(capture_replay(paused))))
        restored = restore_replay(replay_from_primitives(payload), objects, _data(QUOTED))
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert isinstance(resumed, ReplayResult)
        assert digest_run(resumed.backtest).result_id == digest_run(whole.backtest).result_id
        assert _fills(resumed.backtest) == _fills(whole.backtest)
