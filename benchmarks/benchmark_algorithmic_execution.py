"""Benchmark for execution algorithms on the historical path (v4.1.1, ledger FUT-009).

A strategy places a 100-share parent every 20 records, and a TWAP works each in
four children over eight records, so a 4x dataset carries 4x the parents and 4x
the children. The run reports throughput and the judged scaling factor, and
what working orders in children costs against the same run filling them whole.

The algorithm book is worked once per record for the record's asset, and holds
only open parents, so its cost per record is bounded by the parents working at
once -- not by every parent the run ever placed. A factor well above linear would
mean that bound had been lost.
"""

import time
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from _stable_timing import SAMPLES, fastest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    AlgorithmAssignment,
    AlgorithmicResult,
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    ExecutionPlan,
    MarketDataset,
    RunConfig,
)
from alphalab.execution.algorithms import TWAP, Urgency
from alphalab.execution.policy import FillTiming
from alphalab.market.quote import Quote
from alphalab.portfolio.account import Account
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.strategy import DiscardingLogger, FixedClock, context_factory
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

START_CASH = Decimal("10000000000")
SEED = 20809
EVERY = 20
MAX_SCALING_FACTOR = 6.0
#: Children are extra fills, each through the whole accounting path, and the
#: book is worked at every record; a few times the whole-order run, not more.
MAX_ALGORITHMIC_OVERHEAD = 4.0
CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())
PLAN = ExecutionPlan(AlgorithmAssignment(TWAP(4, Urgency.neutral()), 8.0, Decimal("1")))


class _Accumulator(BaseStrategy):
    """Buys 100 shares every ``EVERY`` records."""

    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if int(event.quote.timestamp) % EVERY != 0:
            return ()
        return (
            Intent(
                self._strategy_id, self._asset_id, Decimal("100"), timestamp=event.quote.timestamp
            ),
        )


def _running(strategy_id: str, asset_id: str) -> RuntimeState:
    state = register_strategy(create_runtime(), strategy_id, _Accumulator(strategy_id, asset_id))
    strategy = state.strategies[strategy_id]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"quotes"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={strategy_id: strategy})


def _config(strategy_id: str) -> RunConfig:
    huge = Decimal("100000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("acct-algo-bench", "USD", "Algorithmic Benchmark Account", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=huge,
                cash_buffer=Decimal("0"),
                strategy_budgets={strategy_id: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(huge, huge),
                position=PositionLimit(huge, huge),
                exposure=ExposureLimit(huge, huge),
                leverage=LeverageLimit(Decimal("1000")),
                margin=MarginLimit(Decimal("1.00")),
                daily_loss=DailyLossLimit(huge, "UTC"),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        seed=SEED,
        start_timestamp=1.0,
    )


def _dataset(asset_id: str, records: int) -> MarketDataset:
    return MarketDataset.of(
        "BENCH-ALGO",
        [
            Quote(
                asset_id=asset_id,
                timestamp=2.0 + index,
                bid=Decimal("100.005") + Decimal(index % 20),
                ask=Decimal("100.005") + Decimal(index % 20),
                bid_size=Decimal("1000"),
                ask_size=Decimal("1000"),
                venue="SIM",
                currency="USD",
            )
            for index in range(records)
        ],
    )


def _workload(records: int, plan: ExecutionPlan | None) -> Any:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    config, dataset = _config(strategy_id), _dataset(asset_id, records)

    def work() -> BacktestResult:
        return BacktestEngine.run(
            config, dataset, _running(strategy_id, asset_id), CONTEXTS, execution=plan
        )

    return work


def _check(result: BacktestResult, records: int) -> int:
    if not isinstance(result, AlgorithmicResult):
        raise SystemExit("The algorithmic run returned no book.")
    book = result.algorithms
    parents = records // EVERY
    children = sum(
        len(f.algorithm.children) for f in book.finished.values() if f.algorithm is not None
    ) + sum(len(a.children) for a in book.incomplete)
    held = len(book.finished) + len(book.incomplete)
    if held != parents:
        raise SystemExit(f"Expected {parents} parents, the book holds {held}.")
    return children


def run_benchmark() -> None:
    small, large = 1_000, 4_000
    print("Starting Algorithmic Execution Benchmark...")
    for records in (small, large):
        start = time.perf_counter()
        result = _workload(records, PLAN)()
        elapsed = time.perf_counter() - start
        children = _check(result, records)
        print(
            f"  records={records:>6}  parents={records // EVERY:>4}  children={children:>5}  "
            f"time={elapsed:.4f}s  ({records / max(elapsed, 1e-9):,.1f} records/sec)"
        )

    small_cpu, large_cpu, whole_cpu = fastest(
        [_workload(small, PLAN), _workload(large, PLAN), _workload(large, None)]
    )
    scaling = large_cpu / max(small_cpu, 1e-9)
    overhead = large_cpu / max(whole_cpu, 1e-9)
    print(
        f"  judged (CPU time, collector paused, fastest of {SAMPLES}): 4x workload cost "
        f"{scaling:.2f}x the time; working orders in children cost {overhead:.2f}x filling "
        "them whole"
    )
    if scaling > MAX_SCALING_FACTOR:
        raise SystemExit(
            f"Algorithmic scaling factor {scaling:.2f}x exceeds {MAX_SCALING_FACTOR:.2f}x; the "
            "child path has regressed toward quadratic behaviour."
        )
    if overhead > MAX_ALGORITHMIC_OVERHEAD:
        raise SystemExit(
            f"Working orders in children cost {overhead:.2f}x filling them whole, above "
            f"{MAX_ALGORITHMIC_OVERHEAD:.2f}x."
        )


if __name__ == "__main__":
    run_benchmark()
