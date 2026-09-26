"""The four v3.6 capabilities, joined through the real packages beneath them.

Nothing here is mocked into existence. One ingested multi-asset panel with real
provenance feeds a real v3.2 research study, a real backtest (twice, for
determinism and a rerun), a real paper run through the paper driver, and a real
live run through :class:`~alphalab.runtime.live.LiveSession` against
:class:`~alphalab.broker.paper.PaperBroker` -- a deterministic local venue, not
a network. The lifecycle records are the real registries, promoted on real
evidence and deployed through governance.

The flow the release is about:

.. code-block:: text

    dataset --> research study --> research configuration (study id)
       |                                      |
       |                                      v
       |                 registered version --> strategy fingerprint
       |                        |                     |
       +--> backtest -------> evidence --> promotion --> deployment
       |       |                                          |
       |       +--> reproducibility manifest (+ rerun)    v
       |                                         deployment specification
       +--> paper run --+                                 |
       +--> live run ---+--> runtime health               v
                        +--> comparison / reconciliation  certification
                                                          portability

What is looked for is the class of defect no unit test can see: a strategy
identity that changes between hops, a dataset, parameter, seed or engine that is
lost on the way, a certification claim the evidence does not support, a
portability check that edits the strategy, a lifecycle stage that contradicts
the runtime evidence, and anything machine-local or clock-dependent reaching an
identity.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from alphalab.api import backtest, ingest_rows, run_study, to_market_dataset
from alphalab.backtesting.state import BacktestResult
from alphalab.broker import PaperBroker
from alphalab.broker.account import BrokerAccount
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.dataset import Dataset
from alphalab.data.ingestion import IngestionRequest
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency
from alphalab.enterprise.identity import register_principal
from alphalab.enterprise.models import EnterpriseState
from alphalab.enterprise.rbac import define_role, grant_role
from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    AlignmentKey,
    BrokerCapabilities,
    BrokerRequirements,
    CapitalPolicy,
    CertificationEvidence,
    CertificationProperty,
    CertificationReport,
    CertificationStatus,
    CodeIdentity,
    ComparisonMetric,
    ComparisonSource,
    DependencyCompleteness,
    DeploymentSpecification,
    EngineIdentity,
    HealthStatus,
    LifecycleState,
    MarketAvailability,
    MarketRequirements,
    MeasurementBasis,
    MetricThreshold,
    MismatchCategory,
    PortabilityReport,
    PortabilityRequirement,
    PortabilityStatus,
    ReconciliationTolerances,
    ReproducibilityManifest,
    RequirementOutcome,
    RerunOutcome,
    ResourceBudget,
    ResourceMeasurement,
    ResourceMetric,
    ResultKind,
    RuntimeObservation,
    RuntimeProfile,
    RuntimeRequirements,
    SeedRole,
    StrategyFingerprint,
    StrategyLifecycleStage,
    SymbolMapping,
    TargetEnvironment,
    Tolerance,
    ValidationMethod,
    ValidationPolicy,
    advance_progression,
    assess_reproducibility,
    begin_progression,
    certify_strategy,
    compare_runs,
    dataset_assumption_from,
    deploy_strategy_version,
    evaluate_health,
    evaluate_portability,
    evidence_from_backtest,
    fingerprint_differences,
    fingerprint_for_version,
    manifest_for_run,
    manifest_for_study,
    observation_from_live_run,
    observations_from_backtest,
    progression_conflicts,
    promote_strategy_version,
    reconcile_execution_state,
    record_evidence,
    register_strategy,
    research_configuration_for_study,
    running_engine,
    source_digest,
    specification_for_version,
    verify_certification_report,
    verify_fingerprint,
    verify_manifest,
    verify_portability_report,
)
from alphalab.lifecycle.governance import LIFECYCLE_PERMISSIONS, Governance
from alphalab.lifecycle.strategy_version import StrategyVersion, get_strategy_version
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.market.record import MarketRecord
from alphalab.persistence.serializer import serialize
from alphalab.research.study import ResearchStudy, StudyResult
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.live import LiveRunState, LiveSession
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.runtime.session import TradingSession
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
from tests.unit.lifecycle.evidence_harness import CLEANING, csv_payload, equity_convention

STRATEGY_ID = "V36-PANEL-MOMENTUM"
PROVIDER = "v36-panel-vendor"
SEED = 360_600
SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
TRADED = "AAA"
DAYS = 40
START = datetime(2024, 1, 1, tzinfo=UTC)

INSTRUMENTS = tuple(
    InstrumentRecord(symbol, AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: symbol})
    for symbol in SYMBOLS
)
ASSETS = {record.symbol: record.asset_id for record in INSTRUMENTS}
TRADED_ASSET = ASSETS[TRADED]
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), INSTRUMENTS)

NORMALIZATION = NormalizationPolicy(
    venue="XNYS", currency="USD", timeframe=TimeFrame.D1, identity=REGISTRY, provider=PROVIDER
)

#: AAA bar index -> signed quantity: buy twelve, then sell five.
PLAN: Mapping[int, Decimal] = {3: Decimal("12"), 20: Decimal("-5")}


class PanelStrategy(BaseStrategy):
    """Trades one name from a panel, by that name's own bar count."""

    def __init__(self, strategy_id: str, asset_id: str, plan: Mapping[int, Decimal]) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._plan = dict(plan)
        self._seen = 0

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.bar.asset_id != self._asset_id:
            return ()
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


