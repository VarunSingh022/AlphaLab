"""Cross-validation and CPCV over backtests (v4.3, ADR-0073, WS-24, GAP-02).

The reference experiment, validated two more ways:

* **purged cross-validation**: three blocked folds, every candidate backtested
  on every fold's validation window, the first fold taking its warm-up from
  itself; the selection is the best mean fold score among the candidates that
  completed in every fold;
* **CPCV**: four groups, two tested by each of six splits, every candidate
  backtested on every test group of every split; each candidate's three paths
  join its groups' series, each a history of the whole sample, and the
  selection is the best mean path score.

Every trial is counted, and a candidate that did not complete everywhere is
never selected.
"""

from __future__ import annotations

import math
from dataclasses import replace

from alphalab.lifecycle.designs import (
    VALIDATE,
    CombinatorialPurged,
    CrossValidated,
    PurgeSpec,
    combinatorial,
)
from alphalab.lifecycle.experiments import (
    ExperimentSpec,
    LedgerCount,
    RunnerRegistry,
    SequentialExecutor,
    SpawnExecutor,
    TrialInputs,
    TrialLedger,
    TrialOutcome,
    TrialSpec,
    run_experiment,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.selection import SelectionMeasure
from alphalab.research.time_series_cv import CVMethod
from tests.integration.experiment_workload import CPCV_DEFINITION, REGISTRY, build, spec

BEST = "mode='normal',size=2,window=2"


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def _cv() -> ExperimentSpec:
    return replace(
        spec(),
        validation=CrossValidated(
            3, CVMethod.PURGED, 4, SelectionMeasure.MEAN, PurgeSpec(1, 0.0), None
        ),
    )


def _mean_score(ledger: TrialLedger, series_ids: list[str]) -> float:
    return math.fsum(math.fsum(ledger.series(each)[1]) for each in series_ids) / len(series_ids)


def test_cross_validation_scores_every_candidate_on_every_fold_and_selects_by_the_mean() -> None:
    experiment = _cv()
    ledger = _ledger()
    record = run_experiment(experiment, REGISTRY, "reference", ledger, SequentialExecutor())

    assert record.counts == LedgerCount(
        completed=21, insolvent=0, strategy_failed=3, refused=3, failed=3
    )
    assert {trial.phase for trial in experiment.trials()} == {VALIDATE}
    assert record.matrix_id is None and record.selections == ()
    means: dict[str, float] = {}
    for candidate_trials in (
        [t for t in experiment.trials() if t.label == label]
        for label in dict.fromkeys(t.label for t in experiment.trials())
    ):
        held = [ledger.find(t.trial_id) for t in candidate_trials]
        if all(h is not None and h.outcome is TrialOutcome.COMPLETED for h in held):
            ids = [h.series_id for h in held if h is not None and h.series_id is not None]
            means[candidate_trials[0].label] = _mean_score(ledger, ids)
    best = max(means.values())
    assert record.selected == next(label for label, mean in means.items() if mean == best)
    assert record.selected == BEST


def test_a_candidate_that_did_not_complete_in_every_fold_is_never_selected() -> None:
    def failing_once(trial: TrialSpec) -> TrialInputs:
        if trial.label == BEST and trial.fold == 1:
            raise ValueError("this candidate fails on the second fold")
        return build(trial)

    record = run_experiment(
        _cv(),
        RunnerRegistry().register("reference", failing_once),
        "reference",
        _ledger(),
        SequentialExecutor(),
    )

    assert record.counts.failed == 3 + 1
    assert record.selected is not None and record.selected != BEST


def test_cpcv_joins_each_candidates_groups_into_paths_over_the_whole_sample() -> None:
    experiment = CPCV_DEFINITION.spec
    design = experiment.validation
    assert isinstance(design, CombinatorialPurged)
    ledger = _ledger()
    record = run_experiment(experiment, REGISTRY, "reference", ledger, SequentialExecutor())

    assert len(experiment.trials()) == 6 * 2 * 10
    assert record.counts.recorded == 120
    report = combinatorial(design, experiment.dataset.instants)
    paths = dict(record.paths)
    for label, ids in record.paths:
        assert len(ids) == len(report.paths) == 3
        if label.startswith("mode='normal'"):
            assert all(each is not None for each in ids)
    for p, path_id in enumerate(paths[BEST]):
        assert path_id is not None
        instants, returns = ledger.series(path_id)
        # Group 0 takes its three-instant warm-up from itself: the paths start at 4.0.
        assert instants[0] == 4.0 and instants[-1] == 61.0 and len(returns) == 57
        joined: list[float] = []
        for group, split in enumerate(report.paths[p]):
            trial = next(
                t
                for t in experiment.trials()
                if t.label == BEST and t.fold == split and t.phase == f"test:{group}"
            )
            held = ledger.find(trial.trial_id)
            assert held is not None and held.series_id is not None
            joined.extend(ledger.series(held.series_id)[1])
        assert list(returns) == joined
    failed = paths["mode='raise',size=1,window=7"]
    assert all(each is None for each in failed), "a path a trial left incomplete is None"
    means = {
        label: _mean_score(ledger, [each for each in ids if each is not None])
        for label, ids in record.paths
        if all(each is not None for each in ids)
    }
    best = max(means.values())
    assert record.selected == next(label for label, mean in means.items() if mean == best)


def test_a_spawned_cpcv_experiment_is_the_sequential_one() -> None:
    """A worker finds a split's group trial, ``test:g``, by its identity alone."""

    experiment = CPCV_DEFINITION.spec
    sequential = run_experiment(experiment, REGISTRY, "reference", _ledger(), SequentialExecutor())
    spawned = run_experiment(
        experiment,
        REGISTRY,
        "reference",
        _ledger(),
        SpawnExecutor(
            "tests.integration.experiment_workload:CPCV_DEFINITION",
            workers=4,
            trials_per_worker=None,
        ),
    )

    assert spawned == sequential
