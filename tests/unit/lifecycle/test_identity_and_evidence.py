"""Typed references and validation evidence.

Identity: a model version and a strategy version are the same shape, so the
tests here are mostly about them staying distinguishable and about a reference
round-tripping through the form a release manifest carries.

Evidence: deterministic ids derived from content, extraction from the two
report types AlphaLab already produces, and a policy that names every failure
rather than returning a bare ``False``.
"""

import pytest

from alphalab.lifecycle import (
    DeploymentRef,
    LifecycleInputError,
    MetricThreshold,
    ModelRef,
    StrategyVersionRef,
    ValidationEvidence,
    ValidationMethod,
    ValidationPolicy,
    build_evidence,
    evaluate_policy,
    evidence_id_for,
    parse_ref,
    verify_evidence_id,
)

METRICS = {"sharpe_ratio": 1.4, "max_drawdown": 0.12}
POLICY = ValidationPolicy(
    "prod-v1",
    (
        MetricThreshold("sharpe_ratio", minimum=1.0),
        MetricThreshold("max_drawdown", maximum=0.25),
    ),
)


def _evidence(**overrides: object) -> ValidationEvidence:
    fields: dict[str, object] = {
        "method": ValidationMethod.EXTERNAL,
        "subject": "ma-crossover@1",
        "dataset_id": "ds-1",
        "metrics": METRICS,
        "produced_at": 1.0,
        "seed": 7,
    }
    fields.update(overrides)
    return build_evidence(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_references_render_as_name_at_version() -> None:
    assert str(ModelRef("momentum", 3)) == "momentum@3"
    assert str(StrategyVersionRef("ma-crossover", 1)) == "ma-crossover@1"
    assert str(DeploymentRef("paper", "ma-crossover", 2)) == "paper:ma-crossover@2"


def test_a_reference_round_trips_through_its_rendering() -> None:
    reference = ModelRef("momentum", 12)
    assert parse_ref(str(reference)) == ("momentum", 12)


def test_model_and_strategy_references_are_not_interchangeable() -> None:
    """Same fields, same rendering -- and still not equal, which is the point.

    mypy refuses the comparison outright as non-overlapping, which is the
    stronger guarantee; the operands are widened to ``object`` here so the
    runtime behaviour is pinned as well.
    """
    model: object = ModelRef("x", 1)
    strategy: object = StrategyVersionRef("x", 1)

    assert model != strategy
    assert str(model) == str(strategy)


def test_references_are_hashable_and_compare_by_value() -> None:
    assert ModelRef("m", 1) == ModelRef("m", 1)
    assert len({ModelRef("m", 1), ModelRef("m", 1), ModelRef("m", 2)}) == 2


def test_a_name_containing_the_separator_is_refused() -> None:
    """It would render to a reference that parses back to something else."""
    with pytest.raises(LifecycleInputError, match="cannot contain"):
        ModelRef("mom@entum", 1)


@pytest.mark.parametrize("version", [0, -1])
def test_a_version_below_one_is_refused(version: int) -> None:
    with pytest.raises(LifecycleInputError):
        StrategyVersionRef("s", version)


def test_a_blank_name_or_environment_is_refused() -> None:
    with pytest.raises(LifecycleInputError):
        ModelRef("  ", 1)
    with pytest.raises(LifecycleInputError):
        DeploymentRef("  ", "r", 1)


@pytest.mark.parametrize("bad", ["momentum", "momentum@", "momentum@x", "momentum@0"])
def test_parsing_a_malformed_reference_fails_explicitly(bad: str) -> None:
    with pytest.raises(LifecycleInputError):
        parse_ref(bad)


# --------------------------------------------------------------------------- #
# Evidence identity
# --------------------------------------------------------------------------- #


def test_the_same_measurement_always_gets_the_same_id() -> None:
    assert _evidence().evidence_id == _evidence().evidence_id


def test_the_id_does_not_depend_on_metric_ordering() -> None:
    forward = _evidence(metrics={"a": 1.0, "b": 2.0})
    reversed_order = _evidence(metrics={"b": 2.0, "a": 1.0})
    assert forward.evidence_id == reversed_order.evidence_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", "ma-crossover@2"),
        ("dataset_id", "ds-2"),
        ("seed", 8),
        ("method", ValidationMethod.BACKTEST),
        ("metrics", {"sharpe_ratio": 1.5, "max_drawdown": 0.12}),
    ],
)
def test_changing_any_recorded_fact_changes_the_id(field: str, value: object) -> None:
    assert _evidence().evidence_id != _evidence(**{field: value}).evidence_id


