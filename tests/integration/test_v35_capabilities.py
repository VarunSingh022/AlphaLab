"""The five v3.5 capabilities, joined through the real packages beneath them.

Nothing here is mocked into existence. One ingested dataset with real
provenance drives a real backtest, a real paper run through the paper driver,
and a real live run through :class:`~alphalab.runtime.live.LiveSession` against
:class:`~alphalab.broker.paper.PaperBroker` -- a deterministic local venue, not
a network. The lifecycle records are the real registries, the risk limits are
the type the pre-trade gate enforces, and the broker state is the canonical
vendor-neutral one.

The flow the release is about:

.. code-block:: text

    ingest  ->  dataset version
      |
      +--> backtest ------------> evidence -> promotion -> deployment
      |         |                                             |
      |         |                                             v
      |         |                                   deployment specification
      |         |                                      (dataset assumption)
      |         v
      +--> paper run ----+
      |                  |
      +--> live run -----+--> expected / paper / live comparison
                |
                +--> runtime health
                +--> reconciliation against the broker

What is being looked for is the class of defect an isolated unit test cannot
see: a dataset identity that stops being the same string somewhere along the
chain, a lifecycle stage that contradicts the registry, a health finding that
disagrees with the state it was derived from, a comparison that turns a live
run's unmeasured slippage into a zero, and a reconciliation that mutates
something.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.api import backtest, ingest_rows, to_market_dataset
from alphalab.backtesting.state import BacktestResult
from alphalab.broker import PaperBroker
from alphalab.broker.account import BrokerAccount
from alphalab.broker.state import BrokerState, ConnectionStatus
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
from alphalab.enterprise.identity import register_principal
from alphalab.enterprise.models import EnterpriseState
from alphalab.enterprise.rbac import define_role, grant_role
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import (
    AlignmentKey,
    BrokerRequirements,
    CapitalPolicy,
    ComparisonMetric,
    ComparisonOutcome,
    ComparisonSource,
    HealthCategory,
    HealthStatus,
    LifecycleState,
    MarketRequirements,
    MetricThreshold,
    MismatchCategory,
    ReconciliationTolerances,
    RuntimeRequirements,
    StateExpectation,
    StrategyLifecycleStage,
    SymbolMapping,
    Tolerance,
    ValidationMethod,
    ValidationPolicy,
    active_strategy_version,
    advance_progression,
    begin_progression,
    compare_expected_paper_live,
    compare_runs,
    dataset_assumption_from,
    deploy_strategy_version,
    evaluate_health,
    evidence_from_backtest,
    observation_from_live_run,
    observations_from_backtest,
    observations_from_broker,
    progression_conflicts,
    promote_strategy_version,
    reconcile_execution_state,
    record_evidence,
    register_strategy,
    specification_for_version,
    validate_specification,
    verify_specification_id,
)
from alphalab.lifecycle.governance import LIFECYCLE_PERMISSIONS, Governance
from alphalab.lifecycle.strategy_version import StrategyVersion, get_strategy_version
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.market.record import MarketRecord
from alphalab.model_registry import ModelStage
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.live import LiveRunState, LiveSession, live_health
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

STRATEGY_ID = "V35-MOMENTUM"
SYMBOL = "V35"
PROVIDER = "v35-vendor"
SEED = 350_000
RETRIEVED_AT = 1_700_000_000.0

#: One declared instrument. Its ``asset_id`` is **derived** from the
#: declaration, which is what lets the wire boundary resolve the vendor's symbol
#: to a canonical identity a ``Fill`` will accept. ``UnresolvedIdentity`` cannot
#: reach a fill at all (ADR-0016), so a run that trades needs a registry.
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

#: Closes that are deliberately not round, so the monetary precision policy is
#: actually exercised rather than assumed.
CLOSES = [
    Decimal("100.005"),
    Decimal("102.017"),
    Decimal("101.003"),
    Decimal("104.011"),
    Decimal("103.007"),
    Decimal("105.019"),
]


class BarStrategy(BaseStrategy):
    """Emits a scripted signed quantity at chosen bar timestamps.

    The bar sibling of ``harness.ScriptedStrategy``: the ingested dataset is a
    bar series, so the strategy that trades it has to take ``on_bar``.
    """

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


PLAN: Mapping[int, Decimal] = {1: Decimal("10"), 3: Decimal("-4")}


# --------------------------------------------------------------------------- #
# Fixtures: one dataset, three runs over it
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def dataset() -> Dataset:
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
        for day, close in enumerate(CLOSES)
    ]
    request = IngestionRequest(
        name="V35-PRICES",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "v35-integration", b"", RETRIEVED_AT, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
    )
    result = ingest_rows(rows, request)
    assert result.dataset.is_versioned
    return result.dataset


@pytest.fixture(scope="module")
def dataset_version(dataset: Dataset) -> str:
    return dataset.require_provenance().dataset_version


@pytest.fixture(scope="module")
def records(dataset: Dataset) -> tuple[MarketRecord, ...]:
    return tuple(to_market_dataset(dataset, NORMALIZATION).records)


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
    return running_strategy_state(STRATEGY_ID, BarStrategy(STRATEGY_ID, ASSET_ID, PLAN))


@pytest.fixture(scope="module")
def expected(dataset: Dataset) -> BacktestResult:
    """The backtest: what the research says should happen."""

    return backtest(_run_config(), dataset, _strategy_state(), context_factory, NORMALIZATION)


@pytest.fixture(scope="module")
def paper(records: tuple[MarketRecord, ...], dataset_version: str) -> BacktestResult:
    """A paper run: the same records through the paper driver."""

    state = replace(
        TradingSession.initialize(_run_config(), _strategy_state()),
        source_id=dataset_version,
    )
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return BacktestResult(RunEngine.finalize(state))


def _broker_state() -> BrokerState:
    return BrokerState(
        broker_name="PAPER-VENUE",
        connection_status=ConnectionStatus.DISCONNECTED,
        account=BrokerAccount(
            account_id="ACC-V35",
            cash=START_CASH,
            equity=START_CASH,
            buying_power=START_CASH,
            margin=Decimal("0"),
            available_funds=START_CASH,
            currency="USD",
        ),
    )


@pytest.fixture(scope="module")
def live(records: tuple[MarketRecord, ...], dataset_version: str) -> LiveRunState:
    """A live run through the live driver against a deterministic local venue.

    ``PaperBroker`` is AlphaLab's reference ``BrokerProtocol`` implementation and
    is its own venue: it fills a market order on submission. No socket is opened
    and no credential exists.
    """

    broker = PaperBroker()
    state = LiveSession.initialize(
        _run_config(ExecutionMode.LIVE),
        _strategy_state(),
        _broker_state(),
        RoutingConfig(venue="PAPER-VENUE", currency="USD"),
    )
    state = replace(state, run=replace(state.run, source_id=dataset_version))
    state, _ = LiveSession.connect(state, broker, 1.5)

    for record in records:
        # Whatever the venue filled since the last step comes back in. This is
        # the caller's job by design: how fills arrive is a venue's business.
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
    return replace(state, run=RunEngine.finalize(state.run))


# --------------------------------------------------------------------------- #
# The lifecycle record the deployment specification is built from
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
    name="V3.5 Momentum",
    version="1",
    author="integration",
    description="the v3.5 capability test",
    parameters={"entry": 10.0, "exit": -4.0},
)

POLICY = ValidationPolicy(
    policy_id="v35-gate",
    thresholds=(MetricThreshold("max_drawdown", maximum=1.0),),
    required_method=ValidationMethod.BACKTEST,
)


@pytest.fixture(scope="module")
def deployed(expected: BacktestResult) -> tuple[LifecycleState, StrategyVersion]:
    """A strategy version promoted on real backtest evidence and deployed."""

    state, reference = register_strategy(LifecycleState(), STRATEGY_ID, DEFINITION, 10.0)
    evidence = evidence_from_backtest(expected, str(reference), 11.0)
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, GOVERNANCE, STRATEGY_ID, reference.version, POLICY, evidence.evidence_id, 12.0
    )
    state, _ = deploy_strategy_version(
        state, GOVERNANCE, STRATEGY_ID, reference.version, "paper", 13.0
    )
    return state, get_strategy_version(state.strategies, STRATEGY_ID, reference.version)


RISK = pipeline_config(STRATEGY_ID).risk_limits
CAPITAL = CapitalPolicy("acct-v21", "USD", START_CASH, ("USD",))
BROKER_REQUIREMENTS = BrokerRequirements(
    order_types=frozenset({OrderType.MARKET}),
    time_in_force=frozenset({TimeInForce.DAY}),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=True,
)
RUNTIME = RuntimeRequirements(
    max_data_staleness_seconds=172_800.0,
    max_heartbeat_silence_seconds=172_800.0,
    max_execution_latency_seconds=5.0,
    position_tolerance=Tolerance(absolute=Decimal("0.000001")),
)


@pytest.fixture(scope="module")
def specification(deployed: tuple[LifecycleState, StrategyVersion], dataset: Dataset) -> Any:
    _, version = deployed
    return specification_for_version(
        version,
        datasets=(dataset_assumption_from(dataset, "prices"),),
        risk=RISK,
        capital=CAPITAL,
        broker=BROKER_REQUIREMENTS,
        market=MarketRequirements((ASSET_ID,), ("XNYS",), ("XNYS",), ("USD",)),
        runtime=RUNTIME,
    )


# --------------------------------------------------------------------------- #
# 1 -- the runs actually ran
# --------------------------------------------------------------------------- #


def test_all_three_runs_traded_through_the_real_execution_path(
    expected: BacktestResult, paper: BacktestResult, live: LiveRunState
) -> None:
    assert expected.records_processed == len(CLOSES)
    assert len(expected.fills) == 2
    assert len(paper.fills) == 2
    assert len(live.run.pipeline.execution.reports) == 2
    assert live.settled
    # ``PaperBroker`` is its own venue and fills a market order on submission,
    # so the fill is already in ``BrokerState.executions`` when the caller hands
    # it back: the broker layer classifies it DUPLICATE, which is not a break,
    # and the pipeline applies it once because that application is idempotent in
    # ``execution_id``.
    assert not any(outcome.decision.is_break for outcome in live.settled)


def test_the_paper_driver_reproduces_the_backtest_because_it_is_the_same_step(
    expected: BacktestResult, paper: BacktestResult
) -> None:
    """ADR-0010's structural parity, restated as a v3.5 comparison."""

    assert [str(fill.asset_id) for fill in paper.fills] == [
        str(fill.asset_id) for fill in expected.fills
    ]
    assert paper.state.portfolio.realized_pnl == expected.state.portfolio.realized_pnl


