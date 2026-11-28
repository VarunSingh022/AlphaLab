"""A spawned trial is the sequential trial, and cannot reach the network (ADR-0072, M4's exit gate).

:class:`~alphalab.lifecycle.experiments.SpawnExecutor` runs each trial in a
fresh interpreter that refuses the network before it imports anything of the
researcher's. These tests hold it to ADR-0072 section 6:

* the reference experiment spawned is the reference experiment run in this
  process, record for record and by ``experiment_record_id``;
* records are kept in submission order, though later trials finish first,
  and an executor yields them in that order;
* a definition that reaches for the network as it is imported is refused
  before it reaches it, and every trial is recorded as failing, saying why;
* a runner that reaches for the network while it runs is refused, and fails;
* a trial past its time limit, and a worker that dies, are interrupted: they
  record nothing, and a resume runs them;
* a limit times the trial, never the worker's start;
* what a trial writes, by Python or not, never reaches the worker's answers;
* a worker loads the experiment once, and runs as many trials as it is given:
  one for a fresh interpreter per trial, or all of them;
* a worker that loads another experiment is refused.
"""

from __future__ import annotations

from pathlib import Path

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

#: Both worker lifetimes: a fresh interpreter per trial, and one per worker.
LIFETIMES = pytest.mark.parametrize("per_worker", [1, None], ids=["fresh", "reused"])


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


@LIFETIMES
def test_a_spawned_experiment_is_the_sequential_one_record_for_record(
    per_worker: int | None,
) -> None:
    """Eight workers, as the plan's executor-equality test has it (WS-05 item 13)."""

    spec, sequential = run()
    ledger = _ledger()
    spawned = run_experiment(
        spec,
        REGISTRY,
        "reference",
        ledger,
        SpawnExecutor(WHERE, workers=8, trials_per_worker=per_worker),
    )

    assert spawned == sequential
    assert spawned.experiment_record_id == sequential.experiment_record_id
    assert spawned.counts.recorded == len(spec.trials())


@LIFETIMES
def test_records_keep_submission_order_whatever_order_trials_finish_in(
    per_worker: int | None,
) -> None:
    spec, sequential = run()
    staggered = run_experiment(
        spec,
        REGISTRY,
        "stagger",
        _ledger(),
        SpawnExecutor(WHERE, workers=len(spec.trials()), trials_per_worker=per_worker),
    )

    assert staggered.trial_record_ids == sequential.trial_record_ids


@LIFETIMES
def test_an_executor_yields_in_submission_order_whatever_order_trials_finish_in(
    per_worker: int | None,
) -> None:
    """The protocol itself: a worker each, the first trial finishing last by 2.25 s.

    ``run_experiment`` reads its records back in submission order, so its
    record could not show the order an executor yields in; this reads the
    executor directly.
    """

    spec = DEFINITION.spec
    trials = spec.trials()
    executor = SpawnExecutor(WHERE, workers=len(trials), trials_per_worker=per_worker)

    yielded = [
        ran.record.trial_id for ran in executor.execute(spec, REGISTRY, "slow_first", trials)
    ]

    assert yielded == [trial.trial_id for trial in trials]


@LIFETIMES
def test_a_definition_that_reaches_for_the_network_on_import_is_refused(
    per_worker: int | None,
) -> None:
    spec = DEFINITION.spec
    ledger = _ledger()
    record = run_experiment(
        spec,
        REGISTRY,
        "reference",
        ledger,
        SpawnExecutor(
            "tests.integration.spy_definition:DEFINITION", workers=4, trials_per_worker=per_worker
        ),
    )

    assert record.counts.failed == record.counts.recorded == len(spec.trials())
    held = ledger.find(spec.trials()[0].trial_id)
    assert held is not None and held.outcome is TrialOutcome.FAILED
    assert "did not load" in held.diagnostics
    assert "the network is refused to a trial: socket" in held.diagnostics


@LIFETIMES
def test_a_runner_that_reaches_for_the_network_while_it_runs_fails(per_worker: int | None) -> None:
    spec = DEFINITION.spec
    ledger = _ledger()
    record = run_experiment(
        spec,
        REGISTRY,
        "phone",
        ledger,
        SpawnExecutor(WHERE, workers=4, trials_per_worker=per_worker),
    )

    assert record.counts.failed == len(spec.trials())
    held = ledger.find(spec.trials()[0].trial_id)
    assert held is not None
    assert held.diagnostics == (
        "PermissionError: the network is refused to a trial: socket.getaddrinfo"
    )