# --------------------------------------------------------------------------- #
# The data and the research
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def panel() -> Dataset:
    rows = []
    for position, symbol in enumerate(SYMBOLS):
        price = Decimal(100 + 10 * position)
        for day in range(DAYS):
            drift = Decimal(((position * 7 + day * 3) % 11) - 5) / Decimal(1000)
            price = (price * (Decimal(1) + drift + Decimal(position) / Decimal(10000))).quantize(
                Decimal("0.001")
            )
            stamp = (START + timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": stamp,
                    "open": str(price),
                    "high": str(price * Decimal("1.01")),
                    "low": str(price * Decimal("0.99")),
                    "close": str(price),
                    "volume": 10_000 + day,
                }
            )
    request = IngestionRequest(
        name="V36-PANEL",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY,
            "v36-integration",
            csv_payload(rows),
            1_700_000_000.0,
            "text/csv",
            "utf-8",
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
    )
    dataset = ingest_rows(rows, request).dataset
    assert dataset.is_versioned
    return dataset


@pytest.fixture(scope="module")
def study_result(panel: Dataset) -> StudyResult:
    study = ResearchStudy(
        study_name="v36-panel-momentum",
        dataset_version=panel.require_provenance().dataset_version,
        universe=SYMBOLS,
        features=(FeatureDefinition("mom_5", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5),),
        horizons=(1, 5),
    )
    return run_study(study, panel, buckets=2, minimum_assets=5, produced_at=10.0)


# --------------------------------------------------------------------------- #
# The runs
# --------------------------------------------------------------------------- #


def _run_config(mode: ExecutionMode = ExecutionMode.BACKTEST) -> RunConfig:
    pipeline = replace(pipeline_config(STRATEGY_ID), instruments=REGISTRY)
    if mode is ExecutionMode.LIVE:
        pipeline = replace(pipeline, routing=ExecutionRouting.EXTERNAL)
    return RunConfig(
        pipeline=pipeline,
        mode=mode,
        seed=SEED,
        start_timestamp=1.0,
        compile_analytics=mode is not ExecutionMode.LIVE,
    )


def _strategy_state() -> Any:
    return running_strategy_state(STRATEGY_ID, PanelStrategy(STRATEGY_ID, TRADED_ASSET, PLAN))


def _backtest(panel: Dataset) -> BacktestResult:
    return backtest(_run_config(), panel, _strategy_state(), context_factory, NORMALIZATION)


@pytest.fixture(scope="module")
def expected(panel: Dataset) -> BacktestResult:
    return _backtest(panel)


@pytest.fixture(scope="module")
def expected_again(panel: Dataset) -> BacktestResult:
    """The same inputs, run again from scratch with a fresh strategy instance."""

    return _backtest(panel)


@pytest.fixture(scope="module")
def records(panel: Dataset) -> tuple[MarketRecord, ...]:
    return tuple(to_market_dataset(panel, NORMALIZATION).records)


