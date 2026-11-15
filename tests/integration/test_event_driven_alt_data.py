"""S-13, M5's exit scenario: event-driven alternative data, validated over backtests in a ledger.

ADR-0073 section 10. A researcher's strategy trades surprises -- observations
delivered at the instant each was published -- for a window of instants after
each. Its eight event-window variants are validated walk-forward over
backtests in a ledger (``tests/integration/event_workload.py``):

* the ledger counts every event-window variant, in every fold, and the
  selections' test windows;
* each vintage reaches the strategy at the instant it was published and never
  before, and what was published before a run began is known and never traded;
* a late revision changes no earlier decision: the experiment is run twice,
  the surprise at 24's second vintage (published at 47.5) flipping its sign or
  confirming it, and every decision, every period's return and every fold's
  selection made before 47.5 is the same in both;
* the search is assessed -- PBO, SPA, the deflated Sharpe ratio -- and the
  selection is promoted only on selection-aware evidence: the same statistics
  without the family count are refused.
"""

from __future__ import annotations

import functools
import itertools

import pytest

from alphalab.lifecycle import (
    EXPERIMENT_FAMILY_METRIC,
    ExperimentAssessment,
    LifecycleState,
    LifecycleTransitionError,
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    assess_experiment,
    build_evidence,
    evidence_from_experiment,
    get_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_strategy,
    trial_matrix_of,
)
from alphalab.lifecycle.designs import REPORT
from alphalab.lifecycle.experiments import (
    ExperimentRecord,
    ExperimentSpec,
    SequentialExecutor,
    TrialLedger,
    TrialSpec,
    every_trial,
    run_experiment,
)
from alphalab.lifecycle.governance import LIFECYCLE_PERMISSIONS, Governance, StaticPermissions
from alphalab.model_registry import ModelStage
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.selection import SelectionMeasure, Trim
from alphalab.strategy import StrategyDefinition
from tests.integration.event_workload import (
    CANDIDATES,
    CONFIRMED,
    FLIPPED,
    REVISED_AT,
    STRATEGY_ID,
    EventWindow,
    registry,
    spec,
    surprises,
)

GOVERNANCE = Governance(StaticPermissions({"researcher": LIFECYCLE_PERMISSIONS}), "researcher")

Place = tuple[str, int, str]


@functools.cache
def _run(
    revision: str,
) -> tuple[ExperimentSpec, TrialLedger, ExperimentRecord, dict[Place, EventWindow]]:
    """The experiment with ``revision`` as the late vintage, and each trial's strategy."""

    experiment = spec(revision)
    built: dict[str, EventWindow] = {}
    ledger = TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))
    record = run_experiment(
        experiment, registry(revision, built), STRATEGY_ID, ledger, SequentialExecutor()
    )
    places = {
        (trial.label, trial.fold, trial.phase): built[trial.trial_id]
        for trial in every_trial(experiment)
        if trial.trial_id in built
    }
    return experiment, ledger, record, places


def _trials(experiment: ExperimentSpec, ledger: TrialLedger) -> dict[Place, TrialSpec]:
    """Every trial the ledger recorded, by its candidate, fold and phase."""

    return {
        (trial.label, trial.fold, trial.phase): trial
        for trial in every_trial(experiment)
        if ledger.find(trial.trial_id) is not None
    }


def _assessed(revision: str) -> ExperimentAssessment:
    experiment, ledger, record, _ = _run(revision)
    periods = trial_matrix_of(experiment, ledger, record, path=None).periods
    return assess_experiment(
        experiment,
        ledger,
        record,
        path=None,
        blocks=6,
        measure=SelectionMeasure.MEAN,
        trim=Trim.NONE,
        benchmark=[0.0] * periods,
        replications=200,
        mean_block_length=5.0,
        seed=7,
        effective_trials=None,
    )


def test_the_ledger_counts_every_event_window_variant() -> None:
    experiment, ledger, record, _ = _run(FLIPPED)

    variants = {trial.label for trial in experiment.trials()}
    assert len(variants) == len(CANDIDATES) == 8
    assert {(trial.label, trial.fold) for trial in experiment.trials()} == set(
        itertools.product(variants, range(3))
    ), "every variant is a trial in every fold"
    assert record.counts.recorded == 3 * 8 + 3, "and each fold's selection on its test window"
    assert len(_trials(experiment, ledger)) == record.counts.recorded
    assert _assessed(FLIPPED).family == record.counts.recorded


