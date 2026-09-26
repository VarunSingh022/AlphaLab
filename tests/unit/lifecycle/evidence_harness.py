"""Real datasets, real runs and real declarations for the v3.6 lifecycle tests.

Nothing here is a stub of a result. A dataset is ingested through the canonical
ingestion path so it carries provenance; a run goes through the execution path
via :func:`alphalab.api.backtest`; a specification and a fingerprint are built
from a registered-shaped ``StrategyVersion``. Only the strategy is scripted, so a
test can say exactly which order it wants at which bar.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.api import backtest, ingest_rows
from alphalab.backtesting.state import BacktestResult
from alphalab.conventions.lot import LotSpecification
from alphalab.conventions.market import MarketConvention
from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.conventions.tick import TickSchedule
from alphalab.core.enums import AssetType, OrderType, TimeInForce
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
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    BrokerCapabilities,
    BrokerRequirements,
    CapitalPolicy,
    CodeIdentity,
    DependencyManifest,
    DeploymentSpecification,
    EngineIdentity,
    MarketAvailability,
    MarketRequirements,
    ResearchConfiguration,
    RuntimeRequirements,
    StrategyFingerprint,
    Tolerance,
    dataset_assumption_from,
    fingerprint_for_version,
    research_configuration,
    source_digest,
    specification_for_version,
)
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.model_registry import ModelStage
from alphalab.risk.limits import RiskLimits
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.studio.strategy import StrategyDefinition
from tests.integration.harness import (
    START_CASH,
    context_factory,
    pipeline_config,
    running_strategy_state,
)

STRATEGY_ID = "V36-MOMENTUM"
SYMBOL = "VSX"
PROVIDER = "v36-vendor"
SEED = 360_000
RETRIEVED_AT = 1_700_000_000.0

INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: SYMBOL})
ASSET_ID = INSTRUMENT.asset_id
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))

NORMALIZATION = NormalizationPolicy(
    venue="XNYS",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=REGISTRY,
    provider=PROVIDER,
)

CLEANING = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

CLOSES = (
    Decimal("100.005"),
    Decimal("102.017"),
    Decimal("101.003"),
    Decimal("104.011"),
    Decimal("103.007"),
    Decimal("105.019"),
)

#: Bar index -> signed quantity. Buys ten, then sells four.
PLAN: Mapping[int, Decimal] = {1: Decimal("10"), 3: Decimal("-4")}


class BarStrategy(BaseStrategy):
    """Emits a scripted signed quantity at chosen bar indices."""

    def __init__(self, strategy_id: str, asset_id: str, plan: Mapping[int, Decimal]) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._plan = dict(plan)
        self._seen = 0

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        index, self._seen = self._seen, self._seen + 1
        delta = self._plan.get(index)
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


def csv_payload(rows: Sequence[Mapping[str, object]]) -> bytes:
    """The rows as the CSV bytes they stand for, header first, in row order.

    Recorded as the source payload, so a dataset's derived version names these
    rows and no others. :func:`alphalab.api.ingest_rows` records whatever
    source it is given, and a source recorded with no bytes would give every
    row set under one name the same version.
    """

    header = list(rows[0])
    lines = [",".join(header), *(",".join(str(row[key]) for key in header) for row in rows)]
    return "\n".join(lines).encode("utf-8")


def ingest(name: str = "V36-PRICES", closes: Iterable[Decimal] = CLOSES) -> Dataset:
    """A dataset with real provenance, so its derived version names real bytes."""

    rows = [
        {
            "symbol": SYMBOL,
            "timestamp": f"2024-03-{day + 1:02d} 00:00:00",
            "open": str(close * Decimal("0.999")),
            "high": str(close * Decimal("1.004")),
            "low": str(close * Decimal("0.996")),
            "close": str(close),
            "volume": 1_000_000 + day,
        }
        for day, close in enumerate(closes)
    ]
    request = IngestionRequest(
        name=name,
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "v36-tests", csv_payload(rows), RETRIEVED_AT, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
    )
    return ingest_rows(rows, request).dataset


def run_config(
    strategy_id: str = STRATEGY_ID,
    seed: int | None = SEED,
    mode: ExecutionMode = ExecutionMode.BACKTEST,
    risk_limits: RiskLimits | None = None,
) -> RunConfig:
    pipeline = replace(pipeline_config(strategy_id, risk_limits=risk_limits), instruments=REGISTRY)
    if mode is ExecutionMode.LIVE:
        pipeline = replace(pipeline, routing=ExecutionRouting.EXTERNAL)
    return RunConfig(
        pipeline=pipeline,
        mode=mode,
        seed=seed,
        start_timestamp=1.0,
        compile_analytics=mode is not ExecutionMode.LIVE,
    )


def run_backtest(
    dataset: Dataset,
    seed: int | None = SEED,
    plan: Mapping[int, Decimal] = PLAN,
    strategy_id: str = STRATEGY_ID,
    risk_limits: RiskLimits | None = None,
) -> BacktestResult:
    """One real backtest over ``dataset``, with a fresh strategy instance."""

    return backtest(
        run_config(strategy_id, seed, risk_limits=risk_limits),
        dataset,
        running_strategy_state(strategy_id, BarStrategy(strategy_id, ASSET_ID, plan)),
        context_factory,
        NORMALIZATION,
    )


DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="V3.6 Momentum",
    version="1",
    author="tests",
    description="the v3.6 capability tests",
    parameters={"entry": 10.0, "exit": -4.0},
)

VERSION = StrategyVersion(
    name="v36-momentum",
    version=1,
    definition=DEFINITION,
    stage=ModelStage.STAGING,
)

#: The limits every harness run is gated by, so a specification carrying them
#: describes what the runs were actually held to.
RISK: RiskLimits = pipeline_config(STRATEGY_ID).risk_limits

CAPITAL = CapitalPolicy("acct-v21", "USD", START_CASH, ("USD",))

BROKER_REQUIREMENTS = BrokerRequirements(
    order_types=frozenset({OrderType.MARKET}),
    time_in_force=frozenset({TimeInForce.DAY}),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=True,
)

MARKET_REQUIREMENTS = MarketRequirements((ASSET_ID,), ("XNYS",), ("XNYS",), ("USD",))

RUNTIME_REQUIREMENTS = RuntimeRequirements(
    max_data_staleness_seconds=172_800.0,
    max_heartbeat_silence_seconds=172_800.0,
    max_execution_latency_seconds=5.0,
    position_tolerance=Tolerance(absolute=Decimal("0.000001")),
)


def specification(
    dataset: Dataset,
    version: StrategyVersion = VERSION,
    risk: RiskLimits = RISK,
    market: MarketRequirements = MARKET_REQUIREMENTS,
) -> DeploymentSpecification:
    return specification_for_version(
        version,
        datasets=(dataset_assumption_from(dataset, "prices"),),
        risk=risk,
        capital=CAPITAL,
        broker=BROKER_REQUIREMENTS,
        market=market,
        runtime=RUNTIME_REQUIREMENTS,
    )


SOURCES: Mapping[str, bytes] = {
    "v36_strategies/momentum.py": b"class Momentum: ...\n",
    "v36_strategies/__init__.py": b"",
}

CODE = CodeIdentity(
    package="v36-strategies",
    version="1.0.0",
    entry_point="v36_strategies.momentum.Momentum",
    source_digest=source_digest(SOURCES),
)

ENGINE = EngineIdentity("alphalab", "3.6.0")

RESEARCH: ResearchConfiguration = research_configuration(
    {"validation": "single in-sample backtest", "costs": "ExecutionSimulator defaults"}
)


def fingerprint(
    version: StrategyVersion = VERSION,
    code: CodeIdentity = CODE,
    dependencies: DependencyManifest = NO_DEPENDENCIES,
    research: ResearchConfiguration = RESEARCH,
    engine: EngineIdentity = ENGINE,
) -> StrategyFingerprint:
    return fingerprint_for_version(version, code, dependencies, research, engine)


#: What a simulated environment accepts, declared rather than assumed.
EVERYTHING = BrokerCapabilities(
    order_types=frozenset(OrderType),
    time_in_force=frozenset(TimeInForce),
    asset_classes=frozenset(AssetType),
    short_selling=True,
    fractional_quantities=True,
)

AVAILABLE = MarketAvailability(
    instruments=frozenset({ASSET_ID}),
    venues=frozenset({"XNYS"}),
    calendar_ids=frozenset({"XNYS"}),
    quote_currencies=frozenset({"USD"}),
)


def equity_convention(multiplier: Decimal = Decimal("1")) -> MarketConvention:
    """The contract terms of the harness instrument, stated in full."""

    return MarketConvention(
        venue="XNYS",
        calendar_id="XNYS",
        quote_currency="USD",
        settlement_currency="USD",
        multiplier=multiplier,
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
    )