@pytest.fixture(scope="module")
def paper(records: tuple[MarketRecord, ...], panel: Dataset) -> BacktestResult:
    state = replace(
        TradingSession.initialize(_run_config(ExecutionMode.PAPER), _strategy_state()),
        source_id=panel.require_provenance().dataset_version,
    )
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return BacktestResult(RunEngine.finalize(state))


@pytest.fixture(scope="module")
def live(records: tuple[MarketRecord, ...], panel: Dataset) -> LiveRunState:
    broker = PaperBroker()
    account = BrokerAccount(
        account_id="ACC-V36",
        cash=START_CASH,
        equity=START_CASH,
        buying_power=START_CASH,
        margin=Decimal("0"),
        available_funds=START_CASH,
        currency="USD",
    )
    state = LiveSession.initialize(
        _run_config(ExecutionMode.LIVE),
        _strategy_state(),
        BrokerState(
            broker_name="PAPER-VENUE",
            connection_status=ConnectionStatus.DISCONNECTED,
            account=account,
        ),
        RoutingConfig(venue="PAPER-VENUE", currency="USD"),
    )
    state = replace(
        state, run=replace(state.run, source_id=panel.require_provenance().dataset_version)
    )
    state, _ = LiveSession.connect(state, broker, 1.5)
    for record in records:
        arrived = tuple(
            execution
            for execution in state.broker.executions.values()
            if execution.execution_id not in state.run.pipeline.execution.reports
        )
        state, _ = LiveSession.advance(state, record, context_factory, broker, executions=arrived)
    arrived = tuple(
        execution
        for execution in state.broker.executions.values()
        if execution.execution_id not in state.run.pipeline.execution.reports
    )
    state, _ = LiveSession.settle(state, arrived)
    # The venue's adapter reports it is alive at the end of the session.
    heartbeat, _ = broker.heartbeat(state.broker, state.run.current_timestamp)
    return replace(state, broker=heartbeat, run=RunEngine.finalize(state.run))


# --------------------------------------------------------------------------- #
# The lifecycle record, the fingerprint and the specification
# --------------------------------------------------------------------------- #

_ENTERPRISE = grant_role(
    define_role(
        register_principal(EnterpriseState(), "release-engineer", "Release Engineer", 0.0)[0],
        "release",
        LIFECYCLE_PERMISSIONS,
    ),
    "release-engineer",
    "release",
)
GOVERNANCE = Governance(_ENTERPRISE, "release-engineer")

DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="V3.6 Panel Momentum",
    version="1",
    author="integration",
    description="the v3.6 capability test",
    parameters={"entry": 12.0, "exit": -5.0},
)

POLICY = ValidationPolicy(
    policy_id="v36-gate",
    thresholds=(MetricThreshold("max_drawdown", maximum=1.0),),
    required_method=ValidationMethod.BACKTEST,
)

SOURCES = {"v36_panel/momentum.py": b"class PanelStrategy: ...\n"}
CODE = CodeIdentity(
    "v36-panel", "1.0.0", "v36_panel.momentum.PanelStrategy", source_digest(SOURCES)
)

#: The engine that runs every run in this module: this interpreter's AlphaLab,
#: recorded once, as a caller would.
ENGINE: EngineIdentity = running_engine()


@pytest.fixture(scope="module")
def registered() -> tuple[LifecycleState, StrategyVersion]:
    state, reference = register_strategy(LifecycleState(), STRATEGY_ID, DEFINITION, 10.0)
    return state, get_strategy_version(state.strategies, STRATEGY_ID, reference.version)


@pytest.fixture(scope="module")
def deployed(
    registered: tuple[LifecycleState, StrategyVersion], expected: BacktestResult
) -> tuple[LifecycleState, StrategyVersion]:
    state, version = registered
    evidence = evidence_from_backtest(expected, str(version.ref), 11.0)
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, GOVERNANCE, STRATEGY_ID, version.version, POLICY, evidence.evidence_id, 12.0
    )
    state, _ = deploy_strategy_version(
        state, GOVERNANCE, STRATEGY_ID, version.version, "paper", 13.0
    )
    return state, get_strategy_version(state.strategies, STRATEGY_ID, version.version)


