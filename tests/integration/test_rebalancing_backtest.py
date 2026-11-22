"""A construction rule backtested as a strategy, its rebalances reconciled to its fills (ADR-0063).

WS-12's integration line: a rebalancing backtest with a construction at each
period, reconciled to the run's fills.

* **The run.** Three equities quoted daily for thirteen months. A minimum-variance
  model estimates its covariance from the run's own history, bounded at each
  slice, and a calendar trigger rebalances at the first slice of each month in
  UTC. Capital is the account's equity, so it compounds; the model holds three
  percent in cash, and the strategy sells first, so a cash account pays for its
  purchases from its sales.
* **Reconciled.** Each rebalance's targets are, by the next one, exactly the
  strategy's own book -- its share of every fill -- and at the end the account
  holds what the strategy does.
* **Certified answers only.** A model whose problem cannot be met records each
  rebalance as ``INFEASIBLE`` and trades nothing.
* **Identity and state.** Two runs give one result; a run paused anywhere and
  resumed from its captured state gives the same result and the same records.
* **Its book is its own** (S-10's piece). Beside a second strategy trading one
  of its assets in the same account, the strategy's book is its own share --
  the position allocation measures its targets against -- and its rebalances
  reconcile to it, while the account holds both.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.engine import AllocationEngine
from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.backtesting import BacktestEngine, BacktestResult, PausedReplay, ReplayBacktest
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import MARKET
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    ExposureRange,
    MinimumVariance,
    SolverSettings,
    WeightBounds,
)
from alphalab.rebalancing import (
    AccountEquity,
    CalendarTrigger,
    CapitalRule,
    FixedCapital,
    Period,
    RebalanceExecution,
    RebalanceView,
    RebalancingStrategy,
)
from alphalab.runtime.run import RunConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    backtest_config,
    context_factory,
    equity,
    quote,
    registry_of,
    running_strategy_state,
)

STRATEGY = "REBALANCER"
RECORDS = (equity("RBA"), equity("RBB"), equity("RBC"))
ASSETS = tuple(sorted(record.asset_id for record in RECORDS))
DAY = 86400.0
#: 2025-01-02 12:00:00 UTC, then one quote per asset each day.
START = 1735819200.0
DAYS = 400
SETTINGS = SolverSettings(1e-9, 1e-9, 500)


def _mids() -> dict[str, list[Decimal]]:
    """Each asset's daily mid: a random walk drawn with random() alone, in cents."""

    rng = random.Random(20261122)
    volatility = {ASSETS[0]: 0.010, ASSETS[1]: 0.018, ASSETS[2]: 0.026}
    mids: dict[str, list[Decimal]] = {}
    for asset in ASSETS:
        price = 100.0
        path = []
        for _ in range(DAYS):
            price *= 1.0 + volatility[asset] * 2.0 * (rng.random() - 0.5)
            path.append(Decimal(repr(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
        mids[asset] = path
    return mids


MIDS = _mids()
DATASET = MarketDataset.of(
    "DS-REBALANCING",
    [quote(asset, START + day * DAY, MIDS[asset][day]) for day in range(DAYS) for asset in ASSETS],
)


@dataclass
class SampleMinimumVariance:
    """Minimum variance over the sample covariance of the last ``lookback`` daily returns.

    It reads only the history the slice's context exposes, bounded at the
    instant, and declines -- states no problem -- until it holds enough.
    ``views`` keeps what it was shown, for the test to read.
    """

    lookback: int
    cap: float
    views: list[RebalanceView] = field(default_factory=list)

    def problem(self, view: RebalanceView) -> ConstructionProblem | None:
        self.views.append(view)
        series = {
            asset: [
                (q.bid + q.ask) / 2
                for q in view.context.history.quotes(asset, limit=self.lookback + 1)
            ]
            for asset in view.assets
        }
        if any(len(prices) < self.lookback + 1 for prices in series.values()):
            return None
        returns = {
            asset: [float(b / a - 1) for a, b in pairwise(prices)]
            for asset, prices in series.items()
        }
        means = {asset: sum(values) / len(values) for asset, values in returns.items()}
        rows = [
            [
                sum(
                    (x - means[a]) * (y - means[b])
                    for x, y in zip(returns[a], returns[b], strict=True)
                )
                / (self.lookback - 1)
                for b in view.assets
            ]
            for a in view.assets
        ]
        covariance = CovarianceMatrix.from_rows(
            view.assets,
            rows,
            currency="USD",
            period="1D",
            source="sample",
            observations=self.lookback,
        )
        # Three percent held in cash: targets sized at a slice's prices fill at the
        # next quote's, and a day's moves here are under three percent.
        constraints = ConstraintSet(ExposureRange.exactly(0.97), WeightBounds(0.0, self.cap))
        return ConstructionProblem(covariance, MinimumVariance(), constraints, SETTINGS)


def _strategy(
    model: SampleMinimumVariance, capital: CapitalRule | None = None
) -> RebalancingStrategy:
    return RebalancingStrategy(
        STRATEGY,
        assets=ASSETS,
        model=model,
        triggers=(CalendarTrigger(Period.MONTH, zone="UTC"),),
        capital=AccountEquity("USD") if capital is None else capital,
        quantum=Decimal("1"),
        terms=MARKET,
        execution=RebalanceExecution.SELLS_FIRST,
    )


def _config() -> RunConfig:
    config = backtest_config(STRATEGY)
    return replace(config, pipeline=replace(config.pipeline, instruments=registry_of(*RECORDS)))


def _runtime(strategy: RebalancingStrategy) -> Any:
    return running_strategy_state(STRATEGY, strategy, frozenset({"slices", "fills"}))


def _run(model: SampleMinimumVariance) -> tuple[BacktestResult, RebalancingStrategy]:
    strategy = _strategy(model)
    result = BacktestEngine.run(_config(), DATASET, _runtime(strategy), context_factory)
    return result, strategy


@pytest.fixture(scope="module")
def ran() -> tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance]:
    model = SampleMinimumVariance(lookback=40, cap=0.6)
    result, strategy = _run(model)
    return result, strategy, model


def _held(targets: Any) -> dict[str, Decimal]:
    return {asset: quantity for asset, quantity in targets.items() if quantity}


# --------------------------------------------------------------------------- #
# The run, reconciled
# --------------------------------------------------------------------------- #


def test_it_rebalances_at_the_first_slice_of_each_month_once_the_model_can_estimate(
    ran: tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance],
) -> None:
    _, strategy, model = ran
    records = strategy.records
    # Fourteen month starts from January 2025 to February 2026; the model
    # declines the first two, holding less than forty returns.
    assert len(model.views) == 14 and len(records) == 12
    assert [record.sequence for record in records] == list(range(1, 13))
    for record in records:
        assert record.status is ConstructionStatus.OPTIMAL and record.traded
        assert record.fired == ("trigger=calendar period=month zone='UTC'",)
        assert set(record.targets) == set(ASSETS)
        assert record.correlation_id == f"{STRATEGY}:rebalance:{record.sequence}"
    days = [datetime.fromtimestamp(record.instant, ZoneInfo("UTC")).date() for record in records]
    assert [(day.year, day.month, day.day) for day in days[:2]] == [(2025, 3, 1), (2025, 4, 1)]
    assert len({(day.year, day.month) for day in days}) == 12


def test_each_rebalance_is_the_book_by_the_next_and_the_account_holds_it_at_the_end(
    ran: tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance],
) -> None:
    result, strategy, model = ran
    traded_views = model.views[2:]
    records = strategy.records
    for record, later in zip(records, traded_views[1:], strict=False):
        # The orders a rebalance set filled at each asset's next quote: the
        # strategy's own book at the next rebalance is exactly its targets.
        assert dict(later.quantities) == _held(record.targets)
    assert dict(strategy.holdings) == _held(records[-1].targets)
    positions = result.run.pipeline.portfolio.positions
    assert {
        asset: position.quantity for asset, position in positions.items() if position.quantity
    } == (dict(strategy.holdings))


