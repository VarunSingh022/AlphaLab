"""Selection-aware evidence, and the promotion it refuses (v4.3, ADR-0073 section 8, M5 exit).

The walk-forward reference experiment is assessed, and its assessment recorded
as ``EXPERIMENT`` evidence for the strategy version that runs its selection:

* the evidence carries the search's size -- every trial the ledger recorded,
  failures included -- beside the statistics, derives its dataset and seed
  from the experiment, and points at the assessment's identity;
* a promotion on it passes a policy over those statistics;
* ``EXPERIMENT`` evidence that does not state its family count as a whole
  number of trials is refused by every policy, its thresholds met or not --
  nothing is promoted on a selection that hides the search behind it;
* the member was appended: no existing evidence identity moved, and the
  evidence survives a lifecycle snapshot.
"""

from __future__ import annotations

import functools
import hashlib
import math
from dataclasses import replace

import pytest

from alphalab.lifecycle import (
    EXPERIMENT_FAMILY_METRIC,
    ExperimentAssessment,
    ExperimentRecord,
    ExperimentSpec,
    LifecycleInputError,
    LifecycleState,
    LifecycleTransitionError,
    MetricThreshold,
    StrategyVersionRef,
    ValidationMethod,
    ValidationPolicy,
    assess_experiment,
    build_evidence,
    evaluate_policy,
    evidence_from_experiment,
    evidence_id_for,
    get_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_strategy,
    trial_matrix_of,
    verify_evidence_id,
)
from alphalab.lifecycle.experiments import SequentialExecutor, TrialLedger, run_experiment
from alphalab.lifecycle.governance import LIFECYCLE_PERMISSIONS, Governance, StaticPermissions
from alphalab.lifecycle.snapshot import capture, from_primitives, restore
from alphalab.model_registry import ModelStage
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.research.selection import SelectionMeasure, Trim
from alphalab.strategy import StrategyDefinition
from tests.integration.experiment_workload import REGISTRY, spec, walk_forward_spec

GOVERNANCE = Governance(StaticPermissions({"researcher": LIFECYCLE_PERMISSIONS}), "researcher")

#: A policy over the statistics alone: it names no family count, and needs none
#: named -- the refusal is every policy's.
POLICY = ValidationPolicy(
    "selection-aware",
    (
        MetricThreshold("experiment.pbo", maximum=0.5),
        MetricThreshold("experiment.spa_p_consistent", maximum=0.5),
    ),
    required_method=ValidationMethod.EXPERIMENT,
)