@pytest.fixture(scope="module")
def fingerprint(
    registered: tuple[LifecycleState, StrategyVersion], study_result: StudyResult
) -> StrategyFingerprint:
    _, version = registered
    research = research_configuration_for_study(
        study_result.study, {"execution": "ExecutionSimulator defaults, ImmediateFill"}
    )
    return fingerprint_for_version(version, CODE, NO_DEPENDENCIES, research, ENGINE)


RISK = pipeline_config(STRATEGY_ID).risk_limits
CAPITAL = CapitalPolicy("acct-v21", "USD", START_CASH, ("USD",))
BROKER_REQUIREMENTS = BrokerRequirements(
    order_types=frozenset({OrderType.MARKET}),
    time_in_force=frozenset({TimeInForce.DAY}),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=True,
)
MARKET_REQUIREMENTS = MarketRequirements((TRADED_ASSET,), ("XNYS",), ("XNYS",), ("USD",))
RUNTIME = RuntimeRequirements(
    max_data_staleness_seconds=172_800.0,
    max_heartbeat_silence_seconds=172_800.0,
    max_execution_latency_seconds=5.0,
    position_tolerance=Tolerance(absolute=Decimal("0.000001")),
)


@pytest.fixture(scope="module")
def specification(
    deployed: tuple[LifecycleState, StrategyVersion], panel: Dataset
) -> DeploymentSpecification:
    _, version = deployed
    return specification_for_version(
        version,
        datasets=(dataset_assumption_from(panel, "prices"),),
        risk=RISK,
        capital=CAPITAL,
        broker=BROKER_REQUIREMENTS,
        market=MARKET_REQUIREMENTS,
        runtime=RUNTIME,
    )


@pytest.fixture(scope="module")
def manifest(
    expected: BacktestResult, panel: Dataset, fingerprint: StrategyFingerprint
) -> ReproducibilityManifest:
    return manifest_for_run(expected, panel, fingerprint, ENGINE)


@pytest.fixture(scope="module")
def rerun(
    expected_again: BacktestResult, panel: Dataset, fingerprint: StrategyFingerprint
) -> ReproducibilityManifest:
    return manifest_for_run(expected_again, panel, fingerprint, ENGINE)


def _positions_of(result: BacktestResult) -> dict[str, Decimal]:
    return {
        asset_id: position.quantity
        for asset_id, position in result.state.portfolio.positions.items()
    }


def _attributed(state: Any) -> set[str]:
    """Every strategy the run's fills are attributed to, from their trade records."""

    return {
        contribution.strategy_id
        for record in state.trade_records
        for contribution in record.contributions
    }


def _live_observation(live: LiveRunState, expected: BacktestResult) -> RuntimeObservation:
    return observation_from_live_run(
        live,
        observed_at=live.run.current_timestamp,
        last_market_data_at=live.run.current_timestamp,
        expected_positions=_positions_of(expected),
        risk_violations=(),
        state_expectations=(),
        submitted_at={
            str(order.order_id.value): order.created_at
            for order in live.run.pipeline.oms.orders.orders()
        },
    )


@pytest.fixture(scope="module")
def report(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    expected: BacktestResult,
    expected_again: BacktestResult,
    panel: Dataset,
    manifest: ReproducibilityManifest,
    rerun: ReproducibilityManifest,
    live: LiveRunState,
    registered: tuple[LifecycleState, StrategyVersion],
) -> CertificationReport:
    _, version = registered
    progression = begin_progression(version.ref)
    for index, stage in enumerate(
        (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
        )
    ):
        progression = advance_progression(progression, stage, "v3.6 integration", float(index))

    measured = ResourceMeasurement(
        ResourceMetric.CPU_SECONDS,
        Decimal("0.25"),
        MeasurementBasis.MEASURED,
        "time.process_time around one backtest, as the operator recorded it",
        "CPython 3.12 on the operator's build agent",
    )
    return certify_strategy(
        fingerprint,
        specification,
        CertificationEvidence(
            runs=(expected,),
            repeated_runs=(expected, expected_again),
            datasets=(panel,),
            manifest=manifest,
            rerun=rerun,
            observations=(_live_observation(live, expected),),
            measurements=(measured,),
        ),
        budgets=(
            ResourceBudget(ResourceMetric.RECORDS_PROCESSED, Decimal("1000")),
            ResourceBudget(ResourceMetric.FILLS, Decimal("10")),
            ResourceBudget(ResourceMetric.CPU_SECONDS, Decimal("5")),
        ),
        progression=progression,
    )


