"""
AlphaLab Examples
=================

Example 02 : Your First Backtest

Difficulty : Beginner

Estimated Time : 10 minutes

Topics
------

• Ingesting a CSV into a versioned dataset
• Declaring the instrument a symbol means
• A strategy that states the position it wants
• The one execution path: allocation, risk, OMS, fills, portfolio
• Reading the result

What this shows
---------------

A complete backtest in the fewest honest steps. The sample file is ingested
into a canonical dataset -- whose version is derived from the bytes, so the
result can name exactly what it ran on -- and a moving-average crossover runs
over Apple's bars through the same execution path every AlphaLab run takes:

    CSV -> Dataset -> MarketDataset -> strategy -> allocation -> risk -> OMS
        -> simulated fills -> portfolio -> performance report

The strategy never counts shares. It states the position it wants --
``TARGET_QUANTITY`` of 100 shares while the fast average is above the slow
one, zero otherwise -- and allocation turns the difference from what it holds
into an order (v3.11, ledger FEA-001). Stating the same target again asks for
nothing, so the strategy is free to say it on every bar.

Run

    python examples/02_backtest.py
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import DataRequest, backtest, ingest_csv, select
from alphalab.backtesting import ExecutionMode, RunConfig
from alphalab.core.enums import AssetType
from alphalab.data import (
    CleaningPolicy,
    DataAssetClass,
    DuplicatePolicy,
    IngestionRequest,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.time import BarStamp
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
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
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

DATA = Path(__file__).parent / "data" / "sample_ohlcv.csv"
PROVIDER = "sample-csv"
STRATEGY_ID = "EX02-MA-CROSS"
START_CASH = Decimal("100000.00")
SEED = 20_250_102

# What the symbol in the file *is*: a Nasdaq-listed equity settling in dollars.
# The execution path refuses a provider symbol; it trades the derived asset id.
AAPL = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={PROVIDER: "AAPL"})
INSTRUMENTS: InstrumentRegistry = register_instruments(InstrumentRegistry(), (AAPL,))

NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XNAS",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=INSTRUMENTS,
    provider=PROVIDER,
)


class MovingAverageCross(BaseStrategy):
    """Hold ``size`` shares while the fast average is above the slow one."""

    def __init__(self, asset_id: str, fast: int, slow: int, size: Decimal) -> None:
        self._asset_id = asset_id
        self._fast = fast
        self._slow = slow
        self._size = size
        self._closes: list[Decimal] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        self._closes.append(bar.close)
        if len(self._closes) < self._slow:
            return ()
        fast = sum(self._closes[-self._fast :]) / self._fast
        slow = sum(self._closes[-self._slow :]) / self._slow
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=self._asset_id,
                target=self._size if fast > slow else Decimal("0"),
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def context_factory(strategy_id: str) -> StrategyContext:
    """The context each strategy call receives; this strategy reads none of it."""

    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def running_strategy() -> RuntimeState:
    """Register the strategy and take it through its lifecycle to RUNNING."""

    strategy = MovingAverageCross(AAPL.asset_id, fast=3, slow=8, size=Decimal("100"))
    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    lifecycle = state.strategies[STRATEGY_ID]
    lifecycle, _ = RuntimeSupervisor.configure(lifecycle, {}, 1.0)
    lifecycle, _ = RuntimeSupervisor.initialize(lifecycle, 1.1)
    lifecycle, _ = RuntimeSupervisor.subscribe(lifecycle, frozenset({"bars"}), 1.2)
    lifecycle, _ = RuntimeSupervisor.start(lifecycle, 1.3)
    return replace(state, strategies={STRATEGY_ID: lifecycle})


def run_config(start: float) -> RunConfig:
    """Capital, limits and costs -- every one stated, none assumed."""

    ample = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX02", "USD", "Example 02", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("2")),
                margin=MarginLimit(Decimal("1.00")),
                daily_loss=DailyLossLimit(ample, "America/New_York"),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            # Half a cent a share: small, but a backtest without costs is a claim
            # nobody can trade.
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.005"))),
            instruments=INSTRUMENTS,
        ),
        seed=SEED,
        start_timestamp=start,
    )


def main() -> None:
    print("=" * 70)
    print("AlphaLab Example 02 : Your First Backtest")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Data: a CSV becomes a dataset with an identity
    # ------------------------------------------------------------------

    request = IngestionRequest(
        name="SAMPLE-OHLCV",
        source=raw_source_from_bytes(
            SourceKind.LOCAL_FILE, str(DATA), b"", 1_736_000_000.0, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        # Each bar is stamped at its close: the instant it could first be known.
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CleaningPolicy(
            duplicates=DuplicatePolicy.KEEP_FIRST,
            ordering=OrderingPolicy.SORT,
            invalid_records=InvalidRecordPolicy.DROP,
            missing_values=MissingValuePolicy.DROP_ROW,
        ),
        price_basis=PriceBasis.RAW,
    )
    dataset = ingest_csv(DATA, request).dataset
    apple = select(dataset, DataRequest(symbols=("AAPL",), price_basis=PriceBasis.RAW))
    aapl_only = replace(dataset, records=tuple(apple.records))

    print()
    print("1. Data")
    print(f"   file            : {DATA.name}, {len(dataset.records)} bars of 3 symbols")
    print(f"   dataset version : {dataset.require_provenance().dataset_version[:40]}...")
    print(f"   AAPL bars       : {len(aapl_only.records)}")

    # ------------------------------------------------------------------
    # 2. The run
    # ------------------------------------------------------------------

    first = min(record.timestamp for record in aapl_only.records)
    result = backtest(
        run_config(first - 1.0), aapl_only, running_strategy(), context_factory, NORMALIZATION
    )

    print()
    print("2. The run")
    print(f"   records processed : {result.records_processed}")
    print(f"   orders            : {len(result.orders)}")
    for fill in result.fills:
        # A fill's quantity is a magnitude; its side says which way it traded.
        print(
            f"     {fill.side.value:<4} {fill.quantity:>5} @ {fill.price}  "
            f"commission {fill.commission}"
        )

    # ------------------------------------------------------------------
    # 3. The result
    # ------------------------------------------------------------------

    valuation = result.valuation
    print()
    print("3. The result")
    print(f"   ending cash       : {valuation.cash}")
    print(f"   ending equity     : {valuation.equity}")
    print(f"   realized P&L      : {valuation.realized_pnl}")
    print(f"   unrealized P&L    : {valuation.unrealized_pnl}")
    report = result.report
    if report is not None and report.returns.total_return is not None:
        print(f"   total return      : {report.returns.total_return:+.4%}")
        print(f"   max drawdown      : {report.drawdowns.max_drawdown:.4%}")
        print(f"   closed trades     : {report.trades.closed_trades}")
    print(f"   dataset in result : {(result.dataset_id or '-')[:40]}...")
    print()
    print("The same bytes, configuration and seed give the same run, fill for fill.")


if __name__ == "__main__":
    main()
