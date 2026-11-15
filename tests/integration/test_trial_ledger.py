"""The trial ledger records a search's whole population (ADR-0072, M4's exit gate).

Forty-eight trials of one strategy, each a candidate typed as the experiment's
configuration. Most complete; two fail in the runner, two fail in the strategy,
two are given another dataset and refused, and two go insolvent. Every one is
counted:

* the counts are exact, and the completed trials form the experiment's matrix;
* each record holds digests and the series' identity, never the result, and its
  series is filed beside it;
* a resume runs only what is missing, and a trial that leaves another record
  is refused as nondeterminism;
* an interruption records nothing;
* the lockbox is held back from every trial, read once, and read again only by
  a child experiment that states why;
* the specification states everything a trial turns on, and its identity moves
  with each part.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.schedule import EMPTY_SCHEDULE
from alphalab.common.exceptions import AlphaLabRegistryError, AlphaLabValidationError
from alphalab.lifecycle.experiments import (
    SEARCH,
    ExperimentError,
    ExperimentSpec,
    FullSample,
    LedgerCount,
    Lockbox,
    NondeterministicTrialError,
    RunnerRegistry,
    SequentialExecutor,
    TrialInputs,
    TrialLedger,
    TrialLimits,
    TrialOutcome,
    TrialSpec,
    dataset_identity,
    read_lockbox,
    run_experiment,
    run_trial,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.walk_forward_optimization import ParameterSpace, _render, _seed
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
RECORDS = 80
MIDS = [Decimal(100) + Decimal(2 * i) for i in range(RECORDS)]
DATASET = dataset_of_quotes(ASSET_ID, MIDS, dataset_id="LEDGER")
FOREIGN = dataset_of_quotes(ASSET_ID, MIDS[:-1], dataset_id="ELSEWHERE")
FINGERPRINT = "bands@" + "ab" * 32


@dataclass(frozen=True)
class Plan:
    """The configuration every candidate is typed as."""

    window: int
    size: int
    mode: str


class _Crashing(ScriptedStrategy):
    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.timestamp >= 12.0:
            raise RuntimeError("the strategy's own defect")
        return super().on_quote(context, event)


def _plan(window: int, size: int) -> dict[float, Decimal]:
    return {
        2.0 + i: Decimal(size) if (i // window) % 2 == 0 else -Decimal(size)
        for i in range(0, RECORDS, window)
    }


def build(trial: TrialSpec) -> TrialInputs:
    """The registered runner: a trial's inputs from its typed configuration."""

    plan: Plan = trial.configuration
    if plan.mode == "raise":
        raise ValueError("the runner's own defect")
    config = backtest_config(STRATEGY_ID)
    dataset = DATASET
    strategy: ScriptedStrategy = ScriptedStrategy(
        STRATEGY_ID, ASSET_ID, _plan(plan.window, plan.size)
    )
    if plan.mode == "crash":
        strategy = _Crashing(STRATEGY_ID, ASSET_ID, _plan(plan.window, plan.size))
    elif plan.mode == "foreign":
        dataset = FOREIGN
    elif plan.mode == "squeeze":
        # Short eight at 100 on 1,000 of capital into a price that doubles.
        config = backtest_config(STRATEGY_ID, starting_cash=Decimal(1000), compile_analytics=False)
        strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal(-8)})
    return TrialInputs(
        config=config,
        dataset=dataset,
        strategy_state=running_strategy_state(STRATEGY_ID, strategy),
        context_factory=context_factory,
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


def _candidates() -> list[dict[str, Any]]:
    normal = [
        {"window": window, "size": size, "mode": "normal"}
        for window in range(2, 12)
        for size in (1, 2, 3, 4)
    ]
    odd = [
        {"window": window, "size": 1, "mode": mode}
        for mode in ("raise", "crash", "foreign", "squeeze")
        for window in (13, 14)
    ]
    return normal + odd


def _spec(**changed: Any) -> ExperimentSpec:
    config = backtest_config(STRATEGY_ID)
    fields: dict[str, Any] = {
        "name": "bands",
        "hypothesis": "short windows pay on a rising market",
        "strategy": FINGERPRINT,
        "configuration_type": Plan,
        "space": ParameterSpace.of(_candidates()),
        "dataset": dataset_identity(DATASET),
        "schedule": EMPTY_SCHEDULE.binding,
        "instruments": None,
        "execution": execution_assumptions(config.pipeline, config.fill_policy),
        "capture": CaptureSpec(
            ReturnExtractor(
                EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE
            ),
            trace=True,
        ),
        "retention": RetentionPolicy(market_history=24, steps=10, audit_events=64, results=64),
        "validation": FullSample(None),
        "seed": 7,
        "parent_id": None,
        "limits": TrialLimits(None),
    }
    fields.update(changed)
    return ExperimentSpec(**fields)


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


REGISTRY = RunnerRegistry().register("bands", build)


@pytest.fixture(scope="module")
def experiment() -> tuple[ExperimentSpec, TrialLedger, Any]:
    spec = _spec()
    ledger = _ledger()
    record = run_experiment(spec, REGISTRY, "bands", ledger, SequentialExecutor())
    return spec, ledger, record


# --- the exit gate: forty-eight trials, every one counted -------------------------------


def test_every_trial_of_forty_eight_is_counted_failures_and_insolvency_included(
    experiment: tuple[ExperimentSpec, TrialLedger, Any],
) -> None:
    spec, ledger, record = experiment

    assert len(spec.trials()) == 48
    assert record.counts == LedgerCount(
        completed=40, insolvent=2, strategy_failed=2, refused=2, failed=2
    )
    assert record.counts.recorded == 48
    assert len(record.trial_record_ids) == 48
    assert record.matrix_id is not None
    outcomes = {trial.label: ledger.find(trial.trial_id) for trial in spec.trials()}
    by_mode = {
        mode: {
            held.outcome for label, held in outcomes.items() if f"mode='{mode}'" in label and held
        }
        for mode in ("normal", "raise", "crash", "foreign", "squeeze")
    }
    assert by_mode == {
        "normal": {TrialOutcome.COMPLETED},
        "raise": {TrialOutcome.FAILED},
        "crash": {TrialOutcome.STRATEGY_FAILED},
        "foreign": {TrialOutcome.REFUSED},
        "squeeze": {TrialOutcome.INSOLVENT},
    }


def test_a_record_holds_digests_and_the_series_never_the_result(
    experiment: tuple[ExperimentSpec, TrialLedger, Any],
) -> None:
    spec, ledger, _ = experiment
    completed = next(t for t in spec.trials() if "mode='normal'" in t.label)
    insolvent = next(t for t in spec.trials() if "mode='squeeze'" in t.label)
    failed = next(t for t in spec.trials() if "mode='raise'" in t.label)

    held = ledger.find(completed.trial_id)
    assert held is not None
    assert held.result_id and held.trace_id and held.provenance_id and held.series_id
    instants, returns = ledger.series(held.series_id)
    assert len(returns) == len(instants) - 1 == RECORDS
    sunk = ledger.find(insolvent.trial_id)
    assert sunk is not None and sunk.insolvent_at is not None and sunk.series_id is not None
    broken = ledger.find(failed.trial_id)
    assert broken is not None
    assert broken.result_id is None and broken.series_id is None
    assert broken.diagnostics == "ValueError: the runner's own defect"
    refused = ledger.find(next(t for t in spec.trials() if "mode='foreign'" in t.label).trial_id)
    assert refused is not None and "dataset" in refused.diagnostics


def test_a_capture_refused_at_insolvency_is_insolvent_and_keeps_no_series() -> None:
    """Under ``Insolvency.REFUSE`` the capture raises at the wipe-out (ADR-0072, settled 3).

    The trial is ``INSOLVENT``, as a series truncated there is, but it ran to no
    end: it has no series and no result, and enters no matrix.
    """

    spec = _spec(
        space=ParameterSpace.of([c for c in _candidates() if c["mode"] == "squeeze"]),
        capture=CaptureSpec(
            ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.REFUSE),
            trace=True,
        ),
    )
    ledger = _ledger()
    record = run_experiment(spec, REGISTRY, "bands", ledger, SequentialExecutor())

    assert record.counts == LedgerCount(
        completed=0, insolvent=2, strategy_failed=0, refused=0, failed=0
    )
    assert record.matrix_id is None
    for trial in spec.trials():
        held = ledger.find(trial.trial_id)
        assert held is not None and held.outcome is TrialOutcome.INSOLVENT
        assert held.series_id is None and held.result_id is None
        assert held.diagnostics.startswith("InsolventCaptureError: ")