EVERYTHING = BrokerCapabilities(
    order_types=frozenset(OrderType),
    time_in_force=frozenset(TimeInForce),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=True,
)
AVAILABLE = MarketAvailability(
    instruments=frozenset(ASSETS.values()),
    venues=frozenset({"XNYS"}),
    calendar_ids=frozenset({"XNYS"}),
    quote_currencies=frozenset({"USD"}),
)


@pytest.fixture(scope="module")
def portability(
    fingerprint: StrategyFingerprint, specification: DeploymentSpecification, panel: Dataset
) -> PortabilityReport:
    common: dict[str, Any] = {
        "market": AVAILABLE,
        "conventions": {TRADED_ASSET: equity_convention()},
        "max_leverage": RISK.leverage.max_leverage,
        "account_currencies": frozenset({"USD"}),
    }
    environments = (
        TargetEnvironment(
            "research",
            ExecutionMode.BACKTEST,
            EVERYTHING,
            datasets=frozenset({panel.require_provenance().dataset_version}),
            **common,
        ),
        TargetEnvironment(
            "paper", ExecutionMode.PAPER, EVERYTHING, runtime=RuntimeProfile(1, 1, 1), **common
        ),
        TargetEnvironment(
            "live-broker-a",
            ExecutionMode.LIVE,
            EVERYTHING,
            runtime=RuntimeProfile(2.0, 5.0, 0.5),
            **common,
        ),
        TargetEnvironment(
            "live-broker-b",
            ExecutionMode.LIVE,
            replace(EVERYTHING, short_selling=False),
            runtime=RuntimeProfile(2.0, 5.0, 9.0),
            **common,
        ),
    )
    return evaluate_portability(
        fingerprint, specification, environments, {TRADED_ASSET: equity_convention()}
    )


# --------------------------------------------------------------------------- #
# 1 -- the runs are real and are one strategy's
# --------------------------------------------------------------------------- #


def test_the_runs_traded_one_strategy_through_the_real_execution_path(
    expected: BacktestResult, paper: BacktestResult, live: LiveRunState
) -> None:
    assert expected.records_processed == len(SYMBOLS) * DAYS
    assert len(expected.fills) == 2
    assert len(paper.fills) == 2
    assert len(live.run.pipeline.execution.reports) == 2
    for run_state in (expected.state, paper.state, live.run.pipeline):
        assert tuple(run_state.strategy.strategies) == (STRATEGY_ID,)
        # Post-trade attribution lives in the contribution ledger frozen onto
        # each fill's TradeRecord (ADR-0015); the OMS order names no owner.
        assert _attributed(run_state) == {STRATEGY_ID}


# --------------------------------------------------------------------------- #
# 2 -- identity survives every hop
# --------------------------------------------------------------------------- #


def test_the_fingerprint_is_derived_from_the_registered_version_and_its_study(
    fingerprint: StrategyFingerprint,
    registered: tuple[LifecycleState, StrategyVersion],
    study_result: StudyResult,
) -> None:
    _, version = registered

    assert verify_fingerprint(fingerprint)
    assert fingerprint.name == version.name
    assert fingerprint.strategy_id == version.definition.strategy_id
    assert dict(fingerprint.parameters) == dict(version.definition.parameters)
    assert fingerprint.research.study_id == study_result.study_id
    assert fingerprint.dependencies.completeness is DependencyCompleteness.EXACT_CLOSURE
    assert fingerprint.engine == ENGINE


def test_promotion_and_deployment_do_not_change_the_fingerprint(
    fingerprint: StrategyFingerprint,
    deployed: tuple[LifecycleState, StrategyVersion],
) -> None:
    """The registry stage moved; the strategy did not."""

    _, live_version = deployed
    after = fingerprint_for_version(
        live_version, CODE, NO_DEPENDENCIES, fingerprint.research, ENGINE
    )

    assert live_version.stage.name == "PRODUCTION"
    assert after == fingerprint
    assert fingerprint_differences(fingerprint, after) == ()


