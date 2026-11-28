"""
AlphaLab Examples
=================

Example 81 : Search, Train and Trade a Model by Its Artifact

Difficulty : Advanced

Estimated Time : 20 minutes

Prerequisites
-------------

✓ Example 73 (train a model on the past and trade it forward)
✓ Example 80 (a ledgered sweep)

Topics
------

• Point-in-time features beside triple-barrier meta-labels, joined into a panel
  in which every label knows when it became known
• A gradient-boosted classifier from the core's reference models, fitted on
  purged training rows weighed by their uniqueness
• A successive-halving search through the trial ledger: purged
  cross-validation over backtests, every configuration a counted trial, the
  lockbox held back from all of them
• The selected model, trained on everything known before the lockbox, filed by
  its bytes and named among the strategy's parameters
• The strategy restoring that artifact and trading the lockbox, read once;
  another artifact is another run's inputs

What this shows
---------------

Plan scenario S-07 (v4.3, ADR-0064). A momentum rule takes a side at every
session; a meta-model learns when the side pays -- whether the profit barrier
of a triple barrier is touched before its stop -- from two features known at
the event. Successive halving searches the model's depth and learning rate,
raising the number of trees round by round, and every configuration any round
evaluated is a trial in the ledger, scored by purged cross-validation over
backtests. The chosen configuration is trained once more on every label known
before the lockbox, filed in an artifact store, and traded by its artifact id.
The prices are **synthetic**; nothing here says the model predicts anything.
``tests/integration/test_supervised_research_end_to_end.py`` holds it.

Run

    python examples/81_search_train_and_trade_a_model.py
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, ingest_rows, observe, to_market_dataset
from alphalab.backtesting import BacktestResult, ExecutionMode, RunConfig
from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
)
from alphalab.backtesting.schedule import EMPTY_SCHEDULE
from alphalab.common.types import ParamValue
from alphalab.core.enums import AssetType
from alphalab.data import (
    REFUSE_EVERYTHING,
    DataAssetClass,
    Dataset,
    IngestionRequest,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.time import BarStamp
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import FillTiming
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.factor_library import FeatureDefinition, FeatureField, FeatureKind
from alphalab.factor_library.compute import compute_feature, compute_panel
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.instrument.snapshot import registry_digest
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    HalvingRecord,
    LifecycleState,
    ReproducibilityManifest,
    StrategyFingerprint,
    assess_reproducibility,
    code_identity_for,
    digest_run,
    fingerprint_for_version,
    get_strategy_version,
    manifest_for_run,
    register_strategy,
    research_configuration,
    run_successive_halving,
    running_build,
    running_engine,
)
from alphalab.lifecycle.designs import CrossValidated, Lockbox, PurgeSpec, TrialWindow
from alphalab.lifecycle.experiments import (
    ExperimentSpec,
    RunnerRegistry,
    SequentialExecutor,
    TrialInputs,
    TrialLedger,
    TrialLimits,
    TrialSpec,
    dataset_identity,
    read_lockbox,
)
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.ml import (
    BoostingLoss,
    FeatureMatrix,
    GradientBoostedTrees,
    Model,
    PointInTimePanel,
    RollingVolatility,
    SampleWeights,
    TrainingRecord,
    TripleBarrier,
    VerticalOutcome,
    meta_labels,
    point_in_time_panel,
    restore_model,
    train_model,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.model_registry.registry import ArtifactRef
from alphalab.portfolio.account import Account
from alphalab.research import CVMethod, ParameterSpace, SuccessiveHalving
from alphalab.research.selection import SelectionMeasure
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
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    IntentKind,
    StrategyContext,
    StrategyDefinition,
    context_factory,
)
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.registry import StrategyClassRegistry, runtime_for
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

STRATEGY_ID = "EX81-MODEL"
SYMBOL = "SYN"
PROVIDER = "synthetic"
SEED = 81
START_CASH = Decimal("1000000.00")
DAY = 86_400.0
FIRST_BAR = 1_704_153_600.0
SESSIONS = 260
#: From this session on, the data is the lockbox: no trial of the search sees it.
LOCKBOX_SESSION = 200
LOCKBOX_START = FIRST_BAR + LOCKBOX_SESSION * DAY
#: The last session before the lockbox: no model the search or the final run
#: fits holds a label realized later, since a label ending at the lockbox's
#: first session already reads a held-back close.
KNOWN_BY = LOCKBOX_START - DAY
#: The triple barrier's vertical barrier, and the purge's horizon: no label of an
#: event reaches further forward, so purging by it leaves no training label
#: reaching into the window it is scored on.
HORIZON = 10
#: Each trial's run begins this many sessions before the window it is scored on,
#: so the features have their history when scoring starts.
WARM_UP = 20

INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XSYN", "USD", aliases={PROVIDER: SYMBOL})
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XSYN",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=REGISTRY,
    provider=PROVIDER,
)
CONTEXTS = context_factory(FixedClock(FIRST_BAR), DiscardingLogger())

#: The two features, defined once and read by the study and the strategy alike.
FEATURES = (
    FeatureDefinition("mom_5", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5),
    FeatureDefinition("z_10", FeatureKind.ROLLING_ZSCORE, FeatureField.CLOSE, window=10),
)
FEATURE_NAMES = tuple(definition.feature_id for definition in FEATURES)

#: When a momentum side pays: its profit barrier before its stop, each one
#: deviation of the last twenty returns, within ten sessions.
BARRIER = TripleBarrier(
    horizon=HORIZON,
    upper=1.0,
    lower=1.0,
    volatility=RollingVolatility(window=20),
    at_vertical=VerticalOutcome.SIGN,
)

#: Where every model this example trains is filed, by its bytes.
STORE = MemoryArtifactStore()


# --------------------------------------------------------------------------- #
# 1. Data
# --------------------------------------------------------------------------- #


def closes(moved_after: int | None = None) -> list[Decimal]:
    """A synthetic path whose steps persist a little, to the cent: integer arithmetic only.

    ``moved_after`` moves every close from that session on, for the counterfactual
    the test holds the panel to.
    """

    state, drift = SEED, 0
    out, price = [], Decimal("100")
    for index in range(SESSIONS):
        state = (1103515245 * state + 12345) % 2**31
        shock = (state >> 8) % 9 - 4
        drift = (3 * drift + shock) // 4
        price = (price * (1 + Decimal(drift + shock) / Decimal(400))).quantize(Decimal("0.01"))
        moved = moved_after is not None and index >= moved_after
        out.append(price * Decimal("1.25") if moved else price)
    return out


def dataset(moved_after: int | None = None) -> Dataset:
    rows = [
        {
            "timestamp": str(FIRST_BAR + index * DAY),
            "symbol": SYMBOL,
            "open": str(close),
            "high": str(close),
            "low": str(close),
            "close": str(close),
            "volume": "1000000",
        }
        for index, close in enumerate(closes(moved_after))
    ]
    payload = json.dumps(rows, sort_keys=True).encode("utf-8")
    request = IngestionRequest(
        name="EX81-SYNTHETIC",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "synthetic://example-81", payload, FIRST_BAR, "application/json"
        ),
        frequency=TimeFrequency.DAILY,
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=REFUSE_EVERYTHING,
        price_basis=PriceBasis.RAW,
    )
    return ingest_rows(rows, request).dataset


DATA = dataset()
MARKET = to_market_dataset(DATA, NORMALIZATION)


# --------------------------------------------------------------------------- #
# 2. The research data: features known at each event, beside the label it earned
# --------------------------------------------------------------------------- #


def research_panel(data: Dataset) -> PointInTimePanel:
    """Meta-labels of the momentum rule's side, joined to the features known at each event."""

    prices = observe(data, FeatureField.CLOSE)
    features = {definition.feature_id: compute_panel(definition, prices) for definition in FEATURES}
    sides = {
        (SYMBOL, at): 1.0 if value > 0.0 else -1.0
        for at, row in features["mom_5"].rows.items()
        if (value := row.get(SYMBOL)) is not None and value != 0.0
    }
    return point_in_time_panel(features, meta_labels(prices, BARRIER, sides))


