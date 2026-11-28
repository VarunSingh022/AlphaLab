"""The research cluster is one of the ledger's executors: one executor path (ADR-0072, GAP-31).

:class:`~alphalab.lifecycle.experiments.ClusterExecutor` runs an experiment's
trials as registered tasks on :func:`~alphalab.cloud_research.run_cluster_cycle`:
``alphalab.distributed``'s job lifecycle tracks them, an assignment policy --
the cluster's own or one of ``alphalab.cluster_scheduler``'s -- places them, and
each runs in the spawned worker that refuses the network. These tests hold:

* the reference experiment on a cluster is the sequential experiment, record
  for record, under every policy and over as many cycles as its capacity needs;
* a policy that places no trial is refused, not waited on forever;
* a cluster with no worker, or with jobs of its own, is refused;
* a definition that reaches for the network fails every trial, as spawned;
* a trial past its limit is interrupted, records nothing, and a resume on the
  cluster finishes the experiment;
* a worker that loads another experiment is refused.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import pytest

from alphalab.cloud_research import (
    CloudResearchState,
    initialize_cluster,
    submit_research_job,
)
from alphalab.cluster_scheduler import assign_jobs_with_affinity, assign_jobs_with_aging
from alphalab.distributed import DistributedEngine, JobType
from alphalab.distributed.state import DistributedState
from alphalab.lifecycle.experiments import (
    ClusterExecutor,
    ExperimentError,
    SequentialExecutor,
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


def _cluster(workers: int = 2, capacity: int = 2) -> CloudResearchState:
    return initialize_cluster(
        "lab", num_workers=workers, capacity_per_worker=capacity, timestamp=0.0
    )


def test_an_experiment_on_a_cluster_is_the_sequential_one_record_for_record() -> None:
    spec, sequential = run()
    record = run_experiment(
        spec, REGISTRY, "reference", _ledger(), ClusterExecutor(WHERE, _cluster())
    )

    assert record == sequential
    assert record.experiment_record_id == sequential.experiment_record_id
    assert record.counts.recorded == len(spec.trials())


@pytest.mark.parametrize(
    "assign",
    [partial(assign_jobs_with_aging, aging_rate=0.5), assign_jobs_with_affinity],
    ids=["aging", "affinity"],
)
def test_a_cluster_scheduler_policy_places_the_trials_over_several_cycles_and_moves_no_record(
    assign: Any,
) -> None:
    """One worker with room for three: the ten trials take four cycles, and the record is one."""

    spec, sequential = run()
    executor = ClusterExecutor(WHERE, _cluster(workers=1, capacity=3), assign=assign, timestamp=5.0)
    record = run_experiment(spec, REGISTRY, "reference", _ledger(), executor)

    assert record == sequential


def test_a_cluster_yields_in_submission_order_whatever_order_trials_finish_in() -> None:
    """The protocol itself, read directly: one cycle, the first trial finishing last."""

    spec = DEFINITION.spec
    trials = spec.trials()
    executor = ClusterExecutor(WHERE, _cluster(workers=1, capacity=len(trials)))

    yielded = [
        ran.record.trial_id for ran in executor.execute(spec, REGISTRY, "slow_first", trials)
    ]

    assert yielded == [trial.trial_id for trial in trials]


def test_a_policy_that_places_no_trial_is_refused_not_waited_on() -> None:
    def nowhere(state: DistributedState, timestamp: float) -> DistributedState:
        return state

    ledger = _ledger()
    with pytest.raises(ExperimentError, match="took none of the 10 trials left"):
        run_experiment(
            DEFINITION.spec,
            REGISTRY,
            "reference",
            ledger,
            ClusterExecutor(WHERE, _cluster(), nowhere),
        )
    assert ledger.find(DEFINITION.spec.trials()[0].trial_id) is None


def test_a_cluster_with_no_worker_or_with_jobs_of_its_own_is_refused() -> None:
    empty = CloudResearchState(DistributedEngine.initialize("empty"))
    busy, _ = submit_research_job(_cluster(), JobType.OPTIMIZATION, "elsewhere", {}, 1, 0.0)

    with pytest.raises(ExperimentError, match="no worker"):
        ClusterExecutor(WHERE, empty)
    with pytest.raises(ExperimentError, match="idle cluster"):
        ClusterExecutor(WHERE, busy)


def test_a_definition_that_reaches_for_the_network_fails_every_trial_on_the_cluster() -> None:
    spec = DEFINITION.spec
    ledger = _ledger()
    executor = ClusterExecutor("tests.integration.spy_definition:DEFINITION", _cluster())
    record = run_experiment(spec, REGISTRY, "reference", ledger, executor)

    assert record.counts.failed == record.counts.recorded == len(spec.trials())
    held = ledger.find(spec.trials()[0].trial_id)
    assert held is not None and held.outcome is TrialOutcome.FAILED
    assert "the network is refused to a trial: socket" in held.diagnostics


def test_a_trial_past_its_limit_is_interrupted_and_a_resume_on_the_cluster_finishes() -> None:
    spec = SLOW_DEFINITION.spec
    ledger = _ledger()
    executor = ClusterExecutor("tests.integration.experiment_workload:SLOW_DEFINITION", _cluster())
    partial_record = run_experiment(spec, REGISTRY, "sleepy", ledger, executor)
    late = spec.trials()[2]

    assert partial_record.counts.recorded == len(spec.trials()) - 1
    assert ledger.find(late.trial_id) is None, "an interruption records nothing"
    finished = run_experiment(spec, REGISTRY, "reference", ledger, executor)
    whole = run_experiment(spec, REGISTRY, "reference", _ledger(), SequentialExecutor())
    assert finished == whole


def test_a_worker_that_loads_another_experiment_is_refused_on_the_cluster() -> None:
    with pytest.raises(ExperimentError, match="loaded another experiment"):
        run_experiment(
            SLOW_DEFINITION.spec,
            REGISTRY,
            "reference",
            _ledger(),
            ClusterExecutor(WHERE, _cluster(workers=1, capacity=1)),
        )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"definition": "no.colon"}, "ClusterExecutor.definition"),
        ({"definition": ":NAME"}, "ClusterExecutor.definition"),
        ({"cluster": "lab"}, "must be a CloudResearchState"),
        ({"assign": "fifo"}, "assign must be callable"),
        ({"timestamp": float("nan")}, "finite instant"),
        ({"timestamp": True}, "finite instant"),
    ],
)
def test_a_cluster_executor_states_its_definition_cluster_policy_and_instant(
    changes: dict[str, Any], match: str
) -> None:
    arguments: dict[str, Any] = {"definition": WHERE, "cluster": _cluster(), **changes}
    with pytest.raises(ExperimentError, match=match):
        ClusterExecutor(**arguments)
