"""
AlphaLab Examples
=================

Example 08 : Rebalancing to Target Weights

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 02 (your first backtest)

Topics
------

• Target-weight intents (v3.11, ledger FEA-001)
• A strategy's own positions, kept by allocation
• Lot sizes declared on the instrument, and rounding toward zero
• Why a rebalancer states a weight when it means to rebalance

What this shows
---------------

A strategy that owns a three-name book and changes its mind half way through.
It never computes an order. On a rebalance date it states the weight it wants
in the asset each bar is for -- a fraction of its capital -- and allocation
turns the difference between that target and what the strategy already holds
(its own share of every fill, plus anything still working) into an order:

    target quantity = weight * strategy capital / price
    order           = target - held - working, rounded toward zero

Microsoft is declared to trade in lots of five shares, so its orders are whole
lots and its position stops short of the weight rather than overshooting it.

A weight is a *moving* quantity: the same weight at a new price is a different
number of shares, so stating it on every bar would trade a share or two
whenever the price moved the target across one. A rebalancer therefore states
its weights when it means to rebalance -- here on the first bar and the
sixteenth -- and is silent in between. (A target *quantity* is different:
stating the same one again asks for nothing.)

The strategy's capital is its budget in the allocation configuration -- a
stated figure, not its equity -- so a weight means the same number of dollars
on the last bar as on the first.

(Until v3.11 this file demonstrated ``alphalab.studio``, a record-keeping
package outside AlphaLab's boundary that was removed in v3.11; ledger SCF-001.)

Run

    python examples/08_strategy_studio.py
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, ingest_csv
from alphalab.backtesting import ExecutionMode, RunConfig
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
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
STRATEGY_ID = "EX08-REBALANCER"
CAPITAL = Decimal("100000.00")
SEED = 20_250_108

WHOLE_LOTS_OF_FIVE = InstrumentEconomics(
    Decimal("1"),
    SettlementModel.CASH_EQUITY,
    lot=LotSpecification(lot_size=Decimal("5"), minimum_quantity=Decimal("5")),
)
RECORDS = (
    InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={PROVIDER: "AAPL"}),
    InstrumentRecord(
        "MSFT",
        AssetType.EQUITY,
        "XNAS",
        "USD",
        aliases={PROVIDER: "MSFT"},
        economics=WHOLE_LOTS_OF_FIVE,
    ),
    InstrumentRecord("SPY", AssetType.EQUITY, "ARCX", "USD", aliases={PROVIDER: "SPY"}),
)
INSTRUMENTS: InstrumentRegistry = register_instruments(InstrumentRegistry(), RECORDS)
SYMBOL_OF = {record.asset_id: record.symbol for record in RECORDS}

NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="SIM",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=INSTRUMENTS,
    provider=PROVIDER,
)

#: Two books: growth-heavy for the first fifteen bars, index-heavy after. Each
#: leaves five percent in cash for commissions.
FIRST = {"AAPL": Decimal("0.45"), "MSFT": Decimal("0.30"), "SPY": Decimal("0.20")}
SECOND = {"AAPL": Decimal("0.20"), "MSFT": Decimal("0.30"), "SPY": Decimal("0.45")}
SWITCH_AT_BAR = 15


class Rebalancer(BaseStrategy):
    """States each asset's target weight on the two rebalance dates."""

    def __init__(self) -> None:
        self._bars: dict[str, int] = {}
        self.intents = 0

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        seen = self._bars.get(bar.asset_id, 0)
        self._bars[bar.asset_id] = seen + 1
        if seen not in (0, SWITCH_AT_BAR):
            return ()
        weights = FIRST if seen == 0 else SECOND
        self.intents += 1
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=bar.asset_id,
                target=weights[SYMBOL_OF[bar.asset_id]],
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_WEIGHT,
            ),
        )


def context_factory(strategy_id: str) -> StrategyContext:
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


def running(strategy: Rebalancer) -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    lifecycle = state.strategies[STRATEGY_ID]
    lifecycle, _ = RuntimeSupervisor.configure(lifecycle, {}, 1.0)
    lifecycle, _ = RuntimeSupervisor.initialize(lifecycle, 1.1)
    lifecycle, _ = RuntimeSupervisor.subscribe(lifecycle, frozenset({"bars"}), 1.2)
    lifecycle, _ = RuntimeSupervisor.start(lifecycle, 1.3)
    return replace(state, strategies={STRATEGY_ID: lifecycle})


def run_config(start: float) -> RunConfig:
    ample = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX08", "USD", "Example 08", 1.0),
            starting_cash=CAPITAL,
            budget=CapitalBudget(
                global_capital=CAPITAL,
                maximum_exposure=CAPITAL,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: CAPITAL},
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
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.005"))),
            instruments=INSTRUMENTS,
        ),
        seed=SEED,
        start_timestamp=start,
    )


def ingest() -> Any:
    request = IngestionRequest(
        name="SAMPLE-OHLCV",
        source=raw_source_from_bytes(
            SourceKind.LOCAL_FILE, str(DATA), b"", 1_736_000_000.0, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
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
    return ingest_csv(DATA, request).dataset


def _row(symbol: str, quantity: Decimal, price: Decimal, target: Mapping[str, Decimal]) -> str:
    value = quantity * price
    return (
        f"   {symbol:<5} {quantity:>6}  @ {price:>7}  = {value:>10.2f}"
        f"   weight {value / CAPITAL:6.2%}  (target {target[symbol]:.0%})"
    )


def main() -> None:
    print("=" * 74)
    print("AlphaLab Example 08 : Rebalancing to Target Weights")
    print("=" * 74)

    dataset = ingest()
    strategy = Rebalancer()
    first = min(record.timestamp for record in dataset.records)
    result = backtest(
        run_config(first - 1.0), dataset, running(strategy), context_factory, NORMALIZATION
    )

    print()
    print(f"Intents stated : {strategy.intents} (three names, two rebalance dates)")
    print(f"Orders placed  : {len(result.orders)}")
    print()
    print("Fills")
    for fill in result.fills:
        print(
            f"   {SYMBOL_OF[str(fill.asset_id)]:<5} {fill.side.value:<4} {fill.quantity:>5}"
            f" @ {fill.price}"
        )

    portfolio = result.state.portfolio
    prices = result.state.market_prices
    print()
    print(f"Book after the switch at bar {SWITCH_AT_BAR + 1} (capital {CAPITAL})")
    for asset_id, position in portfolio.positions.items():
        print(_row(SYMBOL_OF[asset_id], position.quantity, prices[asset_id], SECOND))

    msft = next(p for a, p in portfolio.positions.items() if SYMBOL_OF[a] == "MSFT")
    print()
    print(f"MSFT holds {msft.quantity} shares: whole lots of five, below its weight, never above.")
    print(f"Ending equity : {result.valuation.equity}")


if __name__ == "__main__":
    main()
