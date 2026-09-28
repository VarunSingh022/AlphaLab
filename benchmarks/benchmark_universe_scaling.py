"""Benchmark: the canonical backtest path across a growing universe (ledger PRF-001).

Every other benchmark of the execution path runs one asset, so none of them
could see what the pre-v4 audit measured: until v3.10 each market event
re-marked, logged and summed every held position, and a record cost more the
more assets the book held. 160 assets for 50 bars took 55 CPU-seconds and
337 MB.

This runs buy-and-hold books of increasing size through
:class:`~alphalab.backtesting.engine.BacktestEngine` -- every asset bought on
the first bar and held -- and reports the **cost per record** at each size.
Linear scaling keeps that figure flat; the removed quadratic grew it in
proportion to the universe. The run fails if an 8x universe makes a record more
than twice as dear, or if the largest book misses its budget.

``tests/regression/test_universe_scaling.py`` holds the same property as
standing assertions.
"""

import time
import tracemalloc
from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from uuid import UUID

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import BacktestEngine, ExecutionMode, MarketDataset, RunConfig
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.bar import Bar, TimeFrame
from alphalab.portfolio.account import Account
from alphalab.risk.limits import (
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

UNIVERSES = (50, 100, 200, 400)
BARS = 20
DAY = 86_400.0
FIRST = 1_700_000_000.0
STRATEGY_ID = str(UUID(int=0xB00C, version=4))
HUGE = Decimal("1000000000")
#: A record in the largest book may cost at most this many times one in the
#: smallest. Linear predicts about 1; the removed quadratic predicted 8.
MAX_PER_RECORD_GROWTH = 2.0
#: 8,000 records through the canonical path, with room for a slow runner.
BUDGET_SECONDS = 60.0


class _BuyEachThenHold(BaseStrategy):
    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        if bar.timestamp != FIRST:
            return ()
        return (Intent(STRATEGY_ID, bar.asset_id, Decimal("10"), timestamp=bar.timestamp),)


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


def _strategy() -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, _BuyEachThenHold())
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return RuntimeState(strategies={STRATEGY_ID: strategy})


def _config() -> RunConfig:
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("BENCH", "USD", "Universe benchmark", 1.0),
            starting_cash=HUGE,
            budget=CapitalBudget(global_capital=HUGE, maximum_exposure=HUGE),
            allocation_constraints=AllocationConstraints(allow_shorting=False),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(HUGE, HUGE),
                position=PositionLimit(HUGE, HUGE),
                exposure=ExposureLimit(HUGE, HUGE),
                leverage=LeverageLimit(Decimal("1000")),
                margin=MarginLimit(Decimal("1000")),
                daily_loss=None,
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            simulator=ExecutionSimulator(),
        ),
        seed=7,
        start_timestamp=1.0,
    )


def _bars(universe: int) -> list[Bar]:
    assets = [str(UUID(int=index + 1, version=4)) for index in range(universe)]
    bars = []
    for step in range(BARS):
        for index, asset in enumerate(assets):
            price = Decimal(100 + (step * (index + 3)) % 11 + index % 5)
            bars.append(
                Bar(
                    asset_id=asset,
                    timestamp=FIRST + DAY * step,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=Decimal("1000000"),
                    vwap=price,
                    trade_count=1,
                    timeframe=TimeFrame.D1,
                )
            )
    return bars


def run_benchmark() -> None:
    print(f"Universe scaling: buy-and-hold books, {BARS} daily bars each")
    print(f"  {'assets':>7} {'records':>8} {'cpu s':>8} {'us/record':>10} {'peak MB':>8}")
    per_record: dict[int, float] = {}
    for universe in UNIVERSES:
        dataset = MarketDataset.of(f"UNIVERSE-{universe}", _bars(universe))
        tracemalloc.start()
        start = time.process_time()
        result = BacktestEngine.run(_config(), dataset, _strategy(), _context)
        elapsed = time.process_time() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        records = universe * BARS
        assert len(result.fills) == universe, "every asset is bought once"
        assert len(result.state.portfolio.positions) == universe
        per_record[universe] = elapsed / records
        print(
            f"  {universe:>7} {records:>8} {elapsed:>8.2f} "
            f"{per_record[universe] * 1e6:>10.0f} {peak / 1e6:>8.1f}"
        )
        if elapsed > BUDGET_SECONDS:
            raise SystemExit(f"{universe} assets took {elapsed:.1f}s, over {BUDGET_SECONDS:.0f}s")

    smallest, largest = UNIVERSES[0], UNIVERSES[-1]
    growth = per_record[largest] / per_record[smallest]
    print(
        f"  a record in a {largest}-asset book costs {growth:.2f}x one in a "
        f"{smallest}-asset book (linear: ~1.0x; the removed quadratic: ~{largest // smallest}x)"
    )
    if growth > MAX_PER_RECORD_GROWTH:
        raise SystemExit(
            f"Per-record cost grew {growth:.2f}x over an {largest // smallest}x universe; "
            "a record's cost is growing with the size of the book again."
        )


if __name__ == "__main__":
    run_benchmark()
