"""The research assistant and the experiment tracker consume the ledger (ADR-0072 section 10).

* ``research_assistant``: ``generate_candidates`` feeds an experiment's trials.
  :func:`~alphalab.research_assistant.candidate_space` is the space of exactly
  the candidates generated, so an experiment over it runs one trial per
  candidate, in the assistant's order -- a limited enumeration counting only
  what it generated.
* ``experiment_tracking`` (D-54-3): the governance registry is linked to the
  ledger by ``experiment_id`` in a run's ``tags``, until a typed field at 5.0.
  :func:`~alphalab.lifecycle.experiments.experiment_tags` is the citation and
  :func:`~alphalab.lifecycle.experiments.tracked_runs` reads it back.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from alphalab.common.types import ParamValue
from alphalab.experiment_tracking import ExperimentTracker, start_run
from alphalab.lifecycle.experiments import (
    EXPERIMENT_TAG,
    ExperimentError,
    SequentialExecutor,
    TrialLedger,
    experiment_tags,
    run_experiment,
    tracked_runs,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research_assistant import candidate_space, generate_candidates
from tests.integration.experiment_workload import REGISTRY, spec


def test_the_assistants_candidates_are_the_experiments_trials_in_its_order() -> None:
    """A limited enumeration of a six-point grid: four candidates, four trials, four counted."""

    axes: dict[str, list[ParamValue]] = {"window": [2, 3, 5], "size": [1, 2], "mode": ["normal"]}
    candidates = generate_candidates("reference", axes, limit=4)
    experiment = replace(spec(), space=candidate_space(candidates))

    trials = experiment.trials()
    assert [dict(trial.candidate) for trial in trials] == [c.parameters for c in candidates]
    assert [(t.configuration.window, t.configuration.size) for t in trials] == [
        (2, 1),
        (3, 1),
        (5, 1),
        (2, 2),
    ]

    ledger = TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))
    record = run_experiment(experiment, REGISTRY, "reference", ledger, SequentialExecutor())
    assert record.counts.completed == record.counts.recorded == 4


def test_a_tracker_run_cites_its_experiment_by_id_and_is_found_by_it() -> None:
    experiment = spec()
    tags = experiment_tags(experiment)

    tracker, cited = start_run(
        ExperimentTracker(),
        experiment.name,
        {"seed": experiment.seed},
        1.0,
        tags={**tags, "desk": "rates"},
    )
    tracker, _ = start_run(tracker, "elsewhere", {}, 2.0)
    tracker, again = start_run(tracker, experiment.name, {"seed": experiment.seed}, 3.0, tags=tags)

    assert tags == {EXPERIMENT_TAG: experiment.experiment_id}
    assert [run.run_id for run in tracked_runs(tracker, experiment.experiment_id)] == [cited, again]
    assert tracked_runs(tracker, "0" * 64) == ()
    with pytest.raises(ExperimentError, match="SHA-256"):
        tracked_runs(tracker, experiment.name)