# --------------------------------------------------------------------------- #
# 2 -- dataset lineage survives the whole chain
# --------------------------------------------------------------------------- #


def test_one_dataset_identity_reaches_the_run_the_evidence_and_the_specification(
    dataset_version: str,
    expected: BacktestResult,
    paper: BacktestResult,
    live: LiveRunState,
    deployed: tuple[LifecycleState, StrategyVersion],
    specification: Any,
) -> None:
    """The join the release rests on: one string, five places, no re-typing."""

    state, version = deployed
    assert version.evidence_id is not None
    evidence = state.evidence[version.evidence_id]

    assert expected.dataset_id == dataset_version
    assert paper.dataset_id == dataset_version
    assert live.run.source_id == dataset_version
    assert evidence.dataset_id == dataset_version
    assert specification.dataset_versions == (dataset_version,)


def test_a_specification_is_reproducible_and_self_verifying(specification: Any) -> None:
    assert verify_specification_id(specification)
    assert validate_specification(specification) == ()


def test_the_specification_configures_what_the_registered_version_declares(
    deployed: tuple[LifecycleState, StrategyVersion], specification: Any
) -> None:
    _, version = deployed
    assert specification.parameters == version.definition.parameters
    assert specification.strategy == version.ref


# --------------------------------------------------------------------------- #
# 3 -- the progression and the registry agree, and neither owns the other
# --------------------------------------------------------------------------- #