PANEL = research_panel(DATA)


# --------------------------------------------------------------------------- #
# 3. The model, and the search over it
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Search:
    """A configuration of the meta-model: what the search varies, and its budget."""

    depth: int
    learning_rate: float
    trees: int


#: The model the search trains and the strategy restores: the core's reference
#: ensemble. An adapter's model takes its place by replacing both names
#: (alphalab-models' suite runs this scenario with scikit-learn's).
MODEL: type[Model] = GradientBoostedTrees


def model_of(search: Search) -> Model:
    return GradientBoostedTrees(
        loss=BoostingLoss.LOGISTIC,
        trees=search.trees,
        learning_rate=search.learning_rate,
        depth=search.depth,
        minimum_leaf=8,
        subsample=0.8,
    )


def train(search: Search, rows: PointInTimePanel, seed: int) -> TrainingRecord:
    """Fit on ``rows`` weighed by their uniqueness among themselves, and file the model."""

    _, record = train_model(
        model_of(search),
        rows.training_set(weights=SampleWeights.UNIQUENESS),
        seed=seed,
        store=STORE,
    )
    return record


# --------------------------------------------------------------------------- #
# 4. The strategy: the same features, the model restored from its artifact
# --------------------------------------------------------------------------- #


class ModelStrategy(BaseStrategy):
    """Takes the momentum rule's side when the meta-model gives it better than even odds.

    The model is restored from the artifact its parameters name, so the run's
    fingerprint carries the model. The features are the study's definitions
    over the closes the strategy has been shown, so they cannot see a later
    close. Orders are placed only from ``trade_from`` on.
    """

    def __init__(self, strategy_id: str, parameters: Mapping[str, ParamValue]) -> None:
        self._strategy_id = strategy_id
        self._artifact = str(parameters["model"])
        self.model: Model = restore_model(MODEL, STORE, self._artifact)
        self._trade_from = float(parameters["trade_from"])
        self._size = Decimal(str(parameters["size"]))
        self._times: list[float] = []
        self._closes: list[float] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        self._times.append(bar.timestamp)
        self._closes.append(float(bar.close))
        frame = ObservationFrame(
            FeatureField.CLOSE,
            {SYMBOL: ObservationSeries(SYMBOL, tuple(self._times), tuple(self._closes))},
            "UTC",
        )
        values = []
        for definition in FEATURES:
            (series,) = compute_feature(definition, frame)
            if not series.timestamps or series.timestamps[-1] != bar.timestamp:
                return ()
            values.append(series.values[-1])
        if bar.timestamp < self._trade_from:
            return ()
        side = 1 if values[0] > 0.0 else -1 if values[0] < 0.0 else 0
        (odds,) = self.model.predict(FeatureMatrix(FEATURE_NAMES, (tuple(values),)))
        target = self._size * side if side and odds > 0.5 else Decimal(0)
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=INSTRUMENT.asset_id,
                target=target,
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )

    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> dict[str, Any]:
        return {"times": list(self._times), "closes": list(self._closes), "model": self._artifact}

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"ModelStrategy cannot read state version {version}")
        if payload["model"] != self._artifact:
            raise ValueError("the captured model is not the artifact this strategy was built with")
        self._times = [float(value) for value in payload["times"]]
        self._closes = [float(value) for value in payload["closes"]]


