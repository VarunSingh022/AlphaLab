"""A recorded trial is reproduced from its record, and an experiment draws no identifier.

The plan's acceptance for the ledger (GAP-01; WS-05 item 14): ``rerun_trial``
reproduces a record in a fresh process. ADR-0054's review added two checks
(master plan E.12) that hold here too:

* **zero identifier draws** (ADR-0029 D6): an experiment draws nothing from the
  identifier stream around it, whichever executor runs it -- the cluster's own
  bookkeeping included;
* **typed-parameter identity** (R54-3): a candidate of another type than the
  configuration declares never becomes a trial.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from alphalab.cloud_research import initialize_cluster
from alphalab.common.ids import current_id_position, id_scope
from alphalab.common.registry import RunnerRegistry
from alphalab.lifecycle.experiments import (
    ClusterExecutor,
    ExperimentError,
    SequentialExecutor,
    SpawnExecutor,
    TrialExecutor,
    TrialInputs,
    TrialLedger,
    TrialOutcome,
    TrialSpec,
    rerun_trial,
    run_experiment,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.walk_forward_optimization import ParameterSpace
from tests.integration.experiment_workload import (
    DEFINITION,
    REGISTRY,
    SLOW_DEFINITION,
    build,
)

WHERE = "tests.integration.experiment_workload:DEFINITION"


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def _recorded() -> TrialLedger:
    ledger = _ledger()
    run_experiment(DEFINITION.spec, REGISTRY, "reference", ledger, SequentialExecutor())
    return ledger


def test_a_record_of_every_outcome_is_reproduced_in_a_fresh_process() -> None:
    spec = DEFINITION.spec
    ledger = _recorded()
    first_of_each: dict[TrialOutcome, TrialSpec] = {}
    for trial in spec.trials():
        held = ledger.find(trial.trial_id)
        assert held is not None
        first_of_each.setdefault(held.outcome, trial)

    assert set(first_of_each) == {
        TrialOutcome.COMPLETED,
        TrialOutcome.INSOLVENT,
        TrialOutcome.STRATEGY_FAILED,
        TrialOutcome.REFUSED,
        TrialOutcome.FAILED,
    }
    for outcome, trial in first_of_each.items():
        rerun = rerun_trial(
            spec, REGISTRY, "reference", ledger, trial.trial_id, SpawnExecutor(WHERE, workers=1)
        )
        assert rerun.reproduced, outcome
        assert rerun.rerun == rerun.held == ledger.find(trial.trial_id).record_id  # type: ignore[union-attr]


def drifted(trial: TrialSpec) -> TrialInputs:
    """The reference runner with one more unit traded: the code a record was not made by."""

    return build(replace(trial, configuration=replace(trial.configuration, size=2)))


def test_a_rerun_that_leaves_another_record_says_so_and_files_nothing() -> None:
    spec = DEFINITION.spec
    ledger = _recorded()
    trial = spec.trials()[0]
    held = ledger.find(trial.trial_id)

    rerun = rerun_trial(
        spec,
        RunnerRegistry().register("reference", drifted),
        "reference",
        ledger,
        trial.trial_id,
        SequentialExecutor(),
    )

    assert held is not None and rerun.held == held.record_id
    assert rerun.rerun is not None and not rerun.reproduced
    assert ledger.find(trial.trial_id) == held, "a rerun is a check: the ledger is unchanged"


def test_an_interrupted_rerun_left_no_record() -> None:
    spec = SLOW_DEFINITION.spec
    ledger = _ledger()
    run_experiment(spec, REGISTRY, "reference", ledger, SequentialExecutor())
    late = spec.trials()[2]

    rerun = rerun_trial(
        spec,
        REGISTRY,
        "sleepy",
        ledger,
        late.trial_id,
        SpawnExecutor("tests.integration.experiment_workload:SLOW_DEFINITION", workers=1),
    )

    assert rerun.rerun is None and not rerun.reproduced


def test_a_rerun_names_a_recorded_trial_of_the_experiment() -> None:
    spec = DEFINITION.spec

    with pytest.raises(ExperimentError, match="is not a trial of experiment reference"):
        rerun_trial(spec, REGISTRY, "reference", _recorded(), "ab" * 32, SequentialExecutor())
    with pytest.raises(ExperimentError, match="holds no record"):
        rerun_trial(
            spec, REGISTRY, "reference", _ledger(), spec.trials()[0].trial_id, SequentialExecutor()
        )


@pytest.mark.parametrize(
    "executor",
    [
        SequentialExecutor(),
        SpawnExecutor(WHERE, workers=4),
        ClusterExecutor(
            WHERE, initialize_cluster("lab", num_workers=2, capacity_per_worker=2, timestamp=0.0)
        ),
    ],
    ids=["sequential", "spawned", "cluster"],
)
def test_an_experiment_draws_no_identifier_from_the_scope_around_it(
    executor: TrialExecutor,
) -> None:
    with id_scope(11):
        before = current_id_position()
        record = run_experiment(DEFINITION.spec, REGISTRY, "reference", _ledger(), executor)
        after = current_id_position()

    assert after == before
    assert record.counts.recorded == len(DEFINITION.spec.trials())


def test_a_candidate_not_of_the_configuration_type_is_refused_before_any_trial_runs() -> None:
    """R54-3: ``window`` is an int, so ``2.0`` is not a candidate of it, and nothing runs."""

    called: list[str] = []

    def counting(trial: TrialSpec) -> TrialInputs:
        called.append(trial.label)
        return build(trial)

    mistyped = replace(
        DEFINITION.spec,
        space=ParameterSpace.of(
            [
                {"window": 2, "size": 1, "mode": "normal"},
                {"window": 2.0, "size": 1, "mode": "normal"},
            ]
        ),
    )

    with pytest.raises(ExperimentError, match=r"expected int, found float 2\.0"):
        run_experiment(
            mistyped,
            RunnerRegistry().register("reference", counting),
            "reference",
            _ledger(),
            SequentialExecutor(),
        )
    assert called == []