def test_the_progression_walks_to_paper_and_agrees_with_the_registered_version(
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
        progression = advance_progression(progression, stage, "v3.5 integration", float(index))

    assert version.stage is ModelStage.PRODUCTION
    assert progression_conflicts(progression, version) == ()


def test_a_progression_that_contradicts_the_registry_is_reported_not_repaired(
    deployed: tuple[LifecycleState, StrategyVersion],
) -> None:
    _, version = deployed
    research_only = begin_progression(version.ref)

    conflicts = progression_conflicts(research_only, version)

    assert len(conflicts) == 1
    assert "RESEARCH" in conflicts[0] and "PRODUCTION" in conflicts[0]
    assert research_only.stage is StrategyLifecycleStage.RESEARCH
    assert version.stage is ModelStage.PRODUCTION


def test_the_deployment_ledger_remains_the_only_answer_to_what_is_live(
    deployed: tuple[LifecycleState, StrategyVersion],
) -> None:
    """A progression names no environment, so it cannot be a second answer."""

    state, version = deployed
    progression = begin_progression(version.ref)

    assert active_strategy_version(state, "paper") is not None
    assert active_strategy_version(state, "live-eu") is None
    assert not hasattr(progression, "environment")


# --------------------------------------------------------------------------- #
# 4 -- runtime health, derived from the live run rather than restated
# --------------------------------------------------------------------------- #


def _positions_of(result: BacktestResult) -> dict[str, Decimal]:
    return {
        asset_id: position.quantity
        for asset_id, position in result.state.portfolio.positions.items()
    }


def test_health_is_derived_from_the_live_run_and_agrees_with_it(
    live: LiveRunState, expected: BacktestResult, specification: Any
) -> None:
    observation = observation_from_live_run(
        live,
        observed_at=live.run.current_timestamp,
        last_market_data_at=live.run.current_timestamp,
        expected_positions=_positions_of(expected),
        risk_violations=(),
        state_expectations=(StateExpectation("dataset", live.run.source_id, live.run.source_id),),
        submitted_at=_submitted_at(live.run.pipeline.oms.orders.orders()),
    )

    assert observation.broker_connection is live.broker.connection_status
    assert observation.observed_positions == _positions_of(BacktestResult(live.run))
    # The executions are keyed by AlphaLab's own order identifier, not the venue
    # handle, so a finding about one names the order the rest of the report does.
    assert observation.executions is not None
    known = {str(order.order_id.value) for order in live.run.pipeline.oms.orders.orders()}
    assert {execution.order_id for execution in observation.executions} <= known

    report = evaluate_health(specification, observation)

    # The venue was connected, the book matches the backtest, every execution
    # was inside the latency budget -- but no heartbeat ever arrived from
    # PaperBroker, so that one category is unevaluated and the report says
    # UNKNOWN rather than HEALTHY.
    assert report.findings == ()
    assert not report.was_evaluated(HealthCategory.HEARTBEAT_LOSS)
    assert report.status is HealthStatus.UNKNOWN
    assert report.specification_id == specification.specification_id


def test_an_execution_whose_submission_instant_is_unknown_warns_rather_than_passing(
    live: LiveRunState, specification: Any
) -> None:
    """Without the instants, latency is unmeasurable -- and says so."""

    report = evaluate_health(
        specification,
        observation_from_live_run(live, observed_at=live.run.current_timestamp),
    )

    findings = report.findings_in(HealthCategory.ABNORMAL_EXECUTION)
    assert len(findings) == 2
    assert all("no measurable latency" in finding.summary for finding in findings)
    assert all(finding.detail["submitted_at"] == "None" for finding in findings)
    assert report.status is HealthStatus.DEGRADED


def test_a_healthy_structured_report_and_live_health_do_not_contradict_each_other(
    live: LiveRunState, expected: BacktestResult, specification: Any
) -> None:
    """Two health surfaces, one state: they must not disagree about it."""

    observation = observation_from_live_run(
        live,
        observed_at=live.run.current_timestamp,
        last_market_data_at=live.run.current_timestamp,
        expected_positions=_positions_of(expected),
        risk_violations=(),
        state_expectations=(),
    )
    report = evaluate_health(specification, observation)

    assert live_health(live) == ()
    assert report.findings_in(HealthCategory.BROKER_DISCONNECT) == ()


def test_a_disconnected_venue_is_seen_by_the_structured_report_too(
    live: LiveRunState, specification: Any
) -> None:
    down = replace(live, broker=replace(live.broker, connection_status=ConnectionStatus.FAILED))

    report = evaluate_health(
        specification,
        observation_from_live_run(down, observed_at=down.run.current_timestamp),
    )

    assert report.status is HealthStatus.BREACHED
    assert len(report.findings_in(HealthCategory.BROKER_DISCONNECT)) == 1
    assert "The venue connection is FAILED" in " ".join(live_health(down))


def test_health_evaluation_mutates_nothing(live: LiveRunState, specification: Any) -> None:
    before = live
    evaluate_health(
        specification, observation_from_live_run(live, observed_at=live.run.current_timestamp)
    )
    assert live == before


# --------------------------------------------------------------------------- #
# 5 -- expected vs paper vs live
# --------------------------------------------------------------------------- #

TOLERANCES = {
    ComparisonMetric.TRADE_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.TRADE_PRICE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.FILL_QUANTITY: Tolerance(absolute=Decimal("0")),
    ComparisonMetric.FILL_PRICE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.SLIPPAGE: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.EXECUTION_LATENCY: Tolerance(absolute=Decimal("1")),
    ComparisonMetric.REALIZED_PNL: Tolerance(absolute=Decimal("0.01")),
    ComparisonMetric.EXPOSURE: Tolerance(relative=Decimal("0.001")),
}


def _submitted_at(orders: Any) -> dict[str, float]:
    """``order_id -> the instant the OMS created it``, supplied by the caller.

    Not derived inside the comparison layer, deliberately: an
    ``ExecutionReport`` carries only the post-latency stamp, so a library that
    reached for the order's creation time on its own would be reporting a queue
    time under the name of a latency. Here the caller states what it means.
    """

    return {str(order.order_id.value): order.created_at for order in orders}


def _exposure_of(result: BacktestResult) -> CurrencyAmounts:
    """Gross market value per settlement currency, from the book's own positions.

    Built by the caller, as the comparison layer requires: the figure a book of
    shares means by exposure is ``ExposureEngine``'s, and it is summed here per
    currency because ADR-0020 forbids one number across two.
    """

    amounts = CurrencyAmounts()
    for position in result.state.portfolio.positions.values():
        amounts = amounts.add(abs(position.market_value), position.currency)
    return amounts


def test_expected_and_paper_agree_on_every_metric(
    expected: BacktestResult, paper: BacktestResult
) -> None:
    result = compare_runs(
        observations_from_backtest(
            expected,
            ComparisonSource.EXPECTED,
            AlignmentKey.ASSET_AND_TIME,
            exposure=_exposure_of(expected),
            submitted_at=_submitted_at(expected.orders),
        ),
        observations_from_backtest(
            paper,
            ComparisonSource.PAPER,
            AlignmentKey.ASSET_AND_TIME,
            exposure=_exposure_of(paper),
            submitted_at=_submitted_at(paper.orders),
        ),
        TOLERANCES,
    )

    assert result.agrees
    assert result.material == ()
    assert result.missing == ()


def test_the_live_run_is_compared_through_its_own_normalized_broker_state(
    expected: BacktestResult, paper: BacktestResult, live: LiveRunState
) -> None:
    three_way = compare_expected_paper_live(
        observations_from_backtest(
            expected,
            ComparisonSource.EXPECTED,
            AlignmentKey.ASSET_AND_TIME,
            exposure=_exposure_of(expected),
            submitted_at=_submitted_at(expected.orders),
        ),
        observations_from_backtest(
            paper,
            ComparisonSource.PAPER,
            AlignmentKey.ASSET_AND_TIME,
            exposure=_exposure_of(paper),
            submitted_at=_submitted_at(paper.orders),
        ),
        observations_from_broker(
            live.broker,
            ComparisonSource.LIVE,
            AlignmentKey.ASSET_AND_TIME,
            realized_pnl=live.run.pipeline.portfolio.realized_pnl,
            exposure=_exposure_of(BacktestResult(live.run)),
        ),
        TOLERANCES,
    )

    assert three_way.expected_vs_paper.agrees
    # A venue reports orders and fills, not AlphaLab trades, so the trade
    # metrics against it are not comparable rather than failing.
    trade_entries = three_way.expected_vs_live.entries_for(ComparisonMetric.TRADE_QUANTITY)
    assert all(entry.outcome is ComparisonOutcome.NOT_COMPARABLE for entry in trade_entries)


def test_a_venues_unmeasured_slippage_stays_missing_and_never_becomes_zero(
    expected: BacktestResult, live: LiveRunState
) -> None:
    result = compare_runs(
        observations_from_backtest(
            expected, ComparisonSource.EXPECTED, AlignmentKey.ASSET_AND_TIME
        ),
        observations_from_broker(live.broker, ComparisonSource.LIVE, AlignmentKey.ASSET_AND_TIME),
        TOLERANCES,
    )

    slippage = result.entries_for(ComparisonMetric.SLIPPAGE)
    assert slippage
    assert all(entry.observed is None for entry in slippage)
    assert all(entry.outcome is ComparisonOutcome.MISSING_OBSERVED for entry in slippage)


def test_pnl_is_compared_per_currency_and_carries_the_books_own_figure(
    expected: BacktestResult, paper: BacktestResult
) -> None:
    result = compare_runs(
        observations_from_backtest(
            expected, ComparisonSource.EXPECTED, AlignmentKey.ASSET_AND_TIME
        ),
        observations_from_backtest(paper, ComparisonSource.PAPER, AlignmentKey.ASSET_AND_TIME),
        TOLERANCES,
    )

    entries = result.entries_for(ComparisonMetric.REALIZED_PNL)
    assert [entry.key for entry in entries] == ["USD"]
    assert entries[0].expected == expected.state.portfolio.realized_pnl.of("USD")


def test_latency_is_derived_from_supplied_submission_instants_only(
    expected: BacktestResult,
) -> None:
    """Without them every fill reports no latency, rather than zero."""

    without = observations_from_backtest(expected, ComparisonSource.EXPECTED, AlignmentKey.ORDER_ID)
    assert without.fills is not None
    assert all(fill.latency_seconds is None for fill in without.fills)

    submitted = {str(order.order_id.value): order.created_at for order in expected.orders}
    with_latency = observations_from_backtest(
        expected, ComparisonSource.EXPECTED, AlignmentKey.ORDER_ID, submitted_at=submitted
    )
    assert with_latency.fills is not None
    assert all(fill.latency_seconds is not None for fill in with_latency.fills)
    assert all(isinstance(fill.latency_seconds, Decimal) for fill in with_latency.fills)


# --------------------------------------------------------------------------- #
# 6 -- reconciliation against the venue the live run actually used
# --------------------------------------------------------------------------- #

RECONCILIATION_TOLERANCES = ReconciliationTolerances(
    order_quantity=Tolerance(absolute=Decimal("0")),
    order_price=Tolerance(absolute=Decimal("0.01")),
    fill_quantity=Tolerance(absolute=Decimal("0")),
    fill_price=Tolerance(absolute=Decimal("0.01")),
    commission=Tolerance(absolute=Decimal("0.01")),
    position_quantity=Tolerance(absolute=Decimal("0.000001")),
    cash=Tolerance(absolute=Decimal("1000000")),
)


def test_a_live_run_reconciles_against_the_venue_it_traded_on(live: LiveRunState) -> None:
    result = reconcile_execution_state(
        live.run.pipeline,
        live.broker,
        live.mapping,
        SymbolMapping.identity(),
        RECONCILIATION_TOLERANCES,
    )

    assert result.mismatches_in(MismatchCategory.MISSING_EXPECTED_ORDER) == ()
    assert result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_ORDER) == ()
    assert result.mismatches_in(MismatchCategory.MISSING_EXPECTED_FILL) == ()
    assert result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_FILL) == ()
    assert result.compared_orders == 2
    assert result.compared_fills == 2


