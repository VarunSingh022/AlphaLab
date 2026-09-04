"""Comprehensive tests for the Model Registry: registration/versioning, stage
promotion, production rollback, deployment metadata, functional purity, and a
real ``alphalab.ml`` + ``alphalab.experiment_tracking`` integration."""

from dataclasses import FrozenInstanceError

import pytest

from alphalab.experiment_tracking import complete_run, start_run
from alphalab.experiment_tracking.tracker import ExperimentTracker
from alphalab.ml import LinearRegressionModel, predict_linear, train_linear_regression
from alphalab.model_registry import (
    DeploymentMetadata,
    ModelRegistry,
    ModelRegistryInputError,
    ModelStage,
    ModelVersion,
    deployed_versions,
    deployment_metadata,
    get_model,
    get_version,
    latest_version,
    list_versions,
    model_names,
    previous_production_version,
    production_version,
    promote,
    promotion_history,
    register_model,
    rollback,
    set_deployment_metadata,
    staging_version,
    versions_in_stage,
)


def _prod(registry: ModelRegistry, name: str) -> ModelVersion:
    version = production_version(registry, name)
    assert version is not None
    return version


def _staging(registry: ModelRegistry, name: str) -> ModelVersion:
    version = staging_version(registry, name)
    assert version is not None
    return version


# --------------------------------------------------------------------------- #
# Registration and versioning
# --------------------------------------------------------------------------- #


def test_first_registration_is_version_one_at_stage_none() -> None:
    _, version = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    assert version.version == 1
    assert version.stage is ModelStage.NONE
    assert version.name == "alpha"
    assert version.created_at == 1.0


def test_successive_registrations_increment_version() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, second = register_model(registry, "alpha", object(), timestamp=2.0)
    registry, third = register_model(registry, "alpha", object(), timestamp=3.0)
    assert (second.version, third.version) == (2, 3)
    assert tuple(v.version for v in list_versions(registry, "alpha")) == (1, 2, 3)


def test_distinct_names_version_independently() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=2.0)
    registry, beta_first = register_model(registry, "beta", object(), timestamp=3.0)
    assert beta_first.version == 1
    assert set(model_names(registry)) == {"alpha", "beta"}


def test_registration_stores_metadata() -> None:
    _, version = register_model(
        ModelRegistry(),
        "alpha",
        object(),
        timestamp=1.0,
        metrics={"rmse": 0.25},
        parameters={"l2_penalty": 1.0, "solver": "normal_equation"},
        tags={"universe": "sp500"},
        run_id="run-123",
    )
    assert version.metrics == {"rmse": 0.25}
    assert version.parameters == {"l2_penalty": 1.0, "solver": "normal_equation"}
    assert version.tags == {"universe": "sp500"}
    assert version.run_id == "run-123"


def test_registration_rejects_blank_name() -> None:
    with pytest.raises(ModelRegistryInputError):
        register_model(ModelRegistry(), "   ", object(), timestamp=1.0)


def test_list_versions_rejects_unknown_name() -> None:
    with pytest.raises(ModelRegistryInputError):
        list_versions(ModelRegistry(), "missing")


def test_get_version_rejects_unknown_version() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    with pytest.raises(ModelRegistryInputError):
        get_version(registry, "alpha", 99)


def test_latest_version_returns_highest_number() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=2.0)
    assert latest_version(registry, "alpha").version == 2


# --------------------------------------------------------------------------- #
# get_model: typed retrieval
# --------------------------------------------------------------------------- #


def test_get_model_returns_stored_object_when_type_matches() -> None:
    model = LinearRegressionModel(feature_names=("x",), coefficients=(2.0,), intercept=1.0)
    registry, _ = register_model(ModelRegistry(), "alpha", model, timestamp=1.0)
    assert get_model(registry, "alpha", 1, LinearRegressionModel) is model


def test_get_model_rejects_type_mismatch() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    with pytest.raises(ModelRegistryInputError):
        get_model(registry, "alpha", 1, LinearRegressionModel)


# --------------------------------------------------------------------------- #
# Promotion
# --------------------------------------------------------------------------- #


def test_promote_moves_version_to_target_stage() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.STAGING, timestamp=2.0)
    assert get_version(registry, "alpha", 1).stage is ModelStage.STAGING


def test_promote_records_the_transition() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.PRODUCTION, timestamp=2.0)
    history = promotion_history(registry, "alpha")
    assert len(history) == 1
    assert history[0].from_stage is ModelStage.NONE
    assert history[0].to_stage is ModelStage.PRODUCTION
    assert history[0].timestamp == 2.0