def test_each_vintage_reaches_the_strategy_when_it_was_published_and_never_before() -> None:
    experiment, ledger, _, places = _run(FLIPPED)
    published = {each.record_id: each.stamp.available_at for each in surprises(FLIPPED).records}
    trials = _trials(experiment, ledger)

    traded = 0
    for place, strategy in places.items():
        window = trials[place].window
        assert window is not None
        for decision in strategy.decisions:
            assert decision.at >= window.start, "nothing is traded before the run began"
            if decision.record_id:
                assert decision.at == published[decision.record_id]
                traded += 1
    assert traded > 0
    # The surprises published at 4.5, 9.5 and 14.5 reached the first fold's
    # trials at their start, at 17, and none was traded: they were published
    # before the run began.
    first = places[(experiment.trials()[0].label, 0, experiment.trials()[0].phase)]
    assert {4.0, 9.0, 14.0} <= set(first.known)
    assert min(decision.at for decision in first.decisions) == 19.5


def test_a_late_revision_changes_no_earlier_decision() -> None:
    flipped_spec, flipped_ledger, flipped, flipped_strategies = _run(FLIPPED)
    confirmed_spec, confirmed_ledger, confirmed, confirmed_strategies = _run(CONFIRMED)
    assert flipped_spec.experiment_id != confirmed_spec.experiment_id, (
        "the information set is part of the experiment"
    )
    flipped_trials = _trials(flipped_spec, flipped_ledger)
    confirmed_trials = _trials(confirmed_spec, confirmed_ledger)
    # Only fold 2's test-window trial differs: the selection made on a window
    # that holds 47.5.
    differ = flipped_trials.keys() ^ confirmed_trials.keys()
    assert {(fold, phase) for _, fold, phase in differ} == {(2, REPORT)}
    shared = flipped_trials.keys() & confirmed_trials.keys()

    def before(strategies: dict[Place, EventWindow], place: Place) -> list[object]:
        return [d for d in strategies[place].decisions if d.at < REVISED_AT]

    def returns_before(ledger: TrialLedger, trial: TrialSpec) -> list[float]:
        held = ledger.find(trial.trial_id)
        assert held is not None and held.series_id is not None
        instants, returns = ledger.series(held.series_id)
        return [r for t, r in zip(instants[1:], returns, strict=True) if t < REVISED_AT]

    changed_later = 0
    for place in shared:
        assert before(flipped_strategies, place) == before(confirmed_strategies, place), place
        assert returns_before(flipped_ledger, flipped_trials[place]) == returns_before(
            confirmed_ledger, confirmed_trials[place]
        ), place
        if flipped_strategies[place].decisions != confirmed_strategies[place].decisions:
            changed_later += 1
    assert changed_later > 0, "the flip was traded after it was published"
    # Folds 0 and 1 selected on windows that ended before 47.5.
    assert flipped.selections[:2] == confirmed.selections[:2]


def test_the_search_is_assessed_and_promoted_only_on_selection_aware_evidence() -> None:
    experiment, _, record, _ = _run(FLIPPED)
    assessment = _assessed(FLIPPED)

    never = ("threshold=0.5,window=1", "threshold=1.5,window=1")
    assert assessment.flat == assessment.constant == never, (
        "a one-instant window fills and unwinds at the same quote: it never holds a position"
    )
    assert assessment.superior is not None and assessment.deflated is not None
    assert assessment.deflated.independent_trials == 6
    # The deflated Sharpe ratio is read from the assessment: it is class 3, and
    # stays beside the evidence's identity (ADR-0058, ledger FUT-051).
    assert assessment.deflated.deflated_sharpe > 0.95

    state, ref = register_strategy(
        LifecycleState(),
        STRATEGY_ID,
        StrategyDefinition(
            strategy_id="event-window-selected",
            name="Event window",
            version="1",
            author="research",
            description="S-13's selection",
            parameters={"window": 5.0, "threshold": 0.5},
        ),
        5.0,
    )
    evidence = evidence_from_experiment(experiment, record, assessment, str(ref), 9.0)
    policy = ValidationPolicy(
        "selection-aware",
        (
            MetricThreshold("experiment.pbo", maximum=0.5),
            MetricThreshold("experiment.spa_p_consistent", maximum=0.05),
            MetricThreshold("experiment.reality_check_p", maximum=0.05),
        ),
        required_method=ValidationMethod.EXPERIMENT,
    )
    hidden = build_evidence(
        ValidationMethod.EXPERIMENT,
        str(ref),
        evidence.dataset_id,
        {k: v for k, v in evidence.metrics.items() if k != EXPERIMENT_FAMILY_METRIC},
        9.0,
        seed=evidence.seed,
        source_id=evidence.source_id,
    )
    state = record_evidence(record_evidence(state, evidence), hidden)

    with pytest.raises(LifecycleTransitionError, match="states no family count"):
        promote_strategy_version(
            state, GOVERNANCE, ref.name, ref.version, policy, hidden.evidence_id, 10.0
        )
    promoted = promote_strategy_version(
        state, GOVERNANCE, ref.name, ref.version, policy, evidence.evidence_id, 10.0
    )
    assert get_strategy_version(promoted.strategies, ref.name, ref.version).stage is (
        ModelStage.STAGING
    )
    assert evidence.metrics[EXPERIMENT_FAMILY_METRIC] == 27.0
