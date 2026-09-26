"""Shared setup for the v3.6 strategy-evaluation examples (46-49).

All four examples evaluate the *same* strategy, and each needs the same few
dozen lines to get one: a dataset with real provenance, an instrument registry
so the run can reach a fill, a class registry that maps the strategy's identity
to its code, and a run configuration. Repeating those in four files would make
them about setup rather than about fingerprints, manifests, certification and
portability, so they live here once -- the reason ``_research_panel.py`` exists
for examples 17-24.

Nothing here fetches anything, reads a clock or mints a random identifier. The
prices are written out below, the strategy's identity is a fixed string, and
every run is seeded, so each example prints the same identities on every
machine.

The strategy's quantitative logic lives in :class:`MomentumStrategy` and reads
its sizes from the parameters it is constructed with -- the
:class:`~alphalab.studio.strategy.StrategyDefinition` parameters, handed over by
the :class:`~alphalab.strategy.registry.StrategyClassRegistry` factory. So the
parameters a fingerprint hashes are the ones that actually drive the orders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, ingest_rows
from alphalab.backtesting.state import BacktestResult
from alphalab.core.enums import AssetType
from alphalab.data.cleaning import (
    CleaningPolicy,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
)
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.dataset import Dataset
from alphalab.data.ingestion import IngestionRequest
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency
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
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView, StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy, StrategyProtocol
from alphalab.strategy.registry import StrategyClassRegistry, runtime_for
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.studio.strategy import StrategyDefinition

STRATEGY_ID = "EX-MOMENTUM"
SYMBOL = "EXM"
PROVIDER = "example-vendor"
SEED = 460_000
START_CASH = Decimal("1000000")

INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: SYMBOL})
ASSET_ID = INSTRUMENT.asset_id
INSTRUMENTS: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))

NORMALIZATION = NormalizationPolicy(
    venue="XNYS", currency="USD", timeframe=TimeFrame.D1, identity=INSTRUMENTS, provider=PROVIDER
)

CLEANING = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

#: Twelve daily closes, written out so the arithmetic can be checked by hand.
CLOSES: tuple[Decimal, ...] = tuple(
    Decimal(value)
    for value in (
        "100.00",
        "101.25",
        "100.80",
        "102.40",
        "103.10",
        "102.55",
        "104.20",
        "105.05",
        "104.30",
        "106.10",
        "107.00",
        "106.45",
    )
)

#: The strategy's parameters. ``entry`` is bought on the third bar and ``exit``
#: sold on the eighth -- the whole of its quantitative logic.
DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="Example Momentum",
    version="1",
    author="examples",
    description="buys on the third bar and trims on the eighth",
    parameters={"entry": 1000.0, "exit": -400.0},
)

#: The pre-trade limits every run in these examples is gated by.
#:
#: The position quantity cap is wide on purpose. The gate's position check adds
#: an asset's *notional* exposure to an order's *quantity* before comparing
#: with this cap (recorded in ADR-0041), so a cap near the real share count
#: would refuse this strategy's sell for a reason that has nothing to do with
#: its position.
LIMITS = RiskLimits(
    order_size=OrderSizeLimit(Decimal("5000"), Decimal("1000000")),
    position=PositionLimit(Decimal("1000000"), Decimal("2000000")),
    exposure=ExposureLimit(Decimal("1500000"), Decimal("1500000")),
    leverage=LeverageLimit(Decimal("1.5")),
    margin=MarginLimit(Decimal("1.00")),
    daily_loss=DailyLossLimit(Decimal("50000")),
    drawdown=DrawdownLimit(Decimal("0.10")),
)


class MomentumStrategy(BaseStrategy):
    """Buys ``entry`` on the third bar and sells ``exit`` on the eighth.

    Holds one counter, which is why a fresh instance is built for every run:
    reusing one would carry its count into the next run -- exactly the hidden
    state example 48 shows certification catching.
    """

    def __init__(self, strategy_id: str, parameters: Mapping[str, float]) -> None:
        self._strategy_id = strategy_id
        self._plan = {
            2: Decimal(str(parameters["entry"])),
            7: Decimal(str(parameters["exit"])),
        }
        self._seen = 0

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        index, self._seen = self._seen, self._seen + 1
        delta = self._plan.get(index)
        if delta is None:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=ASSET_ID,
                target=delta,
                timestamp=event.bar.timestamp,
            ),
        )


def momentum_factory(strategy_id: str, parameters: Mapping[str, float], /) -> StrategyProtocol:
    """How the class registry builds the strategy from its declared parameters."""

    return MomentumStrategy(strategy_id, parameters)


#: The one mapping from the strategy's identity to the code that runs it.
CLASSES = StrategyClassRegistry().register(STRATEGY_ID, momentum_factory)

#: The strategy's source files, relative to where they live -- here, this file.
SOURCES: Mapping[str, bytes] = {"_strategy_evidence.py": Path(__file__).read_bytes()}


def _csv(rows: Sequence[Mapping[str, object]]) -> bytes:
    header = list(rows[0])
    lines = [",".join(header), *(",".join(str(row[key]) for key in header) for row in rows)]
    return "\n".join(lines).encode("utf-8")


def ingest_prices(name: str = "EX-PRICES", closes: Sequence[Decimal] = CLOSES) -> Dataset:
    """A dataset with real provenance, ingested from rows written in this file."""

    rows = [
        {
            "symbol": SYMBOL,
            "timestamp": f"2024-06-{day + 1:02d} 00:00:00",
            "open": str(close),
            "high": str(close + Decimal("0.50")),
            "low": str(close - Decimal("0.50")),
            "close": str(close),
            "volume": 500_000 + day,
        }
        for day, close in enumerate(closes)
    ]
    request = IngestionRequest(
        name=name,
        # The rows as the CSV bytes they stand for: a dataset version is derived
        # from the recorded source bytes, so recording none would give every row
        # set under this name the same version.
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "examples", _csv(rows), 1_717_200_000.0, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
    )
    return ingest_rows(rows, request).dataset


def context_factory(strategy_id: str) -> StrategyContext:
    """The context each strategy is dispatched with. It reads nothing here."""

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


def running(definition: StrategyDefinition = DEFINITION) -> RuntimeState:
    """A fresh strategy instance, built by the class registry and started."""

    state = runtime_for(CLASSES, (definition,))
    strategy = state.strategies[definition.strategy_id]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={definition.strategy_id: strategy})


def run_config(
    seed: int | None = SEED,
    mode: ExecutionMode = ExecutionMode.BACKTEST,
    simulator: ExecutionSimulator | None = None,
    risk: RiskLimits = LIMITS,
) -> RunConfig:
    """The configuration every run here is driven with."""

    return RunConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX", "USD", "Examples 46-49", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH * Decimal("2"),
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=risk,
            simulator=simulator if simulator is not None else ExecutionSimulator(),
            instruments=INSTRUMENTS,
        ),
        mode=mode,
        seed=seed,
        start_timestamp=1.0,
    )


def run_backtest(
    dataset: Dataset,
    seed: int | None = SEED,
    definition: StrategyDefinition = DEFINITION,
    simulator: ExecutionSimulator | None = None,
) -> BacktestResult:
    """One real backtest through the execution path, with a fresh strategy."""

    return backtest(
        run_config(seed, simulator=simulator),
        dataset,
        running(definition),
        context_factory,
        NORMALIZATION,
    )


def banner(number: int, title: str) -> None:
    print("=" * 68)
    print(f"AlphaLab Example {number} : {title}")
    print("=" * 68)