def test_the_weights_the_triggers_measure_from_are_the_targets_at_their_prices(
    ran: tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance],
) -> None:
    _, strategy, model = ran
    view = model.views[2]
    record = strategy.records[0]
    for asset in ASSETS:
        value = record.targets[asset] * view.prices[asset] / view.capital
        assert record.weights[asset] == pytest.approx(float(value), abs=1e-12)
    assert record.residual_weight < 3 * max(view.prices.values()) / view.capital


# --------------------------------------------------------------------------- #
# Certified answers only
# --------------------------------------------------------------------------- #


def test_a_problem_that_cannot_be_met_records_why_and_trades_nothing() -> None:
    # Three assets capped at 0.2 cannot hold a fully invested book.
    result, strategy = _run(SampleMinimumVariance(lookback=40, cap=0.2))
    assert len(strategy.records) == 12
    for record in strategy.records:
        assert record.status is ConstructionStatus.INFEASIBLE and not record.traded
        assert record.targets == {}
    assert strategy.holdings == {} and strategy.target is None
    assert result.run.pipeline.portfolio.positions == {} or all(
        not position.quantity for position in result.run.pipeline.portfolio.positions.values()
    )


# --------------------------------------------------------------------------- #
# Identity and state
# --------------------------------------------------------------------------- #