@LIFETIMES
def test_a_trial_past_its_limit_is_interrupted_and_a_resume_runs_it(per_worker: int | None) -> None:
    spec = SLOW_DEFINITION.spec
    ledger = _ledger()
    where = "tests.integration.experiment_workload:SLOW_DEFINITION"
    partial = run_experiment(
        spec,
        REGISTRY,
        "sleepy",
        ledger,
        SpawnExecutor(where, workers=4, trials_per_worker=per_worker),
    )
    late = spec.trials()[2]

    assert partial.counts.recorded == len(spec.trials()) - 1
    assert ledger.find(late.trial_id) is None, "an interruption records nothing"
    finished = run_experiment(
        spec,
        REGISTRY,
        "reference",
        ledger,
        SpawnExecutor(where, workers=2, trials_per_worker=per_worker),
    )
    assert finished.counts.recorded == len(spec.trials())
    whole = run_experiment(spec, REGISTRY, "reference", _ledger(), SequentialExecutor())
    assert finished == whole


@LIFETIMES
def test_a_limit_times_the_trial_never_the_workers_start(per_worker: int | None) -> None:
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
        SpawnExecutor(
            "tests.integration.slow_import_definition:DEFINITION",
            workers=4,
            trials_per_worker=per_worker,
        ),
    )

    assert record.counts.recorded == len(spec.trials())
    assert record == run_experiment(spec, REGISTRY, "reference", _ledger(), SequentialExecutor())


@LIFETIMES
def test_what_a_trial_writes_never_reaches_the_workers_answers(per_worker: int | None) -> None:
    """A trial printing, and writing to file descriptor 1 itself, is still the reference trial."""

    spec, sequential = run()
    record = run_experiment(
        spec,
        REGISTRY,
        "chatty",
        _ledger(),
        SpawnExecutor(WHERE, workers=4, trials_per_worker=per_worker),
    )

    assert record == sequential


@LIFETIMES
def test_a_worker_that_dies_records_nothing_for_its_trial(per_worker: int | None) -> None:
    spec = DEFINITION.spec
    ledger = _ledger()
    record = run_experiment(
        spec,
        REGISTRY,
        "dies",
        ledger,
        SpawnExecutor(WHERE, workers=4, trials_per_worker=per_worker),
    )

    assert record.counts.recorded == len(spec.trials()) - 1
    assert ledger.find(spec.trials()[1].trial_id) is None


@LIFETIMES
def test_a_worker_that_loads_another_experiment_is_refused(per_worker: int | None) -> None:
    other = SLOW_DEFINITION.spec

    with pytest.raises(ExperimentError, match="loaded another experiment"):
        run_experiment(
            other,
            REGISTRY,
            "reference",
            _ledger(),
            SpawnExecutor(WHERE, workers=1, trials_per_worker=per_worker),
        )


@pytest.mark.parametrize(
    ("definition", "workers", "per_worker"),
    [
        ("no.colon", 1, None),
        ("module:", 1, None),
        (":NAME", 1, None),
        ("module:NAME", 0, None),
        ("module:NAME", True, None),
        ("module:NAME", 1, 0),
        ("module:NAME", 1, True),
        ("module:NAME", 1, 1.5),
    ],
)
def test_a_spawn_executor_names_its_definition_workers_and_their_lifetime(
    definition: str, workers: int, per_worker: int | None
) -> None:
    with pytest.raises(ExperimentError, match="SpawnExecutor"):
        SpawnExecutor(definition, workers, per_worker)


@pytest.mark.parametrize(("per_worker", "starts"), [(None, 1), (3, 4), (1, 10)])
def test_a_worker_loads_the_experiment_once_and_runs_as_many_trials_as_it_is_given(
    per_worker: int | None, starts: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One worker, ten trials: one start for the experiment, four of three, or ten of one."""

    log = tmp_path / "imports"
    monkeypatch.setenv("ALPHALAB_DEFINITION_IMPORTS", str(log))
    spec, sequential = run()
    executor = SpawnExecutor(
        "tests.integration.counting_definition:DEFINITION", workers=1, trials_per_worker=per_worker
    )

    assert run_experiment(spec, REGISTRY, "reference", _ledger(), executor) == sequential
    assert len(log.read_text(encoding="utf-8").split()) == starts