def test_a_trial_draws_the_walk_forward_searchs_own_seed() -> None:
    spec = _spec()
    for trial in spec.trials()[:5]:
        assert trial.trial_seed == _seed(7, 0, SEARCH, _render(trial.candidate))
        assert trial.configuration == Plan(**trial.candidate)


# --- resume, nondeterminism and interruption -----------------------------------------------


def test_a_resume_runs_only_what_is_missing_and_records_the_same_experiment(
    experiment: tuple[ExperimentSpec, TrialLedger, Any],
) -> None:
    spec, ledger, record = experiment
    calls: list[str] = []

    def counting(trial: TrialSpec) -> TrialInputs:
        calls.append(trial.label)
        return build(trial)

    again = run_experiment(
        spec, RunnerRegistry().register("bands", counting), "bands", ledger, SequentialExecutor()
    )
    assert calls == []
    assert again == record
    assert again.experiment_record_id == record.experiment_record_id


def test_an_interruption_records_nothing_and_the_resume_finishes_the_search() -> None:
    spec = _spec(space=ParameterSpace.of(_candidates()[:6]))
    ledger = _ledger()
    calls = 0

    def interrupted(trial: TrialSpec) -> TrialInputs:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise KeyboardInterrupt
        return build(trial)

    with pytest.raises(KeyboardInterrupt):
        run_experiment(
            spec,
            RunnerRegistry().register("bands", interrupted),
            "bands",
            ledger,
            SequentialExecutor(),
        )
    held = [ledger.find(trial.trial_id) for trial in spec.trials()]
    assert [record is not None for record in held] == [True, True, True, False, False, False]

    finished = run_experiment(spec, REGISTRY, "bands", ledger, SequentialExecutor())
    assert finished.counts.recorded == 6
    whole = run_experiment(spec, REGISTRY, "bands", _ledger(), SequentialExecutor())
    assert finished == whole