@functools.cache
def _assessed() -> tuple[ExperimentSpec, ExperimentRecord, ExperimentAssessment]:
    experiment = walk_forward_spec()
    ledger = TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))
    record = run_experiment(experiment, REGISTRY, "reference", ledger, SequentialExecutor())
    periods = trial_matrix_of(experiment, ledger, record, path=None).periods
    assessment = assess_experiment(
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
    return experiment, record, assessment


def _registered() -> tuple[LifecycleState, StrategyVersionRef]:
    """The strategy version that runs the selection, at NONE."""

    return register_strategy(
        LifecycleState(),
        "reference",
        StrategyDefinition(
            strategy_id="reference-normal-2-2",
            name="Reference",
            version="1",
            author="research",
            description="The walk-forward reference experiment's selection",
            parameters={"window": 2.0, "size": 2.0},
        ),
        5.0,
    )


def test_experiment_evidence_states_the_search_beside_its_statistics() -> None:
    experiment, record, assessment = _assessed()

    evidence = evidence_from_experiment(experiment, record, assessment, "strategy://x@1", 9.0)

    assert evidence.method is ValidationMethod.EXPERIMENT
    assert evidence.metrics[EXPERIMENT_FAMILY_METRIC] == 33.0 == record.counts.recorded
    assert evidence.metrics["experiment.pbo"] == 0.3
    assert evidence.metrics["experiment.reality_check_p"] == 69 / 200
    assert (
        evidence.metrics["experiment.spa_p_lower"],
        evidence.metrics["experiment.spa_p_consistent"],
        evidence.metrics["experiment.spa_p_upper"],
    ) == (23 / 100, 59 / 200, 21 / 50)
    assert assessment.deflated is not None
    assert "experiment.deflated_sharpe" not in evidence.metrics, (
        "class 3 (ADR-0058): read from the assessment, beside the evidence's identity"
    )
    assert evidence.metrics["experiment.sharpe"] == assessment.deflated.inference.sharpe
    assert evidence.metrics["experiment.deflated_sharpe_trials"] == 6.0
    assert evidence.dataset_id == experiment.dataset.dataset_id == "REFERENCE"
    assert evidence.seed == experiment.seed
    assert evidence.source_id == assessment.assessment_id
    assert verify_evidence_id(evidence)


def test_a_statistic_the_assessment_could_not_compute_is_left_out() -> None:
    experiment, record, assessment = _assessed()
    without = replace(assessment, superior=None, deflated=None)

    evidence = evidence_from_experiment(experiment, record, without, "strategy://x@1", 9.0)

    assert set(evidence.metrics) == {
        EXPERIMENT_FAMILY_METRIC,
        "experiment.pbo",
        "experiment.reality_check_p",
    }
    outcome = evaluate_policy(POLICY, evidence)
    assert not outcome.passed
    assert outcome.failures == (
        "'experiment.spa_p_consistent' is not among the recorded metrics.",
    ), "an absent statistic fails for want of the metric, never passes on a placeholder"


def test_evidence_is_of_its_own_experiment_and_record() -> None:
    experiment, record, assessment = _assessed()

    with pytest.raises(LifecycleInputError, match="The record is of experiment"):
        evidence_from_experiment(spec(), record, assessment, "strategy://x@1", 9.0)
    other = replace(record, selections=())
    with pytest.raises(LifecycleInputError, match="would be about another search"):
        evidence_from_experiment(experiment, other, assessment, "strategy://x@1", 9.0)


def test_a_selection_aware_promotion_passes_on_experiment_evidence() -> None:
    experiment, record, assessment = _assessed()
    state, ref = _registered()
    evidence = evidence_from_experiment(experiment, record, assessment, str(ref), 9.0)
    state = record_evidence(state, evidence)

    promoted = promote_strategy_version(
        state, GOVERNANCE, ref.name, ref.version, POLICY, evidence.evidence_id, 10.0
    )

    version = get_strategy_version(promoted.strategies, ref.name, ref.version)
    assert version.stage is ModelStage.STAGING
    assert version.evidence_id == evidence.evidence_id


@pytest.mark.parametrize(
    "count",
    [None, 0.0, -3.0, 2.5, math.nan, math.inf, True],
    ids=["absent", "zero", "negative", "fraction", "nan", "infinite", "a-boolean"],
)
def test_experiment_evidence_without_its_family_count_is_never_promoted(
    count: float | None,
) -> None:
    """M5's exit gate: the policy's thresholds are met, and the promotion is refused."""

    state, ref = _registered()
    metrics = {"experiment.pbo": 0.1, "experiment.spa_p_consistent": 0.01}
    if count is not None:
        metrics[EXPERIMENT_FAMILY_METRIC] = count
    evidence = build_evidence(
        ValidationMethod.EXPERIMENT, str(ref), "REFERENCE", metrics, 9.0, seed=7
    )
    state = record_evidence(state, evidence)

    expected = "states no family count" if count is None else "a whole number of trials"
    with pytest.raises(LifecycleTransitionError, match=expected):
        promote_strategy_version(
            state, GOVERNANCE, ref.name, ref.version, POLICY, evidence.evidence_id, 10.0
        )
    assert get_strategy_version(state.strategies, ref.name, ref.version).stage is ModelStage.NONE
    lenient = ValidationPolicy("any-method", (MetricThreshold("experiment.pbo", maximum=1.0),))
    assert not evaluate_policy(lenient, evidence).passed, "every policy refuses it"


def test_other_evidence_needs_no_family_count() -> None:
    evidence = build_evidence(
        ValidationMethod.BACKTEST, "strategy://x@1", "REFERENCE", {"sharpe_ratio": 1.4}, 9.0
    )
    policy = ValidationPolicy("backtest", (MetricThreshold("sharpe_ratio", minimum=1.0),))

    assert evaluate_policy(policy, evidence).passed


def test_the_member_was_appended_and_moved_no_identity() -> None:
    rendering = [
        "subject=strategy://x@1",
        "dataset=ds@abc",
        "seed=7",
        "metrics",
    ]
    backtest = hashlib.sha256(
        "\n".join(["method=BACKTEST", *rendering, "sharpe_ratio=1.5"]).encode("utf-8")
    ).hexdigest()
    experiment = hashlib.sha256(
        "\n".join(["method=EXPERIMENT", *rendering, "experiment.trials=33.0"]).encode("utf-8")
    ).hexdigest()

    assert [member.name for member in ValidationMethod] == [
        "BACKTEST",
        "RESEARCH",
        "EXTERNAL",
        "STUDY",
        "EXPERIMENT",
    ]
    assert (
        evidence_id_for(
            ValidationMethod.BACKTEST, "strategy://x@1", "ds@abc", 7, {"sharpe_ratio": 1.5}
        )
        == backtest
    )
    assert (
        evidence_id_for(
            ValidationMethod.EXPERIMENT,
            "strategy://x@1",
            "ds@abc",
            7,
            {EXPERIMENT_FAMILY_METRIC: 33.0},
        )
        == experiment
    )


def test_experiment_evidence_survives_a_lifecycle_snapshot() -> None:
    experiment, record, assessment = _assessed()
    state, ref = _registered()
    evidence = evidence_from_experiment(experiment, record, assessment, str(ref), 9.0)
    state = record_evidence(state, evidence)

    restored = restore(from_primitives(deserialize(serialize(capture(state)))), {})

    assert restored == state
    held = restored.evidence[evidence.evidence_id]
    assert held.method is ValidationMethod.EXPERIMENT and verify_evidence_id(held)