def test_promote_rejects_unknown_version() -> None:
    with pytest.raises(ModelRegistryInputError):
        promote(ModelRegistry(), "alpha", 1, ModelStage.STAGING, timestamp=1.0)


def test_promote_to_none_is_rejected() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    with pytest.raises(ModelRegistryInputError):
        promote(registry, "alpha", 1, ModelStage.NONE, timestamp=2.0)


def test_promote_to_current_stage_is_rejected() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.STAGING, timestamp=2.0)
    with pytest.raises(ModelRegistryInputError):
        promote(registry, "alpha", 1, ModelStage.STAGING, timestamp=3.0)


def test_promoting_second_version_to_production_archives_the_first() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=2.0)
    registry = promote(registry, "alpha", 1, ModelStage.PRODUCTION, timestamp=3.0)
    registry = promote(registry, "alpha", 2, ModelStage.PRODUCTION, timestamp=4.0)

    assert get_version(registry, "alpha", 1).stage is ModelStage.ARCHIVED
    assert get_version(registry, "alpha", 2).stage is ModelStage.PRODUCTION
    assert _prod(registry, "alpha").version == 2

    # The automatic archival is itself recorded.
    archival = [
        r
        for r in promotion_history(registry, "alpha")
        if r.version == 1 and r.to_stage is ModelStage.ARCHIVED
    ]
    assert len(archival) == 1
    assert archival[0].timestamp == 4.0


def test_production_and_staging_version_readers() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=2.0)
    assert production_version(registry, "alpha") is None
    assert staging_version(registry, "alpha") is None

    registry = promote(registry, "alpha", 1, ModelStage.STAGING, timestamp=3.0)
    registry = promote(registry, "alpha", 2, ModelStage.PRODUCTION, timestamp=4.0)
    assert _staging(registry, "alpha").version == 1
    assert _prod(registry, "alpha").version == 2


def test_production_version_is_none_for_unknown_model() -> None:
    assert production_version(ModelRegistry(), "missing") is None


def test_versions_in_stage_filters() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=2.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=3.0)
    registry = promote(registry, "alpha", 1, ModelStage.ARCHIVED, timestamp=4.0)
    registry = promote(registry, "alpha", 2, ModelStage.ARCHIVED, timestamp=5.0)
    archived = versions_in_stage(registry, "alpha", ModelStage.ARCHIVED)
    assert tuple(v.version for v in archived) == (1, 2)
    assert versions_in_stage(registry, "alpha", ModelStage.NONE)[0].version == 3


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def _registry_with_two_production_promotions() -> ModelRegistry:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=2.0)
    registry = promote(registry, "alpha", 1, ModelStage.PRODUCTION, timestamp=3.0)
    registry = promote(registry, "alpha", 2, ModelStage.PRODUCTION, timestamp=4.0)
    return registry


def test_previous_production_version_identifies_the_prior_pointer() -> None:
    registry = _registry_with_two_production_promotions()
    assert previous_production_version(registry, "alpha") == 1


def test_rollback_restores_previous_production_and_archives_current() -> None:
    registry = _registry_with_two_production_promotions()
    registry = rollback(registry, "alpha", timestamp=5.0)
    assert get_version(registry, "alpha", 2).stage is ModelStage.ARCHIVED
    assert get_version(registry, "alpha", 1).stage is ModelStage.PRODUCTION
    assert _prod(registry, "alpha").version == 1


def test_rollback_appends_transitions_to_history() -> None:
    registry = _registry_with_two_production_promotions()
    before = len(promotion_history(registry, "alpha"))
    registry = rollback(registry, "alpha", timestamp=5.0)
    after = promotion_history(registry, "alpha")
    assert len(after) == before + 2
    assert after[-1].version == 1
    assert after[-1].to_stage is ModelStage.PRODUCTION


def test_rollback_without_production_version_is_rejected() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    with pytest.raises(ModelRegistryInputError):
        rollback(registry, "alpha", timestamp=2.0)


def test_rollback_without_a_prior_production_version_is_rejected() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.PRODUCTION, timestamp=2.0)
    with pytest.raises(ModelRegistryInputError):
        rollback(registry, "alpha", timestamp=3.0)


def test_rollback_then_forward_again_chains_correctly() -> None:
    registry = _registry_with_two_production_promotions()
    registry = rollback(registry, "alpha", timestamp=5.0)  # back to v1
    registry = promote(registry, "alpha", 2, ModelStage.PRODUCTION, timestamp=6.0)  # forward to v2
    assert _prod(registry, "alpha").version == 2
    assert get_version(registry, "alpha", 1).stage is ModelStage.ARCHIVED
    assert previous_production_version(registry, "alpha") == 1


# --------------------------------------------------------------------------- #
# Deployment metadata
# --------------------------------------------------------------------------- #