def test_a_trial_that_leaves_another_record_is_refused_as_nondeterminism() -> None:
    spec = _spec(space=ParameterSpace.of(_candidates()[:2]))
    ledger = _ledger()
    run_experiment(spec, REGISTRY, "bands", ledger, SequentialExecutor())
    trial = spec.trials()[0]

    def drifting(trial: TrialSpec) -> TrialInputs:
        inputs = build(trial)
        return replace(
            inputs,
            strategy_state=running_strategy_state(
                STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, _plan(5, 9))
            ),
        )

    with pytest.raises(NondeterministicTrialError) as refused:
        ledger.keep(run_trial(spec, drifting, trial))
    assert refused.value.trial_id == trial.trial_id
    # The same trial run again, unchanged, is the same record, kept once.
    assert ledger.keep(run_trial(spec, build, trial)) == ledger.find(trial.trial_id)


# --- the lockbox ---------------------------------------------------------------------------


def test_the_lockbox_is_held_back_read_once_and_reread_only_by_a_declaring_child() -> None:
    start = 2.0 + 60
    spec = _spec(
        space=ParameterSpace.of(_candidates()[:3]), validation=FullSample(Lockbox(start, None))
    )
    ledger = _ledger()
    run_experiment(spec, REGISTRY, "bands", ledger, SequentialExecutor())
    trial = spec.trials()[0]
    held = ledger.find(trial.trial_id)
    assert held is not None and held.series_id is not None
    instants, _ = ledger.series(held.series_id)
    assert max(instants) < start, "no trial sees the lockbox"

    read = read_lockbox(spec, REGISTRY, "bands", ledger, trial)
    assert read.capture is not None and max(read.capture.instants) >= start
    with pytest.raises(ExperimentError, match="has been read"):
        read_lockbox(spec, REGISTRY, "bands", ledger, trial)
    child = _spec(
        space=ParameterSpace.of(_candidates()[:3]),
        validation=FullSample(Lockbox(start, "a revised cost model, stated before the read")),
        parent_id=spec.experiment_id,
    )
    read_lockbox(child, REGISTRY, "bands", ledger, child.trials()[0])
    lockbox_id = next(iter(ledger._store.identities("lockbox_read")))[:64]
    assert ledger.lockbox_reads(lockbox_id) == 2


def test_a_lockbox_reread_needs_a_parent() -> None:
    with pytest.raises(ExperimentError, match="names its parent"):
        _spec(validation=FullSample(Lockbox(50.0, "again")))


# --- the specification and its identity ---------------------------------------------------


