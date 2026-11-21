"""
AlphaLab Examples
=================

Example 80 : A Ledgered Sweep

Difficulty : Intermediate

Estimated Time : 15 minutes

Topics
------

• Stating an experiment: its hypothesis, candidates, data and everything else a
  trial turns on
• Running every candidate as a trial in spawned workers that refuse the network
• Every trial counted, the failed ones included
• Resuming a sweep, and reproducing one record in a fresh interpreter
• Citing the experiment from the tracker
• Reading the held-back lockbox once, and only once

What this shows
---------------

The trial ledger (ADR-0072) end to end. The experiment is ``_ledgered_sweep``'s:
the research assistant's nine candidates for a moving-average crossover, over
300 synthetic daily closes, the last fifty held back as a lockbox. It lives in a
module of its own because a spawned worker imports an experiment's definition
by its module's name.

* ``run_experiment`` runs each candidate as a trial and records its outcome.
  Two candidates are not crossovers at all -- the fast window is not shorter
  than the slow -- so the strategy refuses them, and those trials are recorded
  ``FAILED``: a search is counted with its failures.
* Running it again runs nothing: every trial has its record. The sequential
  executor and the spawned one leave the same records.
* ``rerun_trial`` runs one recorded trial again in a fresh interpreter and holds
  it to its record.
* A tracker run cites the experiment by its identity, in its tags.
* The best candidate in sample is read over the lockbox once. A second read is
  refused: every look at held-back data is counted.

Nothing here says whether a crossover makes money: the prices are a formula.

Run

    python examples/80_a_ledgered_sweep.py
"""

from __future__ import annotations

import math

from _ledgered_sweep import LOCKBOX_START, REGISTRY, SPEC

from alphalab.experiment_tracking import ExperimentTracker, start_run
from alphalab.lifecycle.experiments import (
    ExperimentError,
    SequentialExecutor,
    SpawnExecutor,
    TrialLedger,
    TrialOutcome,
    TrialSpec,
    experiment_tags,
    read_lockbox,
    rerun_trial,
    run_experiment,
    tracked_runs,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex

WHERE = "_ledgered_sweep:DEFINITION"


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def growth(ledger: TrialLedger, trial: TrialSpec) -> float:
    """What the trial's captured returns compound to, in sample."""

    record = ledger.find(trial.trial_id)
    assert record is not None and record.series_id is not None
    _, returns = ledger.series(record.series_id)
    return math.prod(1.0 + value for value in returns) - 1.0


def main() -> None:
    print("Example 80: a ledgered sweep\n")
    trials = SPEC.trials()
    print(f"experiment {SPEC.name}: {len(trials)} trials, experiment_id {SPEC.experiment_id[:16]}…")

    # 1. Every candidate a trial, in two spawned workers that refuse the network.
    ledger = _ledger()
    spawned = SpawnExecutor(WHERE, workers=2, trials_per_worker=None)
    record = run_experiment(SPEC, REGISTRY, "ma-cross", ledger, spawned)
    counts = record.counts
    print(f"recorded {counts.recorded}: {counts.completed} completed, {counts.failed} failed")
    for trial in trials:
        held = ledger.find(trial.trial_id)
        assert held is not None
        if held.outcome is TrialOutcome.FAILED:
            print(f"  {trial.label}: FAILED, {held.diagnostics}")

    # 2. A resume runs nothing; the sequential executor leaves the same records.
    resumed = run_experiment(SPEC, REGISTRY, "ma-cross", ledger, SequentialExecutor())
    sequential = run_experiment(SPEC, REGISTRY, "ma-cross", _ledger(), SequentialExecutor())
    assert resumed == record == sequential
    print(f"resumed and rerun sequentially: the same record, {record.experiment_record_id[:16]}…")

    # 3. One record reproduced in a fresh interpreter.
    outcomes = {trial.trial_id: ledger.find(trial.trial_id) for trial in trials}
    completed = [
        trial
        for trial in trials
        if (held := outcomes[trial.trial_id]) is not None and held.outcome is TrialOutcome.COMPLETED
    ]
    best = max(completed, key=lambda trial: growth(ledger, trial))
    fresh = SpawnExecutor(WHERE, workers=1, trials_per_worker=1)
    rerun = rerun_trial(SPEC, REGISTRY, "ma-cross", ledger, best.trial_id, fresh)
    assert rerun.reproduced
    print(f"best in sample: {best.label}, {growth(ledger, best):+.2%}")
    print("  its record reproduced in a fresh interpreter")

    # 4. The tracker cites the experiment.
    tracker, run_id = start_run(
        ExperimentTracker(), SPEC.name, {"trials": counts.recorded}, 0.0, tags=experiment_tags(SPEC)
    )
    assert [run.run_id for run in tracked_runs(tracker, SPEC.experiment_id)] == [run_id]
    print(f"tracker run {run_id[:8]}… cites the experiment")

    # 5. The lockbox, once.
    whole = read_lockbox(SPEC, REGISTRY, "ma-cross", ledger, best)
    assert whole.capture is not None
    instants, returns = whole.capture.instants, whole.capture.returns
    # A record at the lockbox's start is held back, so the return ending there is too.
    ends = zip(instants[1:], returns, strict=True)
    held_out = [value for end, value in ends if end >= LOCKBOX_START]
    out = math.prod(1.0 + value for value in held_out) - 1.0
    print(
        f"the lockbox read once: {best.label}, {out:+.2%} over the {len(held_out)} held-back days"
    )
    try:
        read_lockbox(SPEC, REGISTRY, "ma-cross", ledger, best)
    except ExperimentError as refused:
        print(f"a second read is refused: {str(refused)[:60]}…")
    else:
        raise AssertionError("a second read of the lockbox was allowed")


if __name__ == "__main__":
    main()