def test_two_runs_are_one_result(
    ran: tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance],
) -> None:
    result, strategy, _ = ran
    again, other = _run(SampleMinimumVariance(lookback=40, cap=0.6))
    assert digest_run(again).result_id == digest_run(result).result_id
    assert other.records == strategy.records


def _pauses_at(boundary: int) -> Any:
    def pause(progress: Any) -> bool:
        return bool(progress.records_replayed == boundary)

    return pause


@pytest.fixture(scope="module")
def replayed(
    ran: tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance],
) -> str:
    """The whole run, replayed: the result a paused replay resumes to."""

    whole = ReplayBacktest.run(
        _config(),
        DATASET,
        _runtime(_strategy(SampleMinimumVariance(lookback=40, cap=0.6))),
        context_factory,
    )
    assert not isinstance(whole, PausedReplay)
    assert whole.backtest.fills == ran[0].fills
    return digest_run(whole.backtest).result_id


@pytest.mark.parametrize("boundary", [180, 451, 900])
def test_a_paused_run_resumes_to_the_same_result_and_records(
    boundary: int,
    ran: tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance],
    replayed: str,
) -> None:
    # 180 records is just after the first rebalance's sales and purchases; 451,
    # a rebalance's slice itself; 900, ten months in.
    _, strategy, _ = ran
    config = _config()
    paused = ReplayBacktest.run(
        config,
        DATASET,
        _runtime(_strategy(SampleMinimumVariance(lookback=40, cap=0.6))),
        context_factory,
        pause_when=_pauses_at(boundary),
    )
    assert isinstance(paused, PausedReplay)
    payload = deserialize(serialize(capture_replay(paused)))
    fresh = _strategy(SampleMinimumVariance(lookback=40, cap=0.6))
    objects = RunObjects(
        RuntimeObjects(
            config.pipeline.sizing_model,
            config.pipeline.simulator,
            {STRATEGY: fresh},
            config.pipeline.instruments,
        ),
        config.fill_policy,
    )
    resumed = ReplayBacktest.resume(
        restore_replay(replay_from_primitives(payload), objects, DATASET), context_factory
    )
    assert digest_run(resumed.backtest).result_id == replayed
    assert fresh.records == strategy.records
    assert dict(fresh.holdings) == dict(strategy.holdings)


# --------------------------------------------------------------------------- #
# Its book is its own: beside another strategy in one account (S-10's piece)
# --------------------------------------------------------------------------- #

