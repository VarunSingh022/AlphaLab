"""
AlphaLab Examples
=================

Example 16 : Research and Backtest From a Canonical Dataset

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 11 (the unified execution path)
✓ Example 15 (universal data ingestion)

Topics
------

• Requesting research data by dataset version
• Point-in-time selection
• Normalizing wire records into canonical domain records
• Running a backtest from an ingested dataset
• Exact dataset lineage, from bytes to result

What this shows
---------------

The join that makes a result traceable::

    CSV bytes
      -> canonical Dataset        (identity derived from content + config)
      -> MarketDataset            (the SAME identity as its dataset_id)
      -> RunState.source_id
      -> BacktestResult.dataset_id

A run therefore names the exact bytes, schema, policy and transformations it
was measured on. Nothing about the evidence digest changed to make this work:
``dataset_id`` was always hashed into ``ValidationEvidence``; v3.1 simply made
it a *derived* identity rather than a string somebody typed.

Run

    python examples/16_research_from_dataset.py
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import DataRequest, backtest, ingest_csv, select, to_market_dataset
from alphalab.backtesting import ExecutionMode, RunConfig
from alphalab.core.enums import AssetType
from alphalab.data import (
    CleaningPolicy,
    DataAssetClass,
    DuplicatePolicy,
    EquitySpec,
    IngestionRequest,
    InvalidRecordPolicy,
    MarketCalendar,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    SessionWindow,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.feed import Bar as WireBar
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.identity import derive_asset_id
from alphalab.market.bar import Bar as DomainBar
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy, SymbolMap, UnresolvedIdentity
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
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

DATA = Path(__file__).parent / "data" / "sample_ohlcv.csv"
RETRIEVED_AT = 1_726_000_000.0
STRATEGY_ID = str(uuid4())
SYMBOL = "AAPL"
START_CASH = Decimal("100000.00")
SEED = 20260920

NYSE = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions={day: (SessionWindow(time(9, 30), time(16, 0)),) for day in range(5)},
)

POLICY = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)


class BuyAndTrim(BaseStrategy):
    """Buys on the second bar and trims on the fifth.

    Deliberately simple: the example is about where the data came from, not
    about the decision.
    """

    def __init__(self, strategy_id: str, asset_id: str, schedule: dict[int, Decimal]) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._schedule = schedule
        self._seen = 0

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self._seen += 1
        delta = self._schedule.get(self._seen)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=delta,
                timestamp=event.bar.timestamp,
            ),
        )


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


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


def running_strategy(asset_id: str) -> RuntimeState:
    """Take one strategy through its lifecycle to RUNNING."""

    strategy = BuyAndTrim(STRATEGY_ID, asset_id, {2: Decimal("100"), 5: Decimal("-40")})
    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    strategy_state = state.strategies[STRATEGY_ID]
    strategy_state, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
    strategy_state, _ = RuntimeSupervisor.initialize(strategy_state, 1.1)
    strategy_state, _ = RuntimeSupervisor.subscribe(strategy_state, frozenset({"bars"}), 1.2)
    strategy_state, _ = RuntimeSupervisor.start(strategy_state, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy_state})


def build_config(start_timestamp: float) -> RunConfig:
    huge = Decimal("100000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("acct-example-16", "USD", "Example Account", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
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
                daily_loss=DailyLossLimit(huge),
                drawdown=DrawdownLimit(Decimal("1.00")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.005"))),
        ),
        seed=SEED,
        start_timestamp=start_timestamp,
    )


def main() -> None:
    """Ingest a CSV, research it, and backtest it -- naming the data throughout."""

    print("=" * 74)
    print("AlphaLab Example 16 : Research and Backtest From a Canonical Dataset")
    print("=" * 74)

    # ------------------------------------------------------------------
    # Step 08 : Research on a dataset
    # ------------------------------------------------------------------
    #
    # The dataset is ingested once and then *narrowed*. A research function is
    # never handed a way to fetch its own data: two functions that could fetch
    # independently could disagree about what they measured, and no result
    # would be comparable with any other.

    request = IngestionRequest(
        name="AAPL-1D",
        source=raw_source_from_bytes(
            SourceKind.LOCAL_FILE, str(DATA), b"", RETRIEVED_AT, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=POLICY,
        price_basis=PriceBasis.RAW,
        calendar=NYSE,
        instrument=EquitySpec(symbol=SYMBOL, currency="USD", exchange="XNAS"),
    )
    dataset = ingest_csv(DATA, request).dataset

    print()
    print("Step 08 - Research data access")
    version = dataset.require_provenance().dataset_version
    print(f"  dataset version : {version[:56]}...")
    print(f"  records         : {len(dataset.records)}")

    one_name = select(dataset, DataRequest(symbols=(SYMBOL,), price_basis=PriceBasis.RAW))
    print(f"  {SYMBOL} only      : {len(one_name)} records")
    named = one_name.dataset_version or "-"
    print(f"  selection names : {named[:56]}...")

    stamps = sorted(record.timestamp for record in one_name.records)
    cutoff = stamps[len(stamps) // 2]
    as_of = select(dataset, DataRequest(symbols=(SYMBOL,), as_of=cutoff))
    print(f"  point-in-time   : {len(as_of)} of {len(one_name)} records knowable at {cutoff}")

    bars = [record for record in one_name.records if isinstance(record, WireBar)]
    closes = [bar.close for bar in sorted(bars, key=lambda bar: bar.timestamp)]
    total_return = (closes[-1] / closes[0]) - 1.0
    print(f"  total return    : {total_return:+.2%} over the selected window")

    # ------------------------------------------------------------------
    # Step 09 : Backtest from the canonical dataset
    # ------------------------------------------------------------------
    #
    # `to_market_dataset` lifts the wire records into canonical domain records
    # -- float to Decimal, provider symbol to asset_id -- through
    # `alphalab.market.normalization`, the one wire/domain boundary AlphaLab
    # has. It then hands the dataset's DERIVED VERSION to MarketDataset as its
    # dataset_id, which is what carries the identity into the run.

    # The canonical asset_id is *derived* from the declared instrument, not
    # invented: uuid5 over the instrument key, so two independently configured
    # environments agree on the identity of the same instrument with no shared
    # database. The execution path refuses anything that is not UUID-shaped
    # (ADR-0016), which is what stops a provider symbol reaching a fill.
    instrument = EquitySpec(symbol=SYMBOL, currency="USD", exchange="XNAS")
    asset_id = derive_asset_id(
        AssetType.EQUITY, instrument.exchange, instrument.symbol, instrument.currency
    )

    normalization = NormalizationPolicy(
        venue="XNAS",
        currency="USD",
        timeframe=TimeFrame.D1,
        identity=UnresolvedIdentity(SymbolMap({SYMBOL: asset_id})),
    )

    aapl_only = replace(dataset, records=tuple(one_name.records))
    market_dataset = to_market_dataset(aapl_only, normalization)

    print()
    print("Step 09 - Backtest from the canonical dataset")
    print(f"  market records  : {len(market_dataset)}")
    payload = market_dataset.records[0].payload
    price_type = type(payload.close).__name__ if isinstance(payload, DomainBar) else "-"
    print(f"  price type      : {price_type}")
    print(f"  asset id        : {market_dataset.records[0].asset_id}")
    print("                    (uuid5 of AssetType.EQUITY/XNAS/AAPL/USD -- derived, not minted)")

    result = backtest(
        build_config(market_dataset.start_time),
        aapl_only,
        running_strategy(asset_id),
        context_factory,
        normalization,
    )

    valuation = result.valuation

    print()
    print("  Run")
    print(f"    records processed : {result.records_processed}")
    print(f"    fills             : {len(result.fills)}")
    print(f"    ending cash       : {valuation.cash}")
    print(f"    ending equity     : {valuation.equity}")
    print(f"    realized P&L      : {valuation.realized_pnl}")

    # ------------------------------------------------------------------
    # The lineage, end to end
    # ------------------------------------------------------------------

    provenance = dataset.require_provenance()

    print()
    print("  Lineage")
    print(f"    bytes            : {provenance.content_hash[:48]}...")
    print(f"    dataset version  : {version[:48]}...")
    print(f"    market dataset   : {market_dataset.dataset_id[:48]}...")
    print(f"    run source_id    : {result.run.source_id[:48] if result.run.source_id else '-'}...")
    print(f"    result names     : {result.dataset_id[:48] if result.dataset_id else '-'}...")
    print()
    print(
        f"    identical        : "
        f"{result.dataset_id == market_dataset.dataset_id == dataset.dataset_version}"
    )
    print()
    print("  The result names the exact bytes it was measured on. A second run over")
    print("  a re-downloaded but unchanged file reproduces the same identity; a run")
    print("  over an edited file cannot, and its evidence would not verify.")


if __name__ == "__main__":
    main()