def test_a_float_that_differs_in_the_last_bit_changes_the_id() -> None:
    """repr, not str: two floats that print the same must not hash the same."""
    assert (
        _evidence(metrics={"m": 0.1 + 0.2}).evidence_id != _evidence(metrics={"m": 0.3}).evidence_id
    )


def test_evidence_verifies_against_its_own_content() -> None:
    assert verify_evidence_id(_evidence())


def test_edited_evidence_stops_verifying() -> None:
    from dataclasses import replace

    tampered = replace(_evidence(), metrics={"sharpe_ratio": 9.9, "max_drawdown": 0.01})
    assert not verify_evidence_id(tampered)


def test_evidence_id_for_matches_what_build_evidence_stored() -> None:
    evidence = _evidence()
    assert evidence.evidence_id == evidence_id_for(
        evidence.method, evidence.subject, evidence.dataset_id, evidence.seed, evidence.metrics
    )


def test_evidence_requires_an_id_and_a_subject() -> None:
    with pytest.raises(LifecycleInputError):
        ValidationEvidence("", ValidationMethod.EXTERNAL, "s", "d")
    with pytest.raises(LifecycleInputError):
        ValidationEvidence("id", ValidationMethod.EXTERNAL, "  ", "d")


# --------------------------------------------------------------------------- #
# Policy evaluation
# --------------------------------------------------------------------------- #


def test_evidence_meeting_every_threshold_passes_with_no_failures() -> None:
    outcome = evaluate_policy(POLICY, _evidence())
    assert outcome.passed
    assert outcome.failures == ()
    assert outcome.policy_id == "prod-v1"


def test_a_minimum_and_a_maximum_are_both_enforced() -> None:
    below = evaluate_policy(POLICY, _evidence(metrics={"sharpe_ratio": 0.4, "max_drawdown": 0.1}))
    above = evaluate_policy(POLICY, _evidence(metrics={"sharpe_ratio": 2.0, "max_drawdown": 0.9}))

    assert not below.passed
    assert "below the minimum" in below.failures[0]
    assert not above.passed
    assert "above the maximum" in above.failures[0]


def test_every_failing_check_is_named_not_just_the_first() -> None:
    outcome = evaluate_policy(POLICY, _evidence(metrics={"sharpe_ratio": 0.1, "max_drawdown": 0.9}))
    assert len(outcome.failures) == 2


def test_a_missing_metric_fails_rather_than_being_skipped() -> None:
    """An absent number is not a passing one."""
    outcome = evaluate_policy(POLICY, _evidence(metrics={"max_drawdown": 0.1}))
    assert not outcome.passed
    assert "'sharpe_ratio' is not among the recorded metrics." in outcome.failures


def test_a_policy_can_require_the_evidence_to_come_from_a_real_run() -> None:
    policy = ValidationPolicy(
        "backtested",
        (MetricThreshold("sharpe_ratio", minimum=1.0),),
        required_method=ValidationMethod.BACKTEST,
    )
    outcome = evaluate_policy(policy, _evidence())

    assert not outcome.passed
    assert "requires BACKTEST evidence" in outcome.failures[0]


def test_evidence_edited_after_recording_fails_before_any_threshold_is_read() -> None:
    from dataclasses import replace

    tampered = replace(_evidence(), metrics={"sharpe_ratio": 99.0, "max_drawdown": 0.0})
    outcome = evaluate_policy(POLICY, tampered)

    assert not outcome.passed
    assert "does not match its own content" in outcome.failures[0]


def test_evaluation_is_deterministic() -> None:
    evidence = _evidence(metrics={"sharpe_ratio": 0.1, "max_drawdown": 0.9})
    assert evaluate_policy(POLICY, evidence) == evaluate_policy(POLICY, evidence)


def test_a_policy_that_checks_nothing_is_refused() -> None:
    """It would make every promotion look validated."""
    with pytest.raises(LifecycleInputError, match="states no thresholds"):
        ValidationPolicy("empty", ())


def test_a_threshold_that_bounds_nothing_is_refused() -> None:
    with pytest.raises(LifecycleInputError, match="checks nothing"):
        MetricThreshold("sharpe_ratio")


def test_a_policy_needs_an_id() -> None:
    with pytest.raises(LifecycleInputError):
        ValidationPolicy("  ", (MetricThreshold("m", minimum=0.0),))