def test_one_dataset_identity_reaches_every_capability(
    panel: Dataset,
    study_result: StudyResult,
    expected: BacktestResult,
    paper: BacktestResult,
    live: LiveRunState,
    deployed: tuple[LifecycleState, StrategyVersion],
    specification: DeploymentSpecification,
    manifest: ReproducibilityManifest,
) -> None:
    version_id = panel.require_provenance().dataset_version
    state, version = deployed
    assert version.evidence_id is not None

    assert study_result.dataset_version == version_id
    assert expected.dataset_id == version_id
    assert paper.dataset_id == version_id
    assert live.run.source_id == version_id
    assert state.evidence[version.evidence_id].dataset_id == version_id
    assert specification.dataset_versions == (version_id,)
    assert manifest.dataset_version == version_id
    assert manifest.dataset_content_hash == panel.require_provenance().content_hash


def test_parameters_seed_engine_and_configuration_are_not_lost(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    manifest: ReproducibilityManifest,
    expected: BacktestResult,
    deployed: tuple[LifecycleState, StrategyVersion],
) -> None:
    state, version = deployed
    assert version.evidence_id is not None
    evidence = state.evidence[version.evidence_id]

    assert dict(specification.parameters) == dict(fingerprint.parameters)
    assert manifest.seed == SEED == expected.seed == evidence.seed
    assert manifest.seed_role is SeedRole.IDENTIFIER_STREAM
    assert manifest.engine == fingerprint.engine == ENGINE
    recorded = json.loads(manifest.configuration)
    assert recorded["mode"] == "ExecutionMode.BACKTEST"
    assert recorded["pipeline"]["risk_limits"]["leverage"]["max_leverage"] == str(
        specification.risk.leverage.max_leverage
    )
    assert recorded["strategies"][0]["strategy_id"] == fingerprint.strategy_id


# --------------------------------------------------------------------------- #
# 3 -- reproducibility across runs and across environments
# --------------------------------------------------------------------------- #


def test_a_rerun_of_the_backtest_reproduces_it(
    manifest: ReproducibilityManifest, rerun: ReproducibilityManifest
) -> None:
    assessment = assess_reproducibility(manifest, rerun)

    assert verify_manifest(manifest)
    assert assessment.rerun is RerunOutcome.REPRODUCED
    assert assessment.metadata_complete
    assert manifest.manifest_id == rerun.manifest_id


def test_the_paper_run_is_another_result_of_the_same_strategy(
    paper: BacktestResult,
    panel: Dataset,
    fingerprint: StrategyFingerprint,
    manifest: ReproducibilityManifest,
) -> None:
    """Moving environments changes the result's record and never the strategy's identity."""

    paper_manifest = manifest_for_run(paper, panel, fingerprint, ENGINE)
    assessment = assess_reproducibility(manifest, paper_manifest)

    assert paper_manifest.fingerprint == manifest.fingerprint == fingerprint
    assert paper_manifest.mode is ExecutionMode.PAPER
    assert paper_manifest.configuration_id != manifest.configuration_id
    assert assessment.rerun is RerunOutcome.INPUTS_DIFFER
    assert any(line.startswith("configuration:") for line in assessment.rerun_detail)


def test_the_study_that_informed_the_strategy_has_its_own_manifest(
    study_result: StudyResult, panel: Dataset, fingerprint: StrategyFingerprint
) -> None:
    study_manifest = manifest_for_study(study_result, panel, ENGINE, fingerprint)

    assert study_manifest.kind is ResultKind.STUDY
    assert study_manifest.seed_role is SeedRole.ABSENT
    assert study_manifest.configuration_id == study_result.study_id.rpartition("@")[2]
    assert verify_manifest(study_manifest)


# --------------------------------------------------------------------------- #
# 4 -- certification rests on the evidence and on nothing else
# --------------------------------------------------------------------------- #


def test_every_property_passes_on_complete_evidence(report: CertificationReport) -> None:
    for assessment in report.assessments:
        assert assessment.status is CertificationStatus.PASS, (
            assessment.claim,
            assessment.findings,
        )
    assert verify_certification_report(report)
    assert report.stage is StrategyLifecycleStage.PAPER
    assert report.specification_findings == ()


def test_the_certified_identities_are_the_ones_the_flow_produced(
    report: CertificationReport,
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    manifest: ReproducibilityManifest,
) -> None:
    reproducible = report.assessment(CertificationProperty.REPRODUCIBLE)

    assert report.fingerprint == fingerprint.fingerprint
    assert report.specification_id == specification.specification_id
    assert reproducible.evidence["manifest_id"] == manifest.manifest_id