def _deployment(ts: float) -> DeploymentMetadata:
    return DeploymentMetadata(environment="paper", deployed_at=ts, deployed_by="ci", notes="")


def test_set_deployment_metadata_on_production_version() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.PRODUCTION, timestamp=2.0)
    registry = set_deployment_metadata(registry, "alpha", 1, _deployment(3.0))
    meta = deployment_metadata(registry, "alpha", 1)
    assert meta is not None
    assert meta.environment == "paper"


def test_set_deployment_metadata_on_staging_version() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.STAGING, timestamp=2.0)
    registry = set_deployment_metadata(registry, "alpha", 1, _deployment(3.0))
    assert deployment_metadata(registry, "alpha", 1) is not None


def test_set_deployment_metadata_rejects_unstaged_version() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    with pytest.raises(ModelRegistryInputError):
        set_deployment_metadata(registry, "alpha", 1, _deployment(2.0))


def test_set_deployment_metadata_rejects_archived_version() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.ARCHIVED, timestamp=2.0)
    with pytest.raises(ModelRegistryInputError):
        set_deployment_metadata(registry, "alpha", 1, _deployment(3.0))


def test_set_deployment_metadata_replaces_previous() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry = promote(registry, "alpha", 1, ModelStage.PRODUCTION, timestamp=2.0)
    registry = set_deployment_metadata(registry, "alpha", 1, _deployment(3.0))
    registry = set_deployment_metadata(
        registry,
        "alpha",
        1,
        DeploymentMetadata(environment="live", deployed_at=4.0, deployed_by="ops", notes="cutover"),
    )
    meta = deployment_metadata(registry, "alpha", 1)
    assert meta is not None
    assert meta.environment == "live"


def test_deployed_versions_lists_only_versions_with_metadata() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    registry, _ = register_model(registry, "alpha", object(), timestamp=2.0)
    registry = promote(registry, "alpha", 1, ModelStage.STAGING, timestamp=3.0)
    registry = promote(registry, "alpha", 2, ModelStage.PRODUCTION, timestamp=4.0)
    registry = set_deployment_metadata(registry, "alpha", 2, _deployment(5.0))
    deployed = deployed_versions(registry, "alpha")
    assert tuple(v.version for v in deployed) == (2,)


# --------------------------------------------------------------------------- #
# Immutability and functional purity
# --------------------------------------------------------------------------- #


def test_model_version_is_frozen() -> None:
    _, version = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    with pytest.raises(FrozenInstanceError):
        version.stage = ModelStage.PRODUCTION  # type: ignore[misc]


def test_promote_does_not_mutate_input_registry() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), timestamp=1.0)
    promote(registry, "alpha", 1, ModelStage.PRODUCTION, timestamp=2.0)
    assert get_version(registry, "alpha", 1).stage is ModelStage.NONE
    assert registry.promotions == ()


def test_register_does_not_mutate_input_registry() -> None:
    original = ModelRegistry()
    register_model(original, "alpha", object(), timestamp=1.0)
    assert original.versions == {}


# --------------------------------------------------------------------------- #
# Integration: a real trained model, linked to a real experiment run
# --------------------------------------------------------------------------- #


def test_end_to_end_with_ml_model_and_experiment_run() -> None:
    # Train a real model on y = 3x + 2.
    xs = ((0.0,), (1.0,), (2.0,), (3.0,), (4.0,))
    ys = (2.0, 5.0, 8.0, 11.0, 14.0)
    model = train_linear_regression(("x",), xs, ys)

    # Record the run that produced it.
    tracker = ExperimentTracker()
    tracker, run_id = start_run(
        tracker, "linreg-fit", parameters={"l2_penalty": 0.0}, timestamp=1.0
    )
    tracker = complete_run(tracker, run_id, timestamp=2.0)

    # Register, promote, deploy.
    registry, version = register_model(
        ModelRegistry(),
        "trend-model",
        model,
        timestamp=3.0,
        metrics={"rmse": 0.0},
        parameters={"l2_penalty": 0.0},
        run_id=run_id,
    )
    registry = promote(
        registry, "trend-model", version.version, ModelStage.PRODUCTION, timestamp=4.0
    )
    registry = set_deployment_metadata(
        registry,
        "trend-model",
        version.version,
        DeploymentMetadata(environment="paper", deployed_at=5.0, deployed_by="ci"),
    )

    prod = production_version(registry, "trend-model")
    assert prod is not None
    assert prod.run_id == run_id

    recovered = get_model(registry, "trend-model", prod.version, LinearRegressionModel)
    assert predict_linear(recovered, ((10.0,),))[0] == pytest.approx(32.0)