def model_factory(strategy_id: str, parameters: Mapping[str, ParamValue], /) -> StrategyProtocol:
    return ModelStrategy(strategy_id, parameters)


CLASSES = StrategyClassRegistry().register(STRATEGY_ID, model_factory)


def definition(artifact_id: str, trade_from: float) -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id=STRATEGY_ID,
        name="Meta-model on a momentum rule",
        version="1",
        author="examples",
        description="a momentum side taken when the meta-model gives it better than even odds",
        parameters={"model": artifact_id, "trade_from": trade_from, "size": 1000},
    )


def running(artifact_id: str, trade_from: float) -> RuntimeState:
    state = runtime_for(CLASSES, (definition(artifact_id, trade_from),))
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


def run_config() -> RunConfig:
    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX81", "USD", "Example 81", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("2")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, "UTC"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01"))),
            instruments=REGISTRY,
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        seed=SEED,
        start_timestamp=FIRST_BAR - 1.0,
    )


# --------------------------------------------------------------------------- #
# 5. The experiment: every configuration a trial, scored by purged CV
# --------------------------------------------------------------------------- #


def fit_rows(window: TrialWindow | None) -> PointInTimePanel:
    """What a trial trains on: rows it may fit, every label known before the lockbox.

    A fold's rows start at its fit instants, purged by the experiment's design
    of labels reaching its scored window. The panel was computed from the whole
    dataset, so the runner holds the lockbox back from labels too: a row whose
    label is realized in it is no trial's, wherever it starts. The lockbox read
    has no window, and fits every label known before the lockbox.
    """

    if window is None:
        return PANEL.known_by(KNOWN_BY)
    return PANEL.at(window.fit).known_by(KNOWN_BY)


def build(trial: TrialSpec) -> TrialInputs:
    """The runner: train on what the trial may know, and trade what it is scored on."""

    search: Search = trial.configuration
    trade_from = LOCKBOX_START if trial.window is None else trial.window.evaluate[0]
    record = train(search, fit_rows(trial.window), trial.trial_seed)
    return TrialInputs(
        config=run_config(),
        dataset=MARKET,
        strategy_state=running(record.artifact_id, trade_from),
        context_factory=CONTEXTS,
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


RUNNERS = RunnerRegistry().register("s07", build)
_CONFIG = run_config()
SPEC = ExperimentSpec(
    name="s07-meta-model",
    hypothesis="a meta-model of a momentum rule's side improves on taking every side",
    strategy="s07-meta-model@" + "81" * 32,
    configuration_type=Search,
    space=ParameterSpace.grid({"depth": [1, 2, 3], "learning_rate": [0.1, 0.3]}),
    dataset=dataset_identity(MARKET),
    schedule=EMPTY_SCHEDULE.binding,
    instruments=registry_digest(REGISTRY),
    execution=execution_assumptions(_CONFIG.pipeline, _CONFIG.fill_policy),
    capture=CaptureSpec(
        ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE),
        trace=False,
    ),
    retention=RetentionPolicy(market_history=24, steps=8, audit_events=32, results=8),
    validation=CrossValidated(
        3,
        CVMethod.PURGED,
        WARM_UP,
        SelectionMeasure.MEAN,
        PurgeSpec(HORIZON, 0.0),
        Lockbox(LOCKBOX_START, None),
    ),
    seed=SEED,
    parent_id=None,
    limits=TrialLimits(None),
)
#: Three rounds: every configuration with 10 trees, the best half with 30, the
#: best half of those with 60.
HALVING = SuccessiveHalving(budgets=(10, 30, 60), keep=0.5, parameter="trees")


def ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def search(trials: TrialLedger) -> HalvingRecord:
    return run_successive_halving(
        SPEC, HALVING, SelectionMeasure.MEAN, RUNNERS, "s07", trials, SequentialExecutor()
    )


# --------------------------------------------------------------------------- #
# 6. The chosen model, traded by its artifact
# --------------------------------------------------------------------------- #


def final(record: HalvingRecord, seed: int = SEED) -> TrainingRecord:
    """The selected configuration, trained on every label known before the lockbox."""

    assert record.selected is not None
    return train(Search(**record.selected), fit_rows(None), seed)


def trade(artifact_id: str) -> BacktestResult:
    return backtest(
        run_config(), DATA, running(artifact_id, LOCKBOX_START), CONTEXTS, NORMALIZATION
    )


def fingerprint(artifact_id: str) -> StrategyFingerprint:
    state, reference = register_strategy(
        LifecycleState(), "ex81-meta-model", definition(artifact_id, LOCKBOX_START), 2.0
    )
    version = get_strategy_version(state.strategies, reference.name, reference.version)
    code = code_identity_for(
        CLASSES.require(STRATEGY_ID),
        "alphalab-examples",
        "81",
        {"81_search_train_and_trade_a_model.py": Path(__file__).read_bytes()},
    )
    research = research_configuration({"validation": "purged CV, successive halving, lockbox"})
    return fingerprint_for_version(version, code, NO_DEPENDENCIES, research, running_engine())


def manifest(artifact_id: str, result: BacktestResult) -> ReproducibilityManifest:
    return manifest_for_run(
        result, DATA, fingerprint(artifact_id), running_engine(), build=running_build()
    )


def instance_of(result: BacktestResult) -> ModelStrategy:
    instance = result.state.strategy.strategies[STRATEGY_ID].instance
    assert isinstance(instance, ModelStrategy)
    return instance


# --------------------------------------------------------------------------- #
# The walk-through
# --------------------------------------------------------------------------- #


def main() -> None:
    print("=" * 72)
    print("AlphaLab Example 81 : Search, Train and Trade a Model by Its Artifact")
    print("=" * 72)
    print(f"\n  dataset    {DATA.dataset_version}")
    print(
        f"  panel      {len(PANEL)} labelled events, {PANEL.missing} without every feature; "
        f"{len(fit_rows(None))} known before the lockbox"
    )

    trials = ledger()
    record = search(trials)
    for each in record.rounds:
        print(
            f"  round      {each.budget:>2} trees: {len(each.scores)} configurations, "
            f"{each.record.counts.recorded} trials"
        )
    print(
        f"  searched   {record.configurations} configurations in {record.counts.recorded} "
        f"counted trials; selected {dict(record.selected or {})}"
    )

    last = record.rounds[-1].spec
    chosen = next(
        trial for trial in last.trials() if trial.fold == 0 and trial.candidate == record.selected
    )
    read = read_lockbox(last, RUNNERS, "s07", trials, chosen)
    print(f"  lockbox    read once: {read.record.outcome.name}")

    filed = final(record)
    result = trade(filed.artifact_id)
    restored = instance_of(result).model.capture()
    print(f"\n  model      {filed.artifact_id[:40]}…, trained on seed {filed.seed}")
    filed_bytes = STORE.get(ArtifactRef(uri=filed.artifact_id))
    print(f"  restored   {'byte for byte' if restored == filed_bytes else 'CHANGED'}")
    print(f"  traded     {len(result.fills)} fills; equity {result.valuation.equity}")
    again = trade(filed.artifact_id)
    print(f"  rerun      {'identical' if digest_run(again) == digest_run(result) else 'DIFFERENT'}")

    other = final(record, seed=SEED + 1)
    assessed = assess_reproducibility(
        manifest(filed.artifact_id, result), manifest(other.artifact_id, trade(other.artifact_id))
    )
    print(f"  another    artifact, another seed: {assessed.rerun.name}")


if __name__ == "__main__":
    main()
