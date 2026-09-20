"""The experiment contract: identity, immutability, and the refusals around it.

A study is reproducible from its persisted configuration or it is not a study.
Three properties carry that, and each has its own section here.

**Identity is derived from the description.** Two identically-described studies
share a ``study_id`` with no shared state; anything that changes the experiment
changes it. The canonical rendering is pinned so it cannot move by accident.

**A result's identity is derived from the numbers.** It answers a different
question from the study's -- "did two runs agree?" rather than "which
experiment is this?" -- and is therefore content-derived, and tamper-evident.

**Wall-clock time is recorded and never hashed.** Re-running the same study
tomorrow produces the same ``result_id``.
"""

import pytest

from alphalab.factor_library import FeatureDefinition, FeatureField, FeatureKind
from alphalab.research import (
    RESULT_KEY_SCHEME,
    STUDY_KEY_SCHEME,
    ResearchStudy,
    ResearchValidationError,
    build_result,
    canonical_result_key,
    canonical_study_key,
    derive_result_id,
    derive_study_id,
)

MOMENTUM = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)
REVERSION = FeatureDefinition("rev_10", FeatureKind.MEAN_REVERSION, FeatureField.CLOSE, window=10)


def _study(**overrides: object) -> ResearchStudy:
    base: dict[str, object] = {
        "study_name": "example",
        "dataset_version": "panel@abc123",
        "universe": ("AAA", "BBB"),
        "features": (MOMENTUM,),
        "horizons": (1, 5),
        "splits": "walk_forward(mode=ROLLING)",
        "seed": 7,
        "notes": "a note",
    }
    return ResearchStudy(**{**base, **overrides})  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Construction refuses an under-specified experiment
# --------------------------------------------------------------------------- #


def test_a_study_must_name_its_universe_and_its_features() -> None:
    with pytest.raises(ResearchValidationError, match="must name the symbols"):
        _study(universe=())
    with pytest.raises(ResearchValidationError, match="at least one feature"):
        _study(features=())


def test_a_repeated_symbol_or_a_repeated_computation_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="repeats a symbol"):
        _study(universe=("AAA", "AAA"))
    with pytest.raises(ResearchValidationError, match="same derived version"):
        _study(features=(MOMENTUM, MOMENTUM))


def test_an_empty_or_at_bearing_name_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="must be named"):
        _study(study_name="   ")
    with pytest.raises(ResearchValidationError, match="may not contain '@'"):
        _study(study_name="a@b")


def test_a_non_positive_horizon_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="at least 1 observation"):
        _study(horizons=(0,))


def test_the_universe_and_horizons_are_sorted_on_construction() -> None:
    """Two studies listing the same names in different orders are one study."""

    assert _study(universe=("BBB", "AAA")).universe == ("AAA", "BBB")
    assert _study(horizons=(20, 1, 5)).horizons == (1, 5, 20)
    assert _study(universe=("BBB", "AAA")).study_id == _study(universe=("AAA", "BBB")).study_id


def test_feature_order_is_preserved_because_it_is_part_of_the_pipeline() -> None:
    """Neutralize-then-rank is not the same pipeline as rank-then-neutralize."""

    forward = _study(features=(MOMENTUM, REVERSION))
    backward = _study(features=(REVERSION, MOMENTUM))

    assert forward.features == (MOMENTUM, REVERSION)
    assert forward.study_id != backward.study_id


def test_parameters_are_genuinely_immutable() -> None:
    supplied = {"cost": 0.001}
    study = _study(parameters=supplied)

    supplied["cost"] = 99.0
    assert study.parameters["cost"] == 0.001
    with pytest.raises(TypeError):
        study.parameters["cost"] = 99.0  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_the_same_description_derives_the_same_study_id() -> None:
    assert _study().study_id == _study().study_id
    assert _study().study_id.startswith("example@")
    assert derive_study_id(_study()) == _study().study_id


@pytest.mark.parametrize(
    "changed",
    [
        {"study_name": "other"},
        {"dataset_version": "panel@zzz"},
        {"universe": ("AAA", "CCC")},
        {"features": (REVERSION,)},
        {"horizons": (1, 10)},
        {"splits": "cross_validation(method=PURGED)"},
        {"seed": 8},
        {"notes": "a different note"},
        {"parameters": {"cost": 0.001}},
    ],
)
def test_anything_that_changes_the_experiment_changes_the_study_id(
    changed: dict[str, object],
) -> None:
    assert _study(**changed).study_id != _study().study_id


