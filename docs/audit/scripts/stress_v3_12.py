"""The v3.12 release stress run: 10,000 assets, 1,000 strategies, 100 venues.

Kept for provenance and re-runnable, not run by the suite (it takes minutes).
Each scenario drives a real code path at the scale the release plan names and
prints what it cost; every scenario also checks an outcome, so a run that
finished fast by doing nothing would fail rather than read well::

    PYTHONHASHSEED=0 python3.12 docs/audit/scripts/stress_v3_12.py

What each one measures
----------------------
``assets``      10,000 instruments in one registry, classified along two
                dimensions, a classification limit on every sector, and a
                buy-and-hold book of all 10,000 run through the canonical
                backtest path -- against 400 assets, so the per-record cost
                can be compared.
``strategies``  1,000 strategies in one pipeline, each trading its own asset on
                its own schedule, with every strategy's capital ceiling
                enforced, netted and reserved.
``venues``      100 listing venues, each with its own calendar and zone: day
                orders on all of them expire at their own venue's close; one
                book reconciled across 100 broker accounts; a session timer per
                venue advanced through a month.
``construction``the factor-structured solver at 10,000 assets (the dense
                covariance it would replace is 10^8 entries and is not built).
``checkpoints`` a 20,000-record run with a retention policy, checkpointed every
                1,000 records, read back as the full capture.
"""

from __future__ import annotations

