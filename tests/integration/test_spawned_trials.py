"""A spawned trial is the sequential trial, and cannot reach the network (ADR-0072, M4's exit gate).

:class:`~alphalab.lifecycle.experiments.SpawnExecutor` runs each trial in a
fresh interpreter that refuses the network before it imports anything of the
researcher's. These tests hold it to ADR-0072 section 6:

* the reference experiment spawned is the reference experiment run in this
  process, record for record and by ``experiment_record_id``;
* records are kept in submission order, though later trials finish first;
* a definition that reaches for the network as it is imported is refused
  before it reaches it, and every trial is recorded as failing, saying why;
* a runner that reaches for the network while it runs is refused, and fails;
* a trial past its time limit, and a worker that dies, are interrupted: they
  record nothing, and a resume runs them;
* a limit times the trial, never the worker's start;
* a worker that loads another experiment is refused.
"""

from __future__ import annotations

import pytest

from alphalab.lifecycle.experiments import (
    ExperimentError,
    SequentialExecutor,
    SpawnExecutor,
    TrialLedger,
    TrialOutcome,
    run_experiment,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from tests.integration.experiment_workload import DEFINITION, REGISTRY, SLOW_DEFINITION, run

WHERE = "tests.integration.experiment_workload:DEFINITION"


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


def test_a_spawned_experiment_is_the_sequential_one_record_for_record() -> None:
    """Eight workers, as the plan's executor-equality test has it (WS-05 item 13)."""

    spec, sequential = run()
    ledger = _ledger()
    spawned = run_experiment(spec, REGISTRY, "reference", ledger, SpawnExecutor(WHERE, workers=8))

    assert spawned == sequential
    assert spawned.experiment_record_id == sequential.experiment_record_id
    assert spawned.counts.recorded == len(spec.trials())


def test_records_keep_submission_order_whatever_order_trials_finish_in() -> None:
    spec, sequential = run()
    staggered = run_experiment(
        spec, REGISTRY, "stagger", _ledger(), SpawnExecutor(WHERE, workers=len(spec.trials()))
    )

    assert staggered.trial_record_ids == sequential.trial_record_ids


def test_a_definition_that_reaches_for_the_network_on_import_is_refused() -> None:
    spec = DEFINITION.spec
    ledger = _ledger()
    record = run_experiment(
        spec,
        REGISTRY,
        "reference",
        ledger,
        SpawnExecutor("tests.integration.spy_definition:DEFINITION", workers=4),
    )

    assert record.counts.failed == record.counts.recorded == len(spec.trials())
    held = ledger.find(spec.trials()[0].trial_id)
    assert held is not None and held.outcome is TrialOutcome.FAILED
    assert "did not load" in held.diagnostics
    assert "the network is refused to a trial: socket" in held.diagnostics


def test_a_runner_that_reaches_for_the_network_while_it_runs_fails() -> None:
    spec = DEFINITION.spec
    ledger = _ledger()
    record = run_experiment(spec, REGISTRY, "phone", ledger, SpawnExecutor(WHERE, workers=4))

    assert record.counts.failed == len(spec.trials())
    held = ledger.find(spec.trials()[0].trial_id)
    assert held is not None
    assert held.diagnostics == (
        "PermissionError: the network is refused to a trial: socket.getaddrinfo"
    )


def test_a_trial_past_its_limit_is_interrupted_and_a_resume_runs_it() -> None:
    spec = SLOW_DEFINITION.spec
    ledger = _ledger()
    where = "tests.integration.experiment_workload:SLOW_DEFINITION"
    partial = run_experiment(spec, REGISTRY, "sleepy", ledger, SpawnExecutor(where, workers=4))
    late = spec.trials()[2]

    assert partial.counts.recorded == len(spec.trials()) - 1
    assert ledger.find(late.trial_id) is None, "an interruption records nothing"
    finished = run_experiment(spec, REGISTRY, "reference", ledger, SpawnExecutor(where, workers=2))
    assert finished.counts.recorded == len(spec.trials())
    whole = run_experiment(spec, REGISTRY, "reference", _ledger(), SequentialExecutor())
    assert finished == whole


def test_a_limit_times_the_trial_never_the_workers_start() -> None:
    """A definition whose import outlasts the limit: every trial still completes.

    The limit is timed from the moment the worker has loaded the experiment, so
    an interpreter's start and its imports -- which a busy host slows -- are
    never counted against a trial (ADR-0072, settled at implementation 5).
    """

    spec = SLOW_DEFINITION.spec
    record = run_experiment(
        spec,
        REGISTRY,
        "reference",
        _ledger(),
        SpawnExecutor("tests.integration.slow_import_definition:DEFINITION", workers=4),
    )

    assert record.counts.recorded == len(spec.trials())
    assert record == run_experiment(spec, REGISTRY, "reference", _ledger(), SequentialExecutor())


def test_a_worker_that_dies_records_nothing_for_its_trial() -> None:
    spec = DEFINITION.spec
    ledger = _ledger()
    record = run_experiment(spec, REGISTRY, "dies", ledger, SpawnExecutor(WHERE, workers=4))

    assert record.counts.recorded == len(spec.trials()) - 1
    assert ledger.find(spec.trials()[1].trial_id) is None


def test_a_worker_that_loads_another_experiment_is_refused() -> None:
    other = SLOW_DEFINITION.spec

    with pytest.raises(ExperimentError, match="loaded another experiment"):
        run_experiment(other, REGISTRY, "reference", _ledger(), SpawnExecutor(WHERE, workers=1))


@pytest.mark.parametrize(
    ("definition", "workers"),
    [("no.colon", 1), ("module:", 1), (":NAME", 1), ("module:NAME", 0), ("module:NAME", True)],
)
def test_a_spawn_executor_names_its_definition_and_its_workers(
    definition: str, workers: int
) -> None:
    with pytest.raises(ExperimentError, match="SpawnExecutor"):
        SpawnExecutor(definition, workers)