@pytest.mark.parametrize(
    "changed",
    [
        {"hypothesis": "long windows pay"},
        {"seed": 8},
        {"space": ParameterSpace.of(_candidates()[:47])},
        {"retention": RetentionPolicy()},
        {"validation": FullSample(Lockbox(70.0, None))},
        {"limits": TrialLimits(30.0)},
        {"name": "bands-2"},
        {"parent_id": "cd" * 32},
    ],
)
def test_the_experiment_id_moves_with_every_part_of_the_specification(
    changed: dict[str, Any],
) -> None:
    assert _spec(**changed).experiment_id != _spec().experiment_id


def test_a_trial_id_is_its_experiment_candidate_fold_and_phase() -> None:
    first, second = _spec().trials()[:2]
    assert first.trial_id != second.trial_id
    assert _spec().trials()[0].trial_id == first.trial_id
    assert _spec(seed=8).trials()[0].trial_id != first.trial_id


@pytest.mark.parametrize(
    ("changed", "match"),
    [
        ({"strategy": "bands"}, "fingerprint"),
        ({"configuration_type": dict}, "dataclass"),
        ({"hypothesis": " "}, "hypothesis"),
        ({"space": ParameterSpace.of([{"window": 2, "size": 1, "mode": "normal", "x": 1}])}, "x"),
        ({"instruments": "not-a-digest"}, "instruments"),
        ({"seed": 1.5}, "seed"),
    ],
)
def test_a_specification_that_is_not_one_is_refused(changed: dict[str, Any], match: str) -> None:
    with pytest.raises(ExperimentError, match=match):
        _spec(**changed)


def test_a_runner_is_looked_up_by_name_never_resolved() -> None:
    """The ledger's registry is the shared one (D-54-11), and refuses as it does."""

    with pytest.raises(AlphaLabRegistryError, match="never resolved to code"):
        REGISTRY.runner("alphalab.lifecycle.experiments.run_trial")
    with pytest.raises(AlphaLabRegistryError, match="already holds"):
        REGISTRY.register("bands", build)
    with pytest.raises(AlphaLabRegistryError, match="never resolved to code"):
        run_experiment(_spec(), REGISTRY, "elsewhere", _ledger(), SequentialExecutor())


def test_a_runner_raising_alphalabs_own_error_is_refused_and_any_other_error_failed() -> None:
    """ADR-0072, settled 3: AlphaLab's refusal is REFUSED; the researcher's defect is FAILED."""

    def refusing(trial: TrialSpec) -> TrialInputs:
        raise AlphaLabValidationError("the venue's calendar has no session that day")

    spec = _spec(space=ParameterSpace.of(_candidates()[:1]))
    ledger = _ledger()
    run_experiment(
        spec, RunnerRegistry().register("bands", refusing), "bands", ledger, SequentialExecutor()
    )
    held = ledger.find(spec.trials()[0].trial_id)

    assert held is not None and held.outcome is TrialOutcome.REFUSED
    assert held.diagnostics == (
        "AlphaLabValidationError: the venue's calendar has no session that day"
    )


def test_inputs_that_are_not_the_experiments_are_refused() -> None:
    spec = _spec(space=ParameterSpace.of(_candidates()[:1]))
    trial = spec.trials()[0]

    def other_costs(trial: TrialSpec) -> TrialInputs:
        inputs = build(trial)
        from alphalab.execution.commission import PerShareCommission
        from alphalab.execution.simulator import ExecutionSimulator

        priced = backtest_config(
            STRATEGY_ID,
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal(1))),
        )
        return replace(inputs, config=priced)

    ran = run_trial(spec, other_costs, trial)
    assert ran.record.outcome is TrialOutcome.REFUSED
    assert "execution assumptions" in ran.record.diagnostics
    assert isinstance(DATASET, MarketDataset)


def test_diagnostics_enter_no_identity_so_an_interpreters_wording_moves_nothing() -> None:
    spec = _spec(space=ParameterSpace.of([c for c in _candidates() if c["mode"] == "raise"][:1]))
    trial = spec.trials()[0]
    ledger = _ledger()
    first = ledger.keep(run_trial(spec, build, trial))

    def reworded(trial: TrialSpec) -> TrialInputs:
        raise ValueError("the same defect, as another interpreter words it")

    again = run_trial(spec, reworded, trial)
    assert again.record.record_id == first.record_id
    assert ledger.keep(again) == first, "the first record, and its diagnostics, are kept"
    assert ledger.find(trial.trial_id) == first
