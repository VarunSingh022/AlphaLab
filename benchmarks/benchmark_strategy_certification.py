"""High-performance benchmark suite for the v3.6 strategy-evaluation paths.

Six computational paths, each on a workload a real application produces:

* **Fingerprinting** -- a strategy with a large parameter set, a long lock file
  and many research settings, fingerprinted and verified repeatedly, as a
  catalogue re-verifies every version it holds.
* **Source digests** -- a strategy package of many files.
* **Reproducibility manifests** -- a real backtest's complete canonical record
  digested and joined to its dataset, fingerprint and engine; then verified,
  and assessed against a rerun.
* **Certification** -- all eight properties over a real run, repeated runs, a
  manifest and its rerun, runtime observations and resource figures.
* **Repeated certification** -- the same report produced again, as a consumer
  re-checking a stored report does.
* **Portability** -- one strategy against many declared environments over many
  instruments.

Each path is measured at two sizes, so the printed ops/sec is readable *and*
the scaling is visible. ``tests/regression/test_v36_complexity.py`` asserts the
growth ratios; this prints the absolute numbers those ratios are made of.

Every run here is real -- ingested with provenance, driven through the execution
path -- and built before its timer starts, so what is timed is the v3.6 code and
not the backtest.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, ingest_rows
from alphalab.backtesting.state import BacktestResult
from alphalab.broker.state import ConnectionStatus
from alphalab.conventions import (
    LotSpecification,
    MarketConvention,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
)
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
    CertificationEvidence,
    CodeIdentity,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    EngineIdentity,
    MarketAvailability,
    MarketRequirements,
    MeasurementBasis,
    ReproducibilityManifest,
    ResourceBudget,
    ResourceMeasurement,
    ResourceMetric,
    RuntimeObservation,
    RuntimeProfile,
    RuntimeRequirements,
    TargetEnvironment,
    Tolerance,
    assess_reproducibility,
    build_fingerprint,
    build_specification,
    certify_strategy,
    dataset_assumption_from,
    digest_run,
    evaluate_portability,
    manifest_for_run,
    research_configuration,
    source_digest,
    verify_fingerprint,
    verify_manifest,
)
from alphalab.lifecycle.identity import StrategyVersionRef
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
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

# --------------------------------------------------------------------------- #
# Shared declarations
# --------------------------------------------------------------------------- #

STRATEGY_ID = "BENCH-MOMENTUM"
SYMBOL = "BNC"
PROVIDER = "bench-vendor"
SEED = 360_360
START = datetime(2020, 1, 1, tzinfo=UTC)
START_CASH = Decimal("1000000")
ENGINE = EngineIdentity("alphalab", "3.6.0")

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
LIMITS = RiskLimits(
    order_size=OrderSizeLimit(Decimal("100000"), Decimal("100000000")),
    position=PositionLimit(Decimal("100000000"), Decimal("100000000")),
    exposure=ExposureLimit(Decimal("100000000"), Decimal("100000000")),
    leverage=LeverageLimit(Decimal("1000")),
    margin=MarginLimit(Decimal("1.00")),
    daily_loss=DailyLossLimit(Decimal("100000000")),
    drawdown=DrawdownLimit(Decimal("1.00")),
)
PARAMETERS = {"entry": 1.0, "exit": -1.0}


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


class _Alternating(BaseStrategy):
    """Buys one unit every twentieth bar and sells one every tenth otherwise."""

    def __init__(self) -> None:
        self._seen = 0

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        index, self._seen = self._seen, self._seen + 1
        if index == 0 or index % 10:
            return ()
        target = Decimal("1") if index % 20 == 0 else Decimal("-1")
        return (Intent(STRATEGY_ID, ASSET_ID, target, event.bar.timestamp),)


def _running() -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, _Alternating())
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


def _config() -> RunConfig:
    return RunConfig(
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-BENCH", "USD", "benchmark", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH * Decimal("10"),
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=False
            ),
            risk_limits=LIMITS,
            instruments=INSTRUMENTS,
        ),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        start_timestamp=1.0,
    )


def _dataset(days: int) -> Dataset:
    rows = [
        {
            "symbol": SYMBOL,
            "timestamp": (START + timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": f"{100 + (day % 9):.2f}",
            "high": f"{101 + (day % 9):.2f}",
            "low": f"{99 + (day % 9):.2f}",
            "close": f"{100.5 + (day % 9):.2f}",
            "volume": 10_000 + day,
        }
        for day in range(days)
    ]
    header = list(rows[0])
    payload = "\n".join(
        [",".join(header), *(",".join(str(row[key]) for key in header) for row in rows)]
    ).encode("utf-8")
    request = IngestionRequest(
        name=f"BENCH-{days}",
        source=raw_source_from_bytes(SourceKind.IN_MEMORY, "benchmark", payload, 1.0, "text/csv"),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
    )
    return ingest_rows(rows, request).dataset


def _run(dataset: Dataset) -> BacktestResult:
    return backtest(_config(), dataset, _running(), _context, NORMALIZATION)


def _timed(label: str, count: int, work: Callable[[], object]) -> None:
    start = time.perf_counter()
    work()
    duration = time.perf_counter() - start
    print(f"  {label:<52} {duration:.4f}s, {count / max(duration, 1e-9):>12,.0f} ops/sec")


def _fingerprint_inputs(size: int) -> tuple[Any, ...]:
    pins = tuple(DependencyPin(f"package-{index:05d}", f"1.{index}.0") for index in range(size))
    return (
        "bench",
        STRATEGY_ID,
        CodeIdentity("bench-strategies", "1.0.0", "bench.Momentum", None),
        DependencyManifest(DependencyCompleteness.EXACT_CLOSURE, pins),
        {f"p{index:05d}": float(index) for index in range(size)},
        research_configuration({f"s{index:05d}": f"setting {index}" for index in range(size)}),
        ENGINE,
    )


def _specification(dataset: Dataset, instruments: tuple[str, ...]) -> Any:
    return build_specification(
        strategy=StrategyVersionRef("bench", 1),
        parameters=PARAMETERS,
        datasets=(dataset_assumption_from(dataset, "prices"),),
        risk=LIMITS,
        capital=CapitalPolicy("ACC-BENCH", "USD", START_CASH, ("USD",)),
        broker=BrokerRequirements(
            order_types=frozenset({OrderType.MARKET}),
            time_in_force=frozenset({TimeInForce.DAY}),
            asset_classes=frozenset({AssetType.EQUITY}),
            short_selling=True,
            fractional_quantities=True,
        ),
        market=MarketRequirements(instruments, ("XNYS",), ("XNYS",), ("USD",)),
        runtime=RuntimeRequirements(172_800.0, 172_800.0, 5.0, Tolerance(absolute=Decimal("0"))),
    )


def _fingerprint() -> Any:
    return build_fingerprint(
        "bench",
        STRATEGY_ID,
        CodeIdentity("bench-strategies", "1.0.0", "bench.Momentum", "a" * 64),
        NO_DEPENDENCIES,
        PARAMETERS,
        research_configuration({"validation": "benchmark"}),
        ENGINE,
    )


# --------------------------------------------------------------------------- #
# 1. Fingerprints
# --------------------------------------------------------------------------- #


def benchmark_fingerprints() -> None:
    print("\n[1] Strategy fingerprints")

    for size in (100, 1_000):

        def build(inputs: tuple[Any, ...] = _fingerprint_inputs(size)) -> object:
            return [verify_fingerprint(build_fingerprint(*inputs)) for _ in range(200)]

        _timed(f"build + verify x200 ({size:,} params, pins, settings)", 200, build)

    small = _fingerprint_inputs(20)
    _timed(
        "repeated fingerprinting x20,000 (20 of each)",
        20_000,
        lambda: [build_fingerprint(*small) for _ in range(20_000)],
    )

    for files in (100, 1_000):

        def digest(
            sources: Mapping[str, bytes] = {
                f"bench/module_{index:05d}.py": b"x = 1\n" * 40 for index in range(files)
            },
        ) -> object:
            return [source_digest(sources) for _ in range(50)]

        _timed(f"source_digest x50 ({files:,} files)", 50, digest)


# --------------------------------------------------------------------------- #
# 2. Reproducibility manifests
# --------------------------------------------------------------------------- #


def benchmark_manifests() -> None:
    print("\n[2] Reproducibility manifests")
    fingerprint = _fingerprint()

    for days in (250, 1_000):
        dataset = _dataset(days)
        result, rerun_result = _run(dataset), _run(dataset)
        manifest = manifest_for_run(result, dataset, fingerprint, ENGINE)
        rerun = manifest_for_run(rerun_result, dataset, fingerprint, ENGINE)

        def digest(result: BacktestResult = result) -> object:
            return digest_run(result)

        def build(result: BacktestResult = result, dataset: Dataset = dataset) -> object:
            return manifest_for_run(result, dataset, fingerprint, ENGINE)

        def verify(manifest: ReproducibilityManifest = manifest) -> object:
            return [verify_manifest(manifest) for _ in range(10_000)]

        def assess(
            manifest: ReproducibilityManifest = manifest, rerun: ReproducibilityManifest = rerun
        ) -> object:
            return [assess_reproducibility(manifest, rerun) for _ in range(2_000)]

        _timed(f"digest_run over {days:,} records", 1, digest)
        _timed(f"manifest_for_run over {days:,} records", 1, build)
        _timed(f"verify_manifest x10,000 ({days:,}-record run)", 10_000, verify)
        _timed(f"assess_reproducibility with a rerun x2,000 ({days:,})", 2_000, assess)


# --------------------------------------------------------------------------- #
# 3. Certification
# --------------------------------------------------------------------------- #


def benchmark_certification() -> None:
    print("\n[3] Certification")
    fingerprint = _fingerprint()
    budgets = (
        ResourceBudget(ResourceMetric.RECORDS_PROCESSED, Decimal("100000")),
        ResourceBudget(ResourceMetric.CPU_SECONDS, Decimal("60")),
    )
    measured = ResourceMeasurement(
        ResourceMetric.CPU_SECONDS,
        Decimal("0.5"),
        MeasurementBasis.MEASURED,
        "process time",
        "benchmark host",
    )

    for days in (250, 1_000):
        dataset = _dataset(days)
        specification = _specification(dataset, (ASSET_ID,))
        result = _run(dataset)
        manifest = manifest_for_run(result, dataset, fingerprint, ENGINE)
        rerun = manifest_for_run(_run(dataset), dataset, fingerprint, ENGINE)
        observations = tuple(
            RuntimeObservation(
                observed_at=1_000.0 + index,
                last_market_data_at=1_000.0 + index,
                last_heartbeat_at=1_000.0 + index,
                broker_connection=ConnectionStatus.CONNECTED,
                executions=(),
                observed_positions={},
                expected_positions={},
                risk_violations=(),
                state_expectations=(),
            )
            for index in range(100)
        )
        full = CertificationEvidence(
            runs=(result,),
            repeated_runs=(result, rerun_result := _run(dataset)),
            datasets=(dataset,),
            manifest=manifest,
            rerun=rerun,
            observations=observations,
            measurements=(measured,),
        )
        observed = CertificationEvidence(runs=(result, rerun_result), datasets=(dataset,))

        def certify_all(specification: Any = specification, full: Any = full) -> object:
            return certify_strategy(fingerprint, specification, full, budgets)

        def certify_again(specification: Any = specification, observed: Any = observed) -> object:
            return [certify_strategy(fingerprint, specification, observed) for _ in range(100)]

        _timed(f"certify_strategy, all eight properties ({days:,} records)", 1, certify_all)
        _timed(f"repeated certification of run evidence x100 ({days:,})", 100, certify_again)


# --------------------------------------------------------------------------- #
# 4. Portability
# --------------------------------------------------------------------------- #


def _convention() -> MarketConvention:
    return MarketConvention(
        venue="XNYS",
        calendar_id="XNYS",
        quote_currency="USD",
        settlement_currency="USD",
        multiplier=Decimal("1"),
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
    )


def benchmark_portability() -> None:
    print("\n[4] Portability")
    fingerprint = _fingerprint()
    dataset = _dataset(30)
    version = dataset.require_provenance().dataset_version
    capabilities = BrokerCapabilities(
        order_types=frozenset(OrderType),
        time_in_force=frozenset(TimeInForce),
        asset_classes=frozenset({AssetType.EQUITY}),
        short_selling=True,
        fractional_quantities=True,
    )

    for environments_count, instruments_count in ((10, 100), (40, 400)):
        instruments = tuple(f"asset-{index:05d}" for index in range(instruments_count))
        specification = _specification(dataset, instruments)
        conventions: Mapping[str, MarketConvention] = {
            asset: _convention() for asset in instruments
        }
        availability = MarketAvailability(
            frozenset(instruments), frozenset({"XNYS"}), frozenset({"XNYS"}), frozenset({"USD"})
        )
        environments = tuple(
            TargetEnvironment(
                f"env-{index:03d}",
                ExecutionMode.LIVE if index % 2 else ExecutionMode.BACKTEST,
                capabilities,
                availability,
                datasets=frozenset({version}),
                conventions=conventions,
                runtime=RuntimeProfile(1.0, 1.0, 1.0),
                max_leverage=Decimal("1000"),
                account_currencies=frozenset({"USD"}),
            )
            for index in range(environments_count)
        )

        def evaluate(
            specification: Any = specification,
            environments: tuple[TargetEnvironment, ...] = environments,
            conventions: Mapping[str, MarketConvention] = conventions,
        ) -> object:
            return evaluate_portability(fingerprint, specification, environments, conventions)

        _timed(
            f"evaluate_portability ({environments_count} envs x {instruments_count} instruments)",
            environments_count,
            evaluate,
        )


def run_benchmark() -> None:
    print("=" * 84)
    print(
        "AlphaLab v3.6 -- strategy evaluation: fingerprints, manifests, certification, portability"
    )
    print("=" * 84)
    benchmark_fingerprints()
    benchmark_manifests()
    benchmark_certification()
    benchmark_portability()
    print()


if __name__ == "__main__":
    run_benchmark()
