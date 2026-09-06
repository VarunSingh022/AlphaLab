"""BACKTEST evidence names the data the run consumed, not the data a caller claims.

``evidence.py`` documented this defect about itself: ``dataset_id`` was "named by
the caller: neither a ``BacktestResult`` nor a ``ResearchState`` carries the
dataset it consumed, and inventing one would be a guess." Phase 2 gave a run its
dataset identity. This is ADR-0017's M3, which makes evidence *derive* it.

That mattered because ``evidence_id_for`` hashes ``dataset_id``. Until now the
digest was only as trustworthy as a string somebody typed: two runs over
genuinely different data could be handed one ``dataset_id``, produce evidence
that verified cleanly, and pass the gate. The tamper-evidence was real for
metrics and decorative for data identity.

The digest itself is FROZEN
---------------------------
M3 changes *who supplies* ``dataset_id``, not how it is hashed.
``evidence_id_for``'s inputs, argument order, canonical rendering, sorting and
algorithm are untouched, which is the whole reason this release needs no schema
bump, no migration and no dual verification: a v2.6 evidence object hashes to
exactly what it always did. The golden digests below are pinned literals
computed from the v2.6 implementation, and they must never move.
"""

import inspect
from dataclasses import fields
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.state import BacktestResult
from alphalab.lifecycle.evidence import (
    MetricThreshold,
    ValidationEvidence,
    ValidationMethod,
    ValidationPolicy,
    build_evidence,
    evaluate_policy,
    evidence_from_backtest,
    evidence_from_research,
    evidence_id_for,
    verify_evidence_id,
)
from alphalab.lifecycle.exceptions import LifecycleInputError
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

MIDS = [Decimal("100.005"), Decimal("120.007"), Decimal("119.003")]
PLAN = {2.0: Decimal("10")}

#: Digests produced by the v2.6 implementation, captured before M3 was written.
#: These are the release's compatibility contract: if any of them moves, a
#: promotion recorded under v2.6 stops verifying and reports as tampered.
GOLDEN_DIGESTS = {
    "backtest": (
        ValidationMethod.BACKTEST,
        "ma-crossover@1",
        "DS-2024-Q1",
        20240,
        {"sharpe_ratio": 1.5, "max_drawdown": -0.2, "total_return": 0.42},
        "88a83d0dd87335ed86f14f890ddf7f2f14007e60027218ffc085a8fe4739ac1c",
    ),
    "research": (
        ValidationMethod.RESEARCH,
        "momentum@3",
        "DS-RESEARCH",
        None,
        {"overall_score": 88.5, "bias_score": 91.0},
        "160252fbae328c712ddbef5598991a8034090d5492369d552cd5932148b0785c",
    ),
    "external": (
        ValidationMethod.EXTERNAL,
        "vendor@1",
        "VENDOR-DS",
        None,
        {"alpha": 0.01},
        "63bd9cb706aaa436742db5c233dd16e34f015b037a50b2b903430e5fb0d6a114",
    ),
    "empty-metrics": (
        ValidationMethod.BACKTEST,
        "s",
        "d",
        0,
        {},
        "b1a18c3eb438294978b14edaa16a84044b85a2882248990bf990c25f9b03d370",
    ),
}