def test_the_canonical_study_rendering_is_pinned() -> None:
    """Changing this re-identifies every study in existence; it needs an ADR."""

    study = ResearchStudy(
        study_name="example",
        dataset_version="panel@abc123",
        universe=("AAA", "BBB"),
        features=(MOMENTUM,),
        horizons=(1, 5),
        splits="scheme",
        parameters={"cost": 0.001},
        seed=7,
        notes="note",
    )

    assert canonical_study_key(study) == "\n".join(
        [
            STUDY_KEY_SCHEME,
            "name=example",
            "dataset=panel@abc123",
            "splits=scheme",
            "seed=7",
            "notes=note",
            "universe",
            "AAA",
            "BBB",
            "features",
            MOMENTUM.feature_version,
            "horizons",
            "1",
            "5",
            "parameters",
            "cost=0.001",
        ]
    )


def test_the_canonical_result_rendering_is_pinned() -> None:
    study = _study()

    assert canonical_result_key(study, {"b": 2.0, "a": 1.0}) == "\n".join(
        [RESULT_KEY_SCHEME, f"study={study.study_id}", "metrics", "a=1.0", "b=2.0"]
    )


# --------------------------------------------------------------------------- #
# Lineage is required where it matters
# --------------------------------------------------------------------------- #


def test_a_study_with_no_dataset_refuses_when_lineage_is_not_optional() -> None:
    study = _study(dataset_version=None)

    assert study.study_id, "the study still has an identity"
    with pytest.raises(ResearchValidationError, match="names no dataset version"):
        study.require_dataset()


def test_a_study_with_no_seed_refuses_when_a_stochastic_step_needs_one() -> None:
    study = _study(seed=None)

    assert study.study_id
    with pytest.raises(ResearchValidationError, match="no default, because a default seed"):
        study.require_seed()


def test_require_dataset_returns_the_version_when_there_is_one() -> None:
    assert _study().require_dataset() == "panel@abc123"
    assert _study().require_seed() == 7


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


def test_a_result_is_derived_from_the_study_and_its_numbers() -> None:
    study = _study()
    metrics = {"mom_20.h5.rank_ic": 0.031}

    result = build_result(study, metrics, produced_at=1000.0)

    assert result.result_id == derive_result_id(study, metrics)
    assert result.study_id == study.study_id
    assert result.dataset_version == "panel@abc123"
    assert result.verify()


def test_the_result_id_does_not_depend_on_the_clock() -> None:
    """Re-running the same study tomorrow must reproduce the same identity."""

    study = _study()
    metrics = {"ic": 0.02}

    early = build_result(study, metrics, produced_at=1.0)
    late = build_result(study, metrics, produced_at=999_999.0)

    assert early.result_id == late.result_id
    assert early.produced_at != late.produced_at


def test_changing_a_metric_changes_the_result_id() -> None:
    study = _study()

    first = build_result(study, {"ic": 0.02}, 1.0)
    second = build_result(study, {"ic": 0.03}, 1.0)

    assert first.result_id != second.result_id


def test_a_result_whose_numbers_were_edited_no_longer_verifies() -> None:
    """The tamper check ``verify_evidence_id`` performs, on a study result."""

    from dataclasses import replace

    result = build_result(_study(), {"ic": 0.02}, 1.0)
    tampered = replace(result, metrics={"ic": 0.99})

    assert result.verify()
    assert not tampered.verify()


def test_a_result_with_no_metrics_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="nothing to record as a result"):
        build_result(_study(), {}, 1.0)


def test_a_result_may_not_name_a_feature_the_study_does_not_run() -> None:
    """The study-level form of the dataset substitution ADR-0017 closed."""

    with pytest.raises(ResearchValidationError, match="the study does not run"):
        build_result(
            _study(),
            {"ic": 0.02},
            1.0,
            feature_lineage={REVERSION.feature_version: "deadbeef"},
        )


def test_a_result_carries_the_lineage_of_every_feature_it_names() -> None:
    result = build_result(
        _study(),
        {"ic": 0.02},
        1.0,
        feature_lineage={MOMENTUM.feature_version: "abc"},
        findings=("a finding",),
        warnings=("a warning",),
    )

    assert result.feature_lineage[MOMENTUM.feature_version] == "abc"
    assert result.findings == ("a finding",)
    assert result.warnings == ("a warning",)


def test_a_results_metrics_are_immutable() -> None:
    result = build_result(_study(), {"ic": 0.02}, 1.0)

    with pytest.raises(TypeError):
        result.metrics["ic"] = 99.0  # type: ignore[index]


def test_a_result_reads_its_dataset_through_the_study_rather_than_storing_it_twice() -> None:
    """One fact, one home: a result cannot disagree with its own study."""

    import dataclasses

    result = build_result(_study(), {"ic": 0.02}, 1.0)
    fields = {field.name for field in dataclasses.fields(result)}

    assert "dataset_version" not in fields
    assert result.dataset_version == result.study.dataset_version
