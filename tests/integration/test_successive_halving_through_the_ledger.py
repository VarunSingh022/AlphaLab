"""Successive halving through the ledger: every configuration a round evaluates counts (plan I.4).

The reference workload's scripted strategy (``experiment_workload``), its
trading window limited by a budget in bars. Six candidates, budgets of 20, 40
and 60 bars, half kept each round:

* **Every round is an experiment**, a child of the round before it: 6, 3 and 2
  trials, every one recorded in the ledger and counted -- 11 in all, over the 6
  configurations the search began with.
* **Each round keeps the best half** of the round before by its candidates'
  mean scores, ranked here independently, and the search selects the last
  round's best.
* **A resume runs nothing the ledger holds** and records the same search.
* **Candidates are ranked as cross-validation selects**: on a cross-validated
  experiment, the best of ``candidate_scores`` is the experiment's selection,
  and a candidate that failed in any fold has no score -- never the mean of
  the folds it completed, which would rank it on the folds that flattered it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from itertools import pairwise

from alphalab.backtesting.schedule import EMPTY_SCHEDULE
from alphalab.lifecycle import HalvingRecord, candidate_scores, run_successive_halving
from alphalab.lifecycle.designs import CrossValidated, PurgeSpec
from alphalab.lifecycle.experiments import (
    ExperimentSpec,
    LedgerCount,
    RunnerRegistry,
    SequentialExecutor,
    TrialInputs,
    TrialLedger,
    TrialSpec,
    run_experiment,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research import CVMethod, ParameterSpace, SuccessiveHalving
from alphalab.research.selection import SelectionMeasure
from tests.integration.experiment_workload import (
    ASSET_ID,
    CANDIDATES,
    DATASET,
    REGISTRY,
    STRATEGY_ID,
    _plan,
    build,
    spec,
)
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    running_strategy_state,
)

HALVING = SuccessiveHalving(budgets=(20, 40, 60), keep=0.5, parameter="budget")


@dataclass(frozen=True)
class Budgeted:
    """A candidate's window and size, and the bars it may trade in."""

    window: int
    size: int
    budget: int


CALLS: list[str] = []


def _build(trial: TrialSpec) -> TrialInputs:
    plan: Budgeted = trial.configuration
    CALLS.append(trial.label)
    steps = {
        at: size for at, size in _plan(plan.window, plan.size).items() if at < 2.0 + plan.budget
    }
    return TrialInputs(
        config=backtest_config(STRATEGY_ID),
        dataset=DATASET,
        strategy_state=running_strategy_state(
            STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, steps)
        ),
        context_factory=context_factory,
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


BUDGETED = RunnerRegistry().register("budgeted", _build)


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def _spec() -> ExperimentSpec:
    return replace(
        spec(),
        name="halving",
        hypothesis="a longer trading window ranks the windows and sizes",
        configuration_type=Budgeted,
        space=ParameterSpace.of([{"window": w, "size": s} for w in (2, 3, 5) for s in (1, 2)]),
    )


def _search(ledger: TrialLedger) -> HalvingRecord:
    return run_successive_halving(
        _spec(), HALVING, SelectionMeasure.MEAN, BUDGETED, "budgeted", ledger, SequentialExecutor()
    )


def _unbudgeted(label: str) -> str:
    """A rendered candidate without its budget: ``budget`` sorts first."""

    first, _, rest = label.partition(",")
    assert first.startswith("budget=")
    return rest


def test_every_round_is_a_child_experiment_and_every_trial_is_counted() -> None:
    record = _search(_ledger())
    assert [len(each.scores) for each in record.rounds] == [6, 3, 2]
    assert [each.budget for each in record.rounds] == [20, 40, 60]
    assert record.configurations == 6
    assert record.counts == LedgerCount(
        completed=11, insolvent=0, strategy_failed=0, refused=0, failed=0
    )
    assert sum(each.record.counts.recorded for each in record.rounds) == record.counts.recorded
    assert record.rounds[0].parent_id is None
    for earlier, later in pairwise(record.rounds):
        assert later.parent_id == earlier.experiment_id
    assert len({each.experiment_id for each in record.rounds}) == 3
    for each in record.rounds:
        assert all(label.startswith(f"budget={each.budget},") for label in each.scores)


def test_each_round_keeps_the_best_half_and_the_search_selects_the_last_rounds_best() -> None:
    record = _search(_ledger())
    for earlier, later in pairwise(record.rounds):
        labels = list(earlier.scores)
        scores = [earlier.scores[label] for label in labels]
        assert all(score is not None for score in scores)
        number = math.ceil(len(labels) / 2)
        ranked = sorted(range(len(labels)), key=lambda i: (-(scores[i] or 0.0), i))[:number]
        expected = [_unbudgeted(labels[i]) for i in sorted(ranked)]
        assert [_unbudgeted(label) for label in later.scores] == expected
    last = record.rounds[-1]
    best = max(last.scores, key=lambda label: last.scores[label] or -math.inf)
    assert record.selected is not None
    assert _unbudgeted(best) == f"size={record.selected['size']},window={record.selected['window']}"
    assert record.selected["budget"] == 60


def test_a_resume_runs_nothing_the_ledger_holds_and_records_the_same_search() -> None:
    ledger = _ledger()
    CALLS.clear()
    first = _search(ledger)
    assert len(CALLS) == 11
    CALLS.clear()
    again = _search(ledger)
    assert CALLS == [], "every trial was already in the ledger"
    assert again == first


def _cross_validated() -> ExperimentSpec:
    return replace(
        spec(),
        validation=CrossValidated(
            3, CVMethod.PURGED, 4, SelectionMeasure.MEAN, PurgeSpec(1, 0.0), None
        ),
    )


def _fails_one_fold(trial: TrialSpec) -> TrialInputs:
    """The reference runner, failing the first candidate in its second fold only."""

    if trial.fold == 1 and dict(trial.candidate) == CANDIDATES[0]:
        raise ValueError("the runner's defect, in one fold")
    return build(trial)


ONE_FOLD = RunnerRegistry().register("one fold", _fails_one_fold)


def test_candidates_are_ranked_as_cross_validation_selects() -> None:
    experiment = _cross_validated()
    ledger = _ledger()
    record = run_experiment(experiment, REGISTRY, "reference", ledger, SequentialExecutor())
    scores = candidate_scores(experiment, ledger, SelectionMeasure.MEAN)
    assert list(scores) == list(experiment.space.rendered)
    ranked = [label for label in scores if scores[label] is not None]
    best = max(ranked, key=lambda label: (scores[label], -ranked.index(label)))
    assert best == record.selected
    failed = {label for label in scores if "mode='normal'" not in label and "squeeze" not in label}
    assert failed and all(scores[label] is None for label in failed)


def test_a_candidate_that_failed_one_fold_has_no_score_and_moves_no_other() -> None:
    experiment = _cross_validated()
    reference, failing = _ledger(), _ledger()
    complete = run_experiment(experiment, REGISTRY, "reference", reference, SequentialExecutor())
    record = run_experiment(experiment, ONE_FOLD, "one fold", failing, SequentialExecutor())
    assert record.counts.recorded == complete.counts.recorded, "the failed trial is counted"
    assert record.counts.completed == complete.counts.completed - 1
    expected = candidate_scores(experiment, reference, SelectionMeasure.MEAN)
    scores = candidate_scores(experiment, failing, SelectionMeasure.MEAN)
    first = experiment.space.rendered[0]
    assert expected[first] is not None, "every fold of the reference scored it"
    assert scores[first] is None, "not the mean of the two folds it completed"
    del expected[first], scores[first]
    assert scores == expected