HOLDER = "HOLDER"
#: The holder buys 2,000 of the first asset at the run's first quote and sells
#: 1,500 of them on day 200, between two of the rebalancer's month starts.
HOLDER_PLAN = {START: Decimal("2000"), START + 200 * DAY: Decimal("500")}
SHARED = ASSETS[0]


class _Holder(BaseStrategy):
    """Holds what its plan states for the shared asset, from each planned instant."""

    def on_quote(self, context: StrategyContext, event: Any) -> list[Intent]:
        quote = event.quote
        target = HOLDER_PLAN.get(quote.timestamp)
        if quote.asset_id != SHARED or target is None:
            return []
        return [
            Intent(
                HOLDER,
                SHARED,
                target,
                timestamp=quote.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
                terms=MARKET,
            )
        ]


def _shared_run() -> tuple[BacktestResult, RebalancingStrategy, SampleMinimumVariance]:
    model = SampleMinimumVariance(lookback=40, cap=0.6)
    # Two strategies share the account: the rebalancer is told its budget, a
    # fixed capital, since the account's equity is not its own.
    strategy = _strategy(model, FixedCapital(Decimal("600000")))
    config = _config()
    config = replace(
        config,
        pipeline=replace(
            config.pipeline,
            budget=CapitalBudget(
                global_capital=config.pipeline.starting_cash,
                maximum_exposure=config.pipeline.starting_cash * Decimal("10"),
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY: Decimal("600000"), HOLDER: Decimal("400000")},
            ),
        ),
    )
    runtime = create_runtime()
    subscriptions = {STRATEGY: frozenset({"slices", "fills"}), HOLDER: frozenset({"quotes"})}
    instances: dict[str, BaseStrategy] = {STRATEGY: strategy, HOLDER: _Holder()}
    for strategy_id, instance in instances.items():
        runtime = register_strategy(runtime, strategy_id, instance)
    running = {}
    for strategy_id in instances:
        entry, _ = RuntimeSupervisor.configure(runtime.strategies[strategy_id], {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, subscriptions[strategy_id], 1.2)
        entry, _ = RuntimeSupervisor.start(entry, 1.3)
        running[strategy_id] = entry
    runtime = replace(runtime, strategies=running)
    result = BacktestEngine.run(config, DATASET, runtime, context_factory)
    return result, strategy, model


def test_beside_another_strategy_its_book_is_its_own_and_its_rebalances_reconcile() -> None:
    result, strategy, model = _shared_run()
    records = strategy.records
    assert len(records) == 12 and all(record.traded for record in records)
    allocation = result.run.pipeline.allocation
    positions = result.run.pipeline.portfolio.positions
    # Its book is the position allocation measures its targets against...
    for asset in ASSETS:
        held = strategy.holdings.get(asset, Decimal(0))
        assert AllocationEngine.strategy_position(allocation, STRATEGY, asset) == held
    # ...and the account holds both strategies' books.
    holder = AllocationEngine.strategy_position(allocation, HOLDER, SHARED)
    assert holder == Decimal("500")
    for asset in ASSETS:
        account = positions[asset].quantity if asset in positions else Decimal(0)
        expected = strategy.holdings.get(asset, Decimal(0)) + (holder if asset == SHARED else 0)
        assert account == expected
    # Each rebalance is its own book by the next, though the holder traded the
    # shared asset in between; the weights are of its budget, not the account.
    traded_views = model.views[2:]
    for record, later in zip(records, traded_views[1:], strict=False):
        assert dict(later.quantities) == _held(record.targets)
        assert later.capital == Decimal("600000")
        for asset in ASSETS:
            value = later.quantities.get(asset, Decimal(0)) * later.prices[asset] / later.capital
            assert later.weights[asset] == pytest.approx(float(value), abs=1e-12)
    assert dict(strategy.holdings) == _held(records[-1].targets)