def _run(dataset_id: str = "DS", seed: int | None = 20240) -> BacktestResult:
    """A real run through the whole execution path, over a named dataset."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    return BacktestEngine.run(
        backtest_config(strategy_id, seed=seed),
        dataset_of_quotes(asset_id, MIDS, dataset_id=dataset_id),
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, PLAN)),
        context_factory,
    )


# --------------------------------------------------------------------------- #
# 1-3. Derivation, and the refusal when there is nothing to derive
# --------------------------------------------------------------------------- #


def test_backtest_evidence_names_the_dataset_the_run_consumed() -> None:
    result = _run(dataset_id="DS-DERIVED")

    evidence = evidence_from_backtest(result, "ma-crossover@1", 6.0)

    assert evidence.dataset_id == "DS-DERIVED" == result.dataset_id
    assert evidence.method is ValidationMethod.BACKTEST


def test_a_run_that_named_no_dataset_cannot_become_evidence() -> None:
    """A hand-driven run has nothing to name, and evidence must not invent one."""

    result = _run()
    unnamed = BacktestResult(
        config=result.config,
        state=result.state,
        steps=result.steps,
        records_processed=result.records_processed,
        seed=result.seed,
    )
    assert unnamed.dataset_id is None

    with pytest.raises(LifecycleInputError) as error:
        evidence_from_backtest(unnamed, "ma-crossover@1", 6.0)

    message = str(error.value)
    assert "dataset" in message.lower(), "the refusal must name the missing identity"
    assert "BacktestEngine.run" in message, "and must say how to get one"


def test_the_refusal_is_not_satisfied_by_a_fabricated_sentinel() -> None:
    """`""` would read as a recorded identity; only a real one is accepted."""

    result = _run(dataset_id="REAL")

    assert evidence_from_backtest(result, "s@1", 1.0).dataset_id == "REAL"


# --------------------------------------------------------------------------- #
# 4. The caller can no longer assert a dataset the run did not consume
# --------------------------------------------------------------------------- #


def test_the_dataset_id_parameter_is_gone_rather_than_ignored() -> None:
    """Removed, not accepted-and-discarded: a silently dropped argument would
    leave callers believing they had set something."""

    parameters = inspect.signature(evidence_from_backtest).parameters

    assert list(parameters) == ["result", "subject", "produced_at"]
    assert "dataset_id" not in parameters


def test_passing_an_alternate_dataset_id_is_a_hard_error() -> None:
    result = _run(dataset_id="REAL")

    with pytest.raises(TypeError):
        evidence_from_backtest(result, "s@1", 1.0, "CLAIMED")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# 5. The identity is load-bearing in the digest
# --------------------------------------------------------------------------- #


def test_two_runs_over_different_datasets_cannot_share_an_evidence_id() -> None:
    """The point of deriving it: identical metrics over different data differ."""

    left = evidence_from_backtest(_run(dataset_id="DS-A"), "ma-crossover@1", 6.0)
    right = evidence_from_backtest(_run(dataset_id="DS-B"), "ma-crossover@1", 6.0)

    assert left.metrics == right.metrics, "same scripted run, same numbers"
    assert left.dataset_id != right.dataset_id
    assert left.evidence_id != right.evidence_id


# --------------------------------------------------------------------------- #
# 6. The frozen digest
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", sorted(GOLDEN_DIGESTS), ids=sorted(GOLDEN_DIGESTS))
def test_the_evidence_digest_is_frozen(case: str) -> None:
    """Pinned v2.6 output. A change here breaks every promotion ever recorded."""

    method, subject, dataset_id, seed, metrics, expected = GOLDEN_DIGESTS[case]

    assert evidence_id_for(method, subject, dataset_id, seed, metrics) == expected


def test_the_digest_signature_is_frozen() -> None:
    """Argument order is part of the contract, not an implementation detail."""

    assert list(inspect.signature(evidence_id_for).parameters) == [
        "method",
        "subject",
        "dataset_id",
        "seed",
        "metrics",
    ]


def test_metric_order_still_does_not_affect_the_digest() -> None:
    method, subject, dataset_id, seed, metrics, expected = GOLDEN_DIGESTS["backtest"]

    reversed_metrics = dict(reversed(list(metrics.items())))

    assert evidence_id_for(method, subject, dataset_id, seed, reversed_metrics) == expected


# --------------------------------------------------------------------------- #
# 7. v2.6 evidence keeps verifying
# --------------------------------------------------------------------------- #


def test_evidence_recorded_under_v2_6_still_verifies() -> None:
    """Reconstructed exactly as a persisted v2.6 record would read back."""

    method, subject, dataset_id, seed, metrics, digest = GOLDEN_DIGESTS["backtest"]
    persisted = ValidationEvidence(
        evidence_id=digest,
        method=method,
        subject=subject,
        dataset_id=dataset_id,
        metrics=metrics,
        seed=seed,
        produced_at=6.0,
        source_id="report-1",
    )

    assert verify_evidence_id(persisted) is True


def test_a_v2_6_promotion_still_passes_its_policy() -> None:
    """Not merely verifiable -- still *usable* as the justification it was."""

    method, subject, dataset_id, seed, metrics, digest = GOLDEN_DIGESTS["backtest"]
    persisted = ValidationEvidence(digest, method, subject, dataset_id, metrics, seed, 6.0, "r1")
    policy = ValidationPolicy(
        "prod-v1",
        (
            MetricThreshold("sharpe_ratio", minimum=1.0),
            MetricThreshold("max_drawdown", maximum=0.0),
        ),
        required_method=ValidationMethod.BACKTEST,
    )

    outcome = evaluate_policy(policy, persisted)

    assert outcome.passed, outcome.failures
    assert outcome.failures == ()


def test_a_v2_6_record_whose_numbers_were_edited_still_fails() -> None:
    """The tamper check must not have been weakened while being preserved."""

    method, subject, dataset_id, seed, metrics, digest = GOLDEN_DIGESTS["backtest"]
    tampered = ValidationEvidence(
        digest, method, subject, dataset_id, {**metrics, "sharpe_ratio": 9.9}, seed, 6.0, "r1"
    )

    assert verify_evidence_id(tampered) is False


def test_a_v2_6_record_whose_dataset_was_edited_still_fails() -> None:
    method, subject, _, seed, metrics, digest = GOLDEN_DIGESTS["backtest"]
    relabelled = ValidationEvidence(digest, method, subject, "OTHER-DS", metrics, seed, 6.0, "r1")

    assert verify_evidence_id(relabelled) is False


# --------------------------------------------------------------------------- #
# 8. The persisted shape does not move
# --------------------------------------------------------------------------- #


def test_validation_evidence_gains_no_field() -> None:
    """Richer provenance is deferred: a new field would enter the digest or sit
    outside it untamper-evident. ADR-0017 defers both."""

    assert [f.name for f in fields(ValidationEvidence)] == [
        "evidence_id",
        "method",
        "subject",
        "dataset_id",
        "metrics",
        "seed",
        "produced_at",
        "source_id",
    ]


def test_evidence_source_id_still_means_the_report_not_a_market_stream() -> None:
    """`ValidationEvidence.source_id` and `SessionState.source_id` are different
    concepts that happen to share a name; M3 must not merge them."""

    evidence = evidence_from_backtest(_run(), "ma-crossover@1", 6.0)
    report = _run().report

    assert report is not None
    assert evidence.source_id and not evidence.source_id.startswith("DS")


def test_the_lifecycle_snapshot_schema_does_not_move() -> None:
    from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA

    assert LIFECYCLE_SNAPSHOT_SCHEMA == 1


# --------------------------------------------------------------------------- #
# 10. Research evidence stays caller-supplied
# --------------------------------------------------------------------------- #


def test_research_evidence_still_takes_its_dataset_from_the_caller() -> None:
    """`ResearchState` does not know what data it consumed, and M3 does not
    pretend otherwise. The asymmetry with BACKTEST is deliberate and honest."""

    parameters = inspect.signature(evidence_from_research).parameters

    assert list(parameters) == ["state", "subject", "dataset_id", "produced_at"]


def test_build_evidence_still_accepts_an_explicit_dataset_for_external_numbers() -> None:
    """EXTERNAL measurements were not produced here; only a caller can name their data."""

    evidence = build_evidence(
        ValidationMethod.EXTERNAL, "vendor@1", "VENDOR-DS", {"alpha": 0.01}, 1.0
    )

    assert evidence.dataset_id == "VENDOR-DS"
    assert verify_evidence_id(evidence)


# --------------------------------------------------------------------------- #
# A persisted v2.6 lifecycle still restores, verifies and gates
# --------------------------------------------------------------------------- #


def test_a_v2_6_lifecycle_snapshot_restores_verifies_and_still_gates() -> None:
    """The compatibility claim end to end, through the real serializer.

    ADR-0017 rests on this: because the digest did not move, a lifecycle
    recorded by v2.6 reads back under v2.7 and its evidence still justifies the
    promotion it justified then. No schema bump, no migration, no dual
    verification.
    """

    from alphalab.lifecycle.promotion import promote_strategy_version, record_evidence
    from alphalab.lifecycle.registration import register_strategy
    from alphalab.lifecycle.snapshot import capture, from_primitives, restore
    from alphalab.lifecycle.state import LifecycleState
    from alphalab.persistence.serializer import deserialize, serialize
    from alphalab.studio.strategy import StrategyDefinition

    # Evidence built exactly as v2.6 built it: an explicitly named dataset.
    evidence = build_evidence(
        ValidationMethod.BACKTEST, "ma-crossover@1", "ds-v26", {"sharpe_ratio": 1.4}, 6.0, seed=7
    )
    policy = ValidationPolicy(
        "prod-v1", (MetricThreshold("sharpe_ratio", minimum=1.0),), ValidationMethod.BACKTEST
    )

    definition = StrategyDefinition("ma-1", "MA crossover", "1", "quant", "d", {"fast": 5.0})
    state, ref = register_strategy(LifecycleState(), "ma-crossover", definition, 5.0)
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, ref.name, ref.version, policy, evidence.evidence_id, 7.0
    )

    restored = restore(from_primitives(deserialize(serialize(capture(state)))), {})

    recovered = restored.evidence[evidence.evidence_id]
    assert verify_evidence_id(recovered), "a v2.6 digest must still verify"
    assert recovered.evidence_id == evidence.evidence_id
    assert evaluate_policy(policy, recovered).passed, "and must still justify its promotion"