def test_runtime_certification_agrees_with_the_health_evaluator(
    report: CertificationReport,
    specification: DeploymentSpecification,
    live: LiveRunState,
    expected: BacktestResult,
) -> None:
    health = evaluate_health(specification, _live_observation(live, expected))

    assert health.status is HealthStatus.HEALTHY
    assert (
        report.assessment(CertificationProperty.RUNTIME_BEHAVIOR).evidence["reports.HEALTHY"] == "1"
    )


def test_a_live_observation_with_no_heartbeat_is_not_certified_healthy(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    live: LiveRunState,
    expected: BacktestResult,
) -> None:
    silent = replace(live, broker=replace(live.broker, last_heartbeat=0.0))
    report = certify_strategy(
        fingerprint,
        specification,
        CertificationEvidence(observations=(_live_observation(silent, expected),)),
    )

    assert (
        report.status_of(CertificationProperty.RUNTIME_BEHAVIOR)
        is CertificationStatus.INSUFFICIENT_EVIDENCE
    )


def test_certifying_against_the_research_stage_contradicts_the_runtime_evidence(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    live: LiveRunState,
    expected: BacktestResult,
    registered: tuple[LifecycleState, StrategyVersion],
) -> None:
    _, version = registered
    report = certify_strategy(
        fingerprint,
        specification,
        CertificationEvidence(observations=(_live_observation(live, expected),)),
        progression=begin_progression(version.ref),
    )

    assert (
        report.status_of(CertificationProperty.RUNTIME_BEHAVIOR)
        is CertificationStatus.INSUFFICIENT_EVIDENCE
    )


def test_the_progression_and_the_registry_still_agree(
    deployed: tuple[LifecycleState, StrategyVersion],
) -> None:
    _, version = deployed
    progression = begin_progression(version.ref)
    for index, stage in enumerate(
        (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
        )
    ):
        progression = advance_progression(progression, stage, "v3.6", float(index))

    assert progression_conflicts(progression, version) == ()


# --------------------------------------------------------------------------- #
# 5 -- portability: one identity, declared environments, nothing adapted
# --------------------------------------------------------------------------- #


def test_the_strategy_moves_to_every_compatible_environment_unchanged(
    portability: PortabilityReport,
    fingerprint: StrategyFingerprint,
) -> None:
    assert verify_portability_report(portability)
    assert portability.fingerprint == fingerprint.fingerprint
    assert portability.portable_to == ("research", "paper", "live-broker-a")


def test_broker_b_is_refused_with_every_reason(portability: PortabilityReport) -> None:
    broker_b = portability.for_environment("live-broker-b")

    assert broker_b.status is PortabilityStatus.NOT_PORTABLE
    assert broker_b.check(PortabilityRequirement.EXECUTION).outcome is RequirementOutcome.BLOCKED
    assert broker_b.check(PortabilityRequirement.RUNTIME).outcome is RequirementOutcome.BLOCKED
    assert broker_b.check(PortabilityRequirement.STRATEGY_LOGIC).outcome is (
        RequirementOutcome.SATISFIED
    )
    joined = " ".join(broker_b.blockers)
    assert "short" in joined
    assert "execution latency" in joined


def test_portability_evaluation_changed_nothing_it_was_given(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    portability: PortabilityReport,
    registered: tuple[LifecycleState, StrategyVersion],
    study_result: StudyResult,
) -> None:
    _, version = registered
    rebuilt = fingerprint_for_version(
        version,
        CODE,
        NO_DEPENDENCIES,
        research_configuration_for_study(
            study_result.study, {"execution": "ExecutionSimulator defaults, ImmediateFill"}
        ),
        ENGINE,
    )

    assert rebuilt == fingerprint
    assert verify_fingerprint(fingerprint)
    assert portability.specification_id == specification.specification_id


def test_every_environment_was_evaluated_for_the_runs_actual_strategy(
    portability: PortabilityReport, paper: BacktestResult, live: LiveRunState
) -> None:
    """What ran on paper and live is what portability evaluated: one strategy id."""

    for state in (paper.state, live.run.pipeline):
        assert tuple(state.strategy.strategies) == (STRATEGY_ID,)
    assert {entry.environment for entry in portability.environments} >= {"paper"}