import gc
import resource
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import time as clock_time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from alphalab.allocation.budget import CapitalBudget  # noqa: E402
from alphalab.allocation.constraints import AllocationConstraints  # noqa: E402
from alphalab.backtesting import (  # noqa: E402
    BacktestEngine,
    ExecutionMode,
    MarketDataset,
    RunConfig,
)
from alphalab.core.enums import AssetType  # noqa: E402
from alphalab.data.calendar import MarketCalendar, SessionWindow  # noqa: E402
from alphalab.execution.simulator import ExecutionSimulator  # noqa: E402
from alphalab.instrument.record import InstrumentRecord  # noqa: E402
from alphalab.instrument.registry import (  # noqa: E402
    InstrumentRegistry,
    classify_dimensions,
    register_instruments,
)
from alphalab.market.bar import Bar, TimeFrame  # noqa: E402
from alphalab.portfolio.account import Account  # noqa: E402
from alphalab.risk.limits import (  # noqa: E402
    ClassificationLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig  # noqa: E402
from alphalab.strategy.context import (  # noqa: E402
    NoMarket,
    NoOrders,
    NoPortfolio,
    NoRiskView,
    StrategyContext,
)
from alphalab.strategy.events import Intent  # noqa: E402
from alphalab.strategy.protocol import BaseStrategy  # noqa: E402
from alphalab.strategy.runtime import create_runtime, register_strategy  # noqa: E402
from alphalab.strategy.state import RuntimeState  # noqa: E402
from alphalab.strategy.supervisor import RuntimeSupervisor  # noqa: E402

HUGE = Decimal("1000000000000")
DAY = 86_400.0
FIRST = 1_700_000_000.0


def _peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _timed(work: Callable[[], Any]) -> tuple[Any, float]:
    gc.collect()
    start = time.process_time()
    result = work()
    return result, time.process_time() - start


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def _context(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


def _running(strategies: dict[str, BaseStrategy], topics: frozenset[str]) -> RuntimeState:
    state = create_runtime()
    for strategy_id, strategy in strategies.items():
        state = register_strategy(state, strategy_id, strategy)
    running = {}
    for strategy_id, held in state.strategies.items():
        held, _ = RuntimeSupervisor.configure(held, {}, 1.0)
        held, _ = RuntimeSupervisor.initialize(held, 1.1)
        held, _ = RuntimeSupervisor.subscribe(held, topics, 1.2)
        held, _ = RuntimeSupervisor.start(held, 1.3)
        running[strategy_id] = held
    return RuntimeState(strategies=running)


def _limits(classification: tuple[ClassificationLimit, ...] = ()) -> RiskLimits:
    return RiskLimits(
        order_size=OrderSizeLimit(HUGE, HUGE),
        position=PositionLimit(HUGE, HUGE),
        exposure=ExposureLimit(HUGE, HUGE),
        leverage=LeverageLimit(Decimal("1000")),
        margin=MarginLimit(Decimal("1000")),
        daily_loss=None,
        drawdown=DrawdownLimit(Decimal("1.00")),
        classification=classification,
    )


def _config(pipeline: ExecutionPipelineConfig) -> RunConfig:
    return RunConfig(mode=ExecutionMode.BACKTEST, pipeline=pipeline, seed=7, start_timestamp=1.0)


def _pipeline(**changes: Any) -> ExecutionPipelineConfig:
    base = ExecutionPipelineConfig(
        account=Account("STRESS", "USD", "v3.12 stress", 1.0),
        starting_cash=HUGE,
        budget=CapitalBudget(global_capital=HUGE, maximum_exposure=HUGE),
        allocation_constraints=AllocationConstraints(allow_shorting=False),
        risk_limits=_limits(),
        simulator=ExecutionSimulator(),
    )
    return replace(base, **changes)


def _bar(asset: str, timestamp: float, price: Decimal) -> Bar:
    return Bar(
        asset_id=asset,
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1000000"),
        vwap=price,
        trade_count=1,
        timeframe=TimeFrame.D1,
    )


# --------------------------------------------------------------------------- #
# 10,000 assets
# --------------------------------------------------------------------------- #


class _BuyEachOnce(BaseStrategy):
    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        if bar.timestamp != FIRST:
            return ()
        return (Intent(self.strategy_id, bar.asset_id, Decimal("10"), timestamp=bar.timestamp),)


def _universe(count: int) -> tuple[InstrumentRegistry, list[str]]:
    records = [
        InstrumentRecord(
            f"S{index:05d}",
            AssetType.EQUITY,
            f"X{index % 100:03d}",
            "USD",
            sector=f"Sector{index % 11}",
        )
        for index in range(count)
    ]
    registry = register_instruments(InstrumentRegistry(), records)
    assets = [record.asset_id for record in records]
    registry = classify_dimensions(
        registry, "country", {asset: f"C{index % 37}" for index, asset in enumerate(assets)}
    )
    return registry, assets


def _book_run(count: int, bars: int) -> tuple[float, int, Any]:
    registry, assets = _universe(count)
    limits = _limits(
        (
            ClassificationLimit("sector", max_share=Decimal("0.5")),
            ClassificationLimit("country", max_gross=HUGE),
        )
    )
    pipeline = _pipeline(instruments=registry, risk_limits=limits)
    data = [
        _bar(asset, FIRST + DAY * step, Decimal(100 + (step * (index + 3)) % 11 + index % 5))
        for step in range(bars)
        for index, asset in enumerate(assets)
    ]
    dataset = MarketDataset.of(f"ASSETS-{count}", data)
    strategy_id = str(UUID(int=0x5712, version=4))
    result, cpu = _timed(
        lambda: BacktestEngine.run(
            _config(pipeline),
            dataset,
            _running({strategy_id: _BuyEachOnce(strategy_id)}, frozenset({"bars"})),
            _context,
        )
    )
    return cpu, len(data), result


def scenario_assets() -> None:
    print("== 10,000 assets: registry, two classification dimensions, sector limits, a full book")
    _, registry_cpu = _timed(lambda: _universe(10_000))
    print(f"  registry of 10,000 + 10,000 country labels: {registry_cpu:.2f}s CPU")
    small_cpu, small_records, small = _book_run(400, 3)
    large_cpu, large_records, large = _book_run(10_000, 3)
    assert len(small.state.portfolio.positions) == 400
    assert len(large.state.portfolio.positions) == 10_000, "every asset is held"
    per_small = small_cpu / small_records * 1e6
    per_large = large_cpu / large_records * 1e6
    print(
        f"  400 assets:    {small_records:>6} records {small_cpu:7.2f}s  {per_small:7.0f} us/record"
    )
    print(
        f"  10,000 assets: {large_records:>6} records {large_cpu:7.2f}s  {per_large:7.0f} us/record"
    )
    print(
        f"  per-record cost at 25x the universe: {per_large / per_small:.2f}x; "
        f"peak RSS {_peak_mb():.0f} MB"
    )


# --------------------------------------------------------------------------- #
# 1,000 strategies
# --------------------------------------------------------------------------- #


class _Periodic(BaseStrategy):
    """Buys its own asset every ``period`` bars, sells it back the next bar."""

    def __init__(self, strategy_id: str, asset: str, period: int, phase: int) -> None:
        self.strategy_id, self.asset, self.period, self.phase = strategy_id, asset, period, phase

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        if bar.asset_id != self.asset:
            return ()
        step = round((bar.timestamp - FIRST) / DAY)
        if step % self.period == self.phase:
            return (Intent(self.strategy_id, self.asset, Decimal("5"), timestamp=bar.timestamp),)
        if step % self.period == (self.phase + 1) % self.period:
            return (Intent(self.strategy_id, self.asset, Decimal("-5"), timestamp=bar.timestamp),)
        return ()


def scenario_strategies() -> None:
    print("== 1,000 strategies: one pipeline, ceilings enforced, netted, reserved")
    assets = [str(UUID(int=index + 1, version=4)) for index in range(100)]
    strategy_ids = [str(UUID(int=0x10_0000 + index, version=4)) for index in range(1_000)]
    strategies = {
        strategy_id: _Periodic(strategy_id, assets[index % 100], 4, index % 3)
        for index, strategy_id in enumerate(strategy_ids)
    }
    budget = CapitalBudget(
        global_capital=HUGE,
        maximum_exposure=HUGE,
        strategy_budgets={strategy_id: Decimal("1000000") for strategy_id in strategy_ids},
        enforce_strategy_budgets=True,
    )
    pipeline = _pipeline(budget=budget)
    days = 20
    data = [
        _bar(asset, FIRST + DAY * step, Decimal(100 + step % 7))
        for step in range(days)
        for asset in assets
    ]
    dataset = MarketDataset.of("STRATEGIES", data)
    runtime, setup = _timed(lambda: _running(strategies, frozenset({"bars"})))
    result, cpu = _timed(lambda: BacktestEngine.run(_config(pipeline), dataset, runtime, _context))
    fills = len(result.fills)
    assert fills > 1_000, "the strategies traded"
    print(f"  1,000 strategies registered and started: {setup:.2f}s CPU")
    print(
        f"  {len(data)} records, {fills} fills, {len(result.trades)} trades: {cpu:.2f}s CPU "
        f"({cpu / len(data) * 1e3:.1f} ms/record); peak RSS {_peak_mb():.0f} MB"
    )


# --------------------------------------------------------------------------- #
# 100 venues
# --------------------------------------------------------------------------- #

ZONES = (
    "America/New_York",
    "Europe/London",
    "Asia/Tokyo",
    "Asia/Kolkata",
    "Australia/Sydney",
    "Europe/Berlin",
    "Asia/Hong_Kong",
    "America/Chicago",
    "Asia/Riyadh",
    "America/Sao_Paulo",
)


def _calendars() -> dict[str, MarketCalendar]:
    return {
        f"X{index:03d}": MarketCalendar(
            calendar_id=f"X{index:03d}",
            timezone_name=ZONES[index % len(ZONES)],
            weekly_sessions={
                day: (SessionWindow(clock_time(9, index % 30), clock_time(16, 0)),)
                for day in range(5)
            },
        )
        for index in range(100)
    }


def scenario_venues() -> None:
    from alphalab.broker.reconciliation import ExternalOrderMap
    from alphalab.lifecycle import (
        AccountMirror,
        ReconciliationTolerances,
        SymbolMapping,
        Tolerance,
        reconcile_accounts,
    )
    from alphalab.runtime.calendars import VenueCalendars
    from alphalab.scheduler import SchedulerEngine, ScheduleType, session_timer
    from tests.unit.lifecycle.test_reconciliation import (
        broker_order,
        broker_with,
        execution,
        oms_order,
        pipeline_with,
        report,
    )

    print("== 100 venues: calendars, multi-account reconciliation, session timers")
    calendars = _calendars()
    venues, build = _timed(lambda: VenueCalendars(by_exchange=calendars))
    instant = FIRST + 3 * 3600.0
    expiries, expiry_cpu = _timed(
        lambda: [
            calendar.day_order_expiry(instant + offset)
            for offset in range(0, 86_400 * 5, 3_600)
            for calendar in venues.by_exchange.values()
        ]
    )
    assert all(expiry is not None for expiry in expiries)
    print(
        f"  100 venue calendars: {build:.3f}s; "
        f"{len(expiries)} day-order expiries: {expiry_cpu:.2f}s"
    )

    ids = [f"{index:08d}-0000-0000-0000-000000000000" for index in range(10_000)]
    book = pipeline_with(
        orders=tuple(oms_order(order_id=oms_id) for oms_id in ids),
        reports=tuple(report(f"x-{index}", order_id=oms_id) for index, oms_id in enumerate(ids)),
        cash=Decimal(50000 * 100),
    )
    accounts = {}
    assignment = {}
    for venue in range(100):
        own = ids[venue::100]
        mapping = ExternalOrderMap()
        for oms_id in own:
            mapping = mapping.bind(oms_id, f"H-{oms_id}")
            assignment[oms_id] = f"venue-{venue:03d}"
        accounts[f"venue-{venue:03d}"] = AccountMirror(
            broker=broker_with(
                orders=tuple(
                    broker_order(broker_order_id=f"H-{oms_id}", oms_order_id=oms_id)
                    for oms_id in own
                ),
                executions=tuple(
                    execution(f"x-{ids.index(oms_id)}", broker_order_id=f"H-{oms_id}")
                    for oms_id in own
                ),
                cash=Decimal(50000),
            ),
            mapping=mapping,
            symbols=SymbolMapping.identity(),
        )
    exact = Tolerance(absolute=Decimal("0"))
    tolerances = ReconciliationTolerances(exact, exact, exact, exact, exact, exact, exact)
    result, recon_cpu = _timed(lambda: reconcile_accounts(book, accounts, assignment, tolerances))
    assert result.compared_orders == 10_000 and result.compared_fills == 10_000
    print(
        f"  one book of 10,000 orders and fills across 100 accounts: {recon_cpu:.2f}s, "
        f"{len(result.mismatches)} mismatches, {len(result.unassigned)} unassigned"
    )

    state = SchedulerEngine.initialize(FIRST)
    for venue, calendar in calendars.items():
        state = SchedulerEngine.schedule_timer(
            state, session_timer(venue, ScheduleType.SESSION_OPEN, calendar, FIRST), FIRST
        )

    def month() -> int:
        nonlocal state
        fired = 0
        now = FIRST
        while now < FIRST + 30 * DAY:
            now += 1800.0
            state, triggered = SchedulerEngine.advance_clock(state, now)
            fired += len(triggered)
        return fired

    fired, timer_cpu = _timed(month)
    assert fired >= 100 * 20, "every venue opened on every weekday"
    print(f"  100 session timers through 30 days: {fired} firings, {timer_cpu:.2f}s")


# --------------------------------------------------------------------------- #
# Construction at 10,000 assets
# --------------------------------------------------------------------------- #


def scenario_construction() -> None:
    import random

    from alphalab.portfolio_optimizer.factor_quadratic import (
        FactorCurvature,
        FactorQuadraticProgram,
        solve_factor_quadratic_program,
    )
    from alphalab.portfolio_optimizer.quadratic import LinearConstraint, SolveStatus

    print("== construction: the factor-structured solver at 10,000 assets")
    n, k = 10_000, 5
    rng = random.Random(7)
    loadings = tuple(tuple(rng.gauss(0.0, 1.0) for _ in range(k)) for _ in range(n))
    factor_covariance = tuple(tuple(0.04 if i == j else 0.0 for j in range(k)) for i in range(k))
    specific = tuple(0.01 + 0.02 * rng.random() for _ in range(n))
    constraints = [LinearConstraint("budget", tuple((i, 1.0) for i in range(n)), 1.0, True)]
    constraints += [LinearConstraint(f"long:{i}", ((i, 1.0),), 0.0, False) for i in range(n)]
    constraints += [LinearConstraint(f"cap:{i}", ((i, -1.0),), -0.05, False) for i in range(n)]
    returns = random.Random(11)
    expected = tuple(-returns.gauss(0.05, 0.10) for _ in range(n))
    program = FactorQuadraticProgram(
        FactorCurvature(loadings, factor_covariance, specific, 1.0), expected, tuple(constraints)
    )
    solution, cpu = _timed(
        lambda: solve_factor_quadratic_program(
            program,
            feasibility_tolerance=1e-9,
            convergence_tolerance=1e-9,
            max_iterations=100_000,
        )
    )
    assert solution.status is SolveStatus.OPTIMAL
    weights = solution.x
    assert abs(sum(weights) - 1.0) < 1e-8
    assert min(weights) > -1e-9 and max(weights) < 0.05 + 1e-9
    print(
        f"  10,000 assets, 5 factors, budget + long-only + 5% cap (20,001 rows): "
        f"{solution.status.name} in {cpu:.2f}s CPU, {solution.iterations} steps, "
        f"{len(solution.active)} constraints binding"
    )


# --------------------------------------------------------------------------- #
# Checkpoints over a long, retained run
# --------------------------------------------------------------------------- #


def scenario_checkpoints() -> None:
    from alphalab.common.serialization import to_serializable
    from alphalab.runtime.checkpoint import checkpoint, read_checkpoints
    from alphalab.runtime.retention import RetentionPolicy
    from alphalab.runtime.run import RunEngine
    from alphalab.runtime.run_snapshot import capture as capture_run
    from tests.integration.harness import ScriptedStrategy, context_factory, sized_quote

    print("== checkpoints: 20,000 records, retention 2,000, a segment every 1,000")
    asset = str(UUID(int=0x4242, version=4))
    strategy_id = str(UUID(int=0x4243, version=4))
    plan = {2.0 + i: (Decimal("3") if i % 4 == 0 else Decimal("-1")) for i in range(0, 20_000, 50)}
    pipeline = replace(
        _pipeline(),
        retention=RetentionPolicy(
            market_history=2_000, steps=2_000, audit_events=2_000, results=2_000
        ),
    )
    config = _config(pipeline)
    runtime = _running({strategy_id: ScriptedStrategy(strategy_id, asset, plan)}, frozenset({"*"}))
    from alphalab.market.record import MarketRecord

    def run() -> tuple[list[str], Any]:
        state = RunEngine.initialize(config, runtime)
        payloads: list[str] = []
        mark = None
        for index in range(20_000):
            quote = sized_quote(asset, 2.0 + index, Decimal(100 + index % 9), Decimal("100"))
            state, _ = RunEngine.advance(
                state, MarketRecord(f"R-{index}", quote.timestamp, quote), context_factory
            )
            if (index + 1) % 1_000 == 0:
                payload, mark = checkpoint(state, mark)
                payloads.append(payload)
        return payloads, state

    (payloads, state), cpu = _timed(run)
    assert to_serializable(read_checkpoints(payloads)) == to_serializable(capture_run(state))
    sizes = [len(payload) for payload in payloads]
    print(
        f"  20,000 records with 20 checkpoints: {cpu:.2f}s CPU; base {sizes[0] / 1e6:.2f} MB, "
        f"last segment {sizes[-1] / 1e6:.2f} MB; chain reads back as the full capture"
    )


SCENARIOS = {
    "assets": scenario_assets,
    "strategies": scenario_strategies,
    "venues": scenario_venues,
    "construction": scenario_construction,
    "checkpoints": scenario_checkpoints,
}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(SCENARIOS)
    for name in chosen:
        SCENARIOS[name]()