def test_a_fill_the_run_never_applied_is_found_by_reconciliation(
    live: LiveRunState,
) -> None:
    """The break a live run is supposed to surface rather than swallow."""

    unapplied = replace(
        live,
        run=replace(
            live.run,
            pipeline=replace(
                live.run.pipeline,
                execution=replace(
                    live.run.pipeline.execution,
                    reports=type(live.run.pipeline.execution.reports)(),
                ),
            ),
        ),
    )

    result = reconcile_execution_state(
        unapplied.run.pipeline,
        unapplied.broker,
        unapplied.mapping,
        SymbolMapping.identity(),
        RECONCILIATION_TOLERANCES,
    )

    assert len(result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_FILL)) == 2


def test_reconciliation_is_stable_and_mutates_neither_side(live: LiveRunState) -> None:
    before_pipeline, before_broker = live.run.pipeline, live.broker

    first = reconcile_execution_state(
        live.run.pipeline,
        live.broker,
        live.mapping,
        SymbolMapping.identity(),
        RECONCILIATION_TOLERANCES,
    )
    second = reconcile_execution_state(
        live.run.pipeline,
        live.broker,
        live.mapping,
        SymbolMapping.identity(),
        RECONCILIATION_TOLERANCES,
    )

    assert first == second
    assert live.run.pipeline == before_pipeline
    assert live.broker == before_broker


def test_the_two_reconciliation_authorities_answer_different_questions(
    live: LiveRunState,
) -> None:
    """``broker.reconcile`` compares the mirror to the venue; this compares the
    book to the mirror. An empty result from one says nothing about the other."""

    from alphalab.broker.reconciliation import reconcile as reconcile_mirror

    mirror = reconcile_mirror(
        live.broker,
        list(live.broker.orders.values()),
        list(live.broker.positions.values()),
        live.broker.account,
    )
    book = reconcile_execution_state(
        live.run.pipeline,
        live.broker,
        live.mapping,
        SymbolMapping.identity(),
        RECONCILIATION_TOLERANCES,
    )

    assert mirror.reconciled
    assert book.reconciled
    # Two result types, because they answer two questions.
    assert type(mirror).__name__ != type(book).__name__