# --------------------------------------------------------------------------- #
# 6 -- comparison and reconciliation consume the same identity
# --------------------------------------------------------------------------- #


def test_the_backtest_and_the_paper_run_agree_under_the_seeded_alignment(
    expected: BacktestResult, paper: BacktestResult
) -> None:
    tolerances = dict.fromkeys(ComparisonMetric, Tolerance(absolute=Decimal("0")))
    comparison = compare_runs(
        observations_from_backtest(expected, ComparisonSource.EXPECTED, AlignmentKey.ORDER_ID),
        observations_from_backtest(paper, ComparisonSource.PAPER, AlignmentKey.ORDER_ID),
        tolerances,
    )

    assert comparison.material == ()
    assert _attributed(expected.state) == _attributed(paper.state) == {STRATEGY_ID}


def test_the_live_book_reconciles_against_the_venue(live: LiveRunState) -> None:
    exact = Tolerance(absolute=Decimal("0"))
    reconciliation = reconcile_execution_state(
        live.run.pipeline,
        live.broker,
        live.mapping,
        SymbolMapping.identity(),
        ReconciliationTolerances(exact, exact, exact, exact, exact, exact, exact),
    )

    # The venue's own cash book is PaperBroker's, not the portfolio engine's, so
    # the orders and fills are what the two sides are expected to agree on --
    # the scope test_v35_capabilities.py reconciles live runs under too.
    for category in (
        MismatchCategory.MISSING_EXPECTED_ORDER,
        MismatchCategory.UNEXPECTED_OBSERVED_ORDER,
        MismatchCategory.MISSING_EXPECTED_FILL,
        MismatchCategory.UNEXPECTED_OBSERVED_FILL,
    ):
        assert reconciliation.mismatches_in(category) == ()
    assert reconciliation.compared_orders == 2
    assert reconciliation.compared_fills == 2


# --------------------------------------------------------------------------- #
# 7 -- nothing machine-local and no clock reaches an identity or an artifact
# --------------------------------------------------------------------------- #


def test_every_artifact_is_deterministic_json_free_of_machine_paths(
    fingerprint: StrategyFingerprint,
    manifest: ReproducibilityManifest,
    report: CertificationReport,
    portability: PortabilityReport,
) -> None:
    import os
    import pathlib

    root = str(pathlib.Path(__file__).resolve().parents[2])
    for artifact in (fingerprint, manifest, report, portability):
        payload = serialize(artifact)
        assert payload == serialize(artifact)
        assert root not in payload
        assert str(pathlib.Path.home()) not in payload
        assert os.uname().nodename not in payload


def test_a_different_wall_clock_changes_no_identity(
    monkeypatch: pytest.MonkeyPatch,
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    expected: BacktestResult,
    panel: Dataset,
    manifest: ReproducibilityManifest,
) -> None:
    """Every clock AlphaLab could reach is moved a decade forward; nothing moves."""

    import time

    monkeypatch.setattr(time, "time", lambda: 2_000_000_000.0)
    monkeypatch.setattr(time, "perf_counter", lambda: 99_999.0)

    again = manifest_for_run(expected, panel, fingerprint, ENGINE)
    assert again.manifest_id == manifest.manifest_id
    first = certify_strategy(fingerprint, specification, CertificationEvidence(manifest=manifest))
    second = certify_strategy(fingerprint, specification, CertificationEvidence(manifest=again))
    assert first.report_id == second.report_id


def test_a_different_recorded_engine_is_a_different_result_record_and_says_so(
    expected: BacktestResult,
    panel: Dataset,
    fingerprint: StrategyFingerprint,
    manifest: ReproducibilityManifest,
) -> None:
    later = manifest_for_run(expected, panel, fingerprint, EngineIdentity("alphalab", "99.0.0"))
    assessment = assess_reproducibility(manifest, later)

    assert later.result_id == manifest.result_id
    assert later.manifest_id != manifest.manifest_id
    assert assessment.rerun is RerunOutcome.INPUTS_DIFFER
    assert "nevertheless matched" in assessment.rerun_detail[-1]
