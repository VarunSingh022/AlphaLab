"""Walk-forward validation over backtests (v4.3, ADR-0073, WS-24, GAP-02).

The reference experiment, validated walk-forward: three folds, each candidate
backtested on the fold's validation window after a five-instant warm-up, the
best selected by the ledger, and the selection alone backtested on the fold's
test window. These tests hold:

* every trial is counted, both stages, failures included, and a candidate
  whose trial did not complete is never selected;
* the selection is the best by the design's measure over the ledger's own
  series, ties to the first;
* a trial's run covers only its warm-up and its window, and the warm-up is
  never scored;
* the selections' test-window series join into the out-of-sample series;
* a resume runs nothing, and a spawned run is the sequential one.
"""

from __future__ import annotations

import math

from alphalab.lifecycle.designs import REPORT, SELECT
from alphalab.lifecycle.experiments import (
    ExperimentRecord,
    LedgerCount,
    RunnerRegistry,
    SequentialExecutor,
    SpawnExecutor,
    TrialInputs,
    TrialLedger,
    TrialOutcome,
    TrialSpec,
    report_trial,
    run_experiment,
    run_trial,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.walk_forward_optimization import Refit, _render
from tests.integration.experiment_workload import REGISTRY, build, walk_forward_spec

WHERE = "tests.integration.experiment_workload:WALK_FORWARD_DEFINITION"


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def _run() -> tuple[TrialLedger, ExperimentRecord]:
    ledger = _ledger()
    record = run_experiment(
        walk_forward_spec(), REGISTRY, "reference", ledger, SequentialExecutor()
    )
    return ledger, record


def test_every_trial_of_both_stages_is_counted_and_each_fold_selects_its_best() -> None:
    spec = walk_forward_spec()
    ledger, record = _run()

    # 3 folds x 10 candidates selected on, and one test-window trial a fold.
    assert record.counts == LedgerCount(
        completed=24, insolvent=0, strategy_failed=3, refused=3, failed=3
    )
    assert record.counts.recorded == 33
    assert record.matrix_id is None, "folds share no one set of periods"
    assert [fold for fold, _ in record.selections] == [0, 1, 2]
    for fold, selected in record.selections:
        scores = {}
        for trial in spec.trials():
            held = ledger.find(trial.trial_id)
            if trial.fold != fold or held is None or held.outcome is not TrialOutcome.COMPLETED:
                continue
            assert held.series_id is not None
            scores[trial.label] = math.fsum(ledger.series(held.series_id)[1])
        best = max(scores.values())
        assert selected == next(label for label, score in scores.items() if score == best)
    failed = {"mode='raise',size=1,window=7", "mode='crash',size=1,window=7"}
    assert not failed & {selected for _, selected in record.selections}


def test_a_trial_runs_only_its_warm_up_and_window_and_its_warm_up_is_never_scored() -> None:
    spec = walk_forward_spec()
    first = spec.trials()[0]
    assert first.window is not None and first.phase == SELECT

    ran = run_trial(spec, build, first)

    assert first.window.start == 17.0 and first.window.evaluate == tuple(
        float(t) for t in range(22, 32)
    )
    assert ran.capture is not None
    # Five warm-up records, ten scored, and the run's start and end.
    assert ran.capture.steps == 5 + 10 + 2
    # The series starts at the instant before the window: ten returns, the
    # periods ending at each of the window's instants, none of the warm-up's.
    assert ran.capture.instants[0] == 21.0
    assert len(ran.capture.returns) == 10


def test_the_selections_test_windows_join_into_the_out_of_sample_series() -> None:
    spec = walk_forward_spec()
    ledger, record = _run()
    assert record.out_of_sample is not None

    instants, returns = ledger.series(record.out_of_sample)
    assert instants[0] == 31.0 and instants[-1] == 61.0 and len(returns) == 30
    joined: list[float] = []
    for fold, selected in record.selections:
        assert selected is not None
        candidate = next(c for c in spec.space.candidates if _render(c) == selected)
        held = ledger.find(report_trial(spec, fold, candidate).trial_id)
        assert held is not None and held.phase == REPORT and held.series_id is not None
        joined.extend(ledger.series(held.series_id)[1])
    assert list(returns) == joined


def test_refitting_on_train_and_validation_fits_the_selection_on_both() -> None:
    spec = walk_forward_spec(Refit.TRAIN_AND_VALIDATION)
    candidate = spec.space.candidates[0]
    window = report_trial(spec, 0, candidate).window

    assert window is not None
    assert window.fit == tuple(float(t) for t in range(2, 32))
    assert window.evaluate == tuple(float(t) for t in range(32, 42))


def test_a_resume_runs_nothing_and_leaves_the_same_record() -> None:
    ledger, record = _run()
    called: list[str] = []

    def counting(trial: TrialSpec) -> TrialInputs:
        called.append(trial.label)
        return build(trial)

    again = run_experiment(
        walk_forward_spec(),
        RunnerRegistry().register("reference", counting),
        "reference",
        ledger,
        SequentialExecutor(),
    )

    assert again == record
    assert called == []


def test_a_fold_with_no_completed_candidate_selects_nothing() -> None:
    def failing(trial: TrialSpec) -> TrialInputs:
        raise ValueError("every candidate fails")

    record = run_experiment(
        walk_forward_spec(),
        RunnerRegistry().register("reference", failing),
        "reference",
        _ledger(),
        SequentialExecutor(),
    )

    assert record.selections == ((0, None), (1, None), (2, None))
    assert record.counts.failed == record.counts.recorded == 30
    assert record.out_of_sample is None


def test_a_spawned_walk_forward_experiment_is_the_sequential_one() -> None:
    """A worker finds a fold's test-window trial by its identity alone."""

    _, sequential = _run()
    spawned = run_experiment(
        walk_forward_spec(),
        REGISTRY,
        "reference",
        _ledger(),
        SpawnExecutor(WHERE, workers=4, trials_per_worker=None),
    )

    assert spawned == sequential
