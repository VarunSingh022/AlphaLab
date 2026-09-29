"""
AlphaLab Examples
=================

Example 09 : Cross-Sectional Decisions on Complete Instants

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 08 (rebalancing to target weights)

Topics
------

• Slices: every record of an instant, delivered once (v3.11, ledger EXE-004)
• Subscriptions: what a strategy is dispatched (v3.11, ledger EXE-007)
• Ranking a universe without mixing two instants
• When a slice's orders execute

What this shows
---------------

A strategy that trades several instruments is dispatched once per *record*.
Three bars share each day's close here -- Apple's, Microsoft's and the S&P
500 ETF's -- and they arrive one after another, so at Apple's bar the other two
prices are still yesterday's. A ranking made there compares today with
yesterday.

A **slice** is the instant complete. After the last record carrying a
timestamp, a strategy subscribed to ``slices`` that defines ``on_slice`` is
called once, with every asset of that instant. This one keeps each asset's
closes from its bars and, at each slice, holds only the asset with the best
five-day return -- 95% of its capital there and nothing elsewhere.

What a slice asks for rests until each asset's next record: the ranking is
made on the close, so it trades on the next day's bar. Nothing is filled at a
price the decision had already seen, which is the honest reading of "decided
at the close".

A rotation is two steps, because the account is a cash account: a purchase is
paid for with cash it holds, and the proceeds of a sale that has not executed
are not spent in advance -- the risk gate refuses such a purchase for want of
buying power. So the strategy sells on one close and buys on the next, once the
sale has filled. (Allocation itself no longer stands in the way: since v3.11 a
sale that reduces a position commits no budget, so the sale is never refused
alongside the purchase it would fund; ledger ALC-007.)

(Until v3.11 this file demonstrated ``alphalab.workbench``, UI state outside
AlphaLab's boundary that was removed in v3.11; ledger BND-003.)

Run

    python examples/09_workbench.py
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, ingest_csv
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
from alphalab.strategy import SliceClosed
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

DATA = Path(__file__).parent / "data" / "sample_ohlcv.csv"
PROVIDER = "sample-csv"
STRATEGY_ID = "EX09-ROTATION"
CAPITAL = Decimal("100000.00")
SEED = 20_250_109
LOOKBACK = 5
INVESTED = Decimal("0.95")

RECORDS = (
    InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={PROVIDER: "AAPL"}),
    InstrumentRecord("MSFT", AssetType.EQUITY, "XNAS", "USD", aliases={PROVIDER: "MSFT"}),
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


class Rotation(BaseStrategy):
    """Holds the asset with the best trailing return, decided on complete instants."""

    def __init__(self) -> None:
        self._closes: dict[str, list[Decimal]] = {}
        self._held: str | None = None
        self._next: str | None = None
        self.decisions: list[tuple[float, str, dict[str, Decimal]]] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        # Bars only feed the history; no decision is made on part of an instant.
        self._closes.setdefault(event.bar.asset_id, []).append(event.bar.close)
        return ()

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        if self._next is not None:
            # Yesterday's sale filled on today's bar: its cash buys the new leader.
            self._held, self._next = self._next, None
            return (self._buy(self._held, event.timestamp),)
        histories = {asset: self._closes.get(asset, []) for asset in event.assets}
        if len(histories) < len(RECORDS) or any(
            len(closes) <= LOOKBACK for closes in histories.values()
        ):
            return ()
        returns = {
            asset: closes[-1] / closes[-1 - LOOKBACK] - 1 for asset, closes in histories.items()
        }
        best = max(sorted(returns), key=lambda asset: returns[asset])
        if best == self._held:
            return ()
        self.decisions.append((event.timestamp, best, returns))
        if self._held is None:
            self._held = best
            return (self._buy(best, event.timestamp),)
        # Sell first; buy at the next close, with the cash the sale raised.
        sale = Intent(
            strategy_id=STRATEGY_ID,
            instrument=self._held,
            target=Decimal("0"),
            timestamp=event.timestamp,
            kind=IntentKind.TARGET_QUANTITY,
        )
        self._held, self._next = None, best
        return (sale,)

    @staticmethod
    def _buy(asset_id: str, timestamp: float) -> Intent:
        return Intent(
            strategy_id=STRATEGY_ID,
            instrument=asset_id,
            target=INVESTED,
            timestamp=timestamp,
            kind=IntentKind.TARGET_WEIGHT,
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


def running(strategy: Rotation) -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    lifecycle = state.strategies[STRATEGY_ID]
    lifecycle, _ = RuntimeSupervisor.configure(lifecycle, {}, 1.0)
    lifecycle, _ = RuntimeSupervisor.initialize(lifecycle, 1.1)
    # Bars to keep the history, slices to decide on: nothing else is dispatched.
    lifecycle, _ = RuntimeSupervisor.subscribe(lifecycle, frozenset({"bars", "slices"}), 1.2)
    lifecycle, _ = RuntimeSupervisor.start(lifecycle, 1.3)
    return replace(state, strategies={STRATEGY_ID: lifecycle})


def run_config(start: float) -> RunConfig:
    ample = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX09", "USD", "Example 09", 1.0),
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


def _day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")


def main() -> None:
    print("=" * 74)
    print("AlphaLab Example 09 : Cross-Sectional Decisions on Complete Instants")
    print("=" * 74)

    dataset = ingest()
    strategy = Rotation()
    first = min(record.timestamp for record in dataset.records)
    result = backtest(
        run_config(first - 1.0), dataset, running(strategy), context_factory, NORMALIZATION
    )

    print()
    print(f"Rotations (best {LOOKBACK}-day return, decided at each day's complete close)")
    for timestamp, best, returns in strategy.decisions:
        ranked = "  ".join(
            f"{SYMBOL_OF[asset]} {value:+.2%}" for asset, value in sorted(returns.items())
        )
        print(f"   {_day(timestamp)}  ->  {SYMBOL_OF[best]:<5} ({ranked})")

    print()
    print("Fills (each on the day after the close that asked for it)")
    for fill in result.fills:
        print(
            f"   {_day(fill.filled_at)}  {SYMBOL_OF[str(fill.asset_id)]:<5} "
            f"{fill.side.value:<4} {fill.quantity:>4} @ {fill.price}"
        )

    print()
    held = {
        SYMBOL_OF[asset]: position.quantity
        for asset, position in result.state.portfolio.positions.items()
    }
    print(f"Holding at the end : {held}")
    print(f"Ending equity      : {result.valuation.equity}")


if __name__ == "__main__":
    main()
