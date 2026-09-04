"""Comprehensive tests for the Deployment Manager: checksummed packaging,
release registration/versioning, environment deployment, rollback, functional
purity, and a model-registry-referencing integration."""

from dataclasses import FrozenInstanceError, replace

import pytest

from alphalab.deployment_manager import (
    DeploymentManager,
    DeploymentManagerInputError,
    ReleasePackage,
    active_release,
    build_release,
    compute_checksum,
    deploy,
    deployed_environments,
    deployment_history,
    get_release,
    latest_release,
    list_releases,
    previous_release,
    register_release,
    release_names,
    rollback,
    verify_checksum,
)

COMPONENTS = {"strategy": "ma_crossover-004", "model": "momentum@3"}
CONFIG = {"max_gross": "1.0", "venue": "paper"}


def _manager_with_two_releases() -> DeploymentManager:
    manager, _ = register_release(DeploymentManager(), "stack", COMPONENTS, CONFIG, timestamp=1.0)
    manager, _ = register_release(
        manager, "stack", COMPONENTS, {**CONFIG, "max_gross": "2.0"}, timestamp=2.0
    )
    return manager


def _active(manager: DeploymentManager, environment: str) -> ReleasePackage:
    release = active_release(manager, environment)
    assert release is not None
    return release


# --------------------------------------------------------------------------- #
# Packaging
# --------------------------------------------------------------------------- #


def test_build_release_populates_and_checksums() -> None:
    package = build_release("stack", 1, COMPONENTS, CONFIG, timestamp=5.0)
    assert package.version == 1
    assert package.created_at == 5.0
    assert package.checksum == compute_checksum(COMPONENTS, CONFIG)
    assert verify_checksum(package)


def test_checksum_is_independent_of_mapping_order() -> None:
    reordered_components = {"model": "momentum@3", "strategy": "ma_crossover-004"}
    reordered_config = {"venue": "paper", "max_gross": "1.0"}
    assert compute_checksum(COMPONENTS, CONFIG) == compute_checksum(
        reordered_components, reordered_config
    )


def test_checksum_changes_with_content() -> None:
    assert compute_checksum(COMPONENTS, CONFIG) != compute_checksum(
        COMPONENTS, {**CONFIG, "max_gross": "9.0"}
    )


def test_verify_checksum_detects_tampering() -> None:
    package = build_release("stack", 1, COMPONENTS, CONFIG, timestamp=1.0)
    tampered = replace(package, config={**CONFIG, "venue": "live"})
    assert not verify_checksum(tampered)


def test_build_release_rejects_blank_name() -> None:
    with pytest.raises(DeploymentManagerInputError):
        build_release("  ", 1, COMPONENTS, CONFIG, timestamp=1.0)


def test_build_release_rejects_non_positive_version() -> None:
    with pytest.raises(DeploymentManagerInputError):
        build_release("stack", 0, COMPONENTS, CONFIG, timestamp=1.0)


def test_build_release_rejects_empty_components() -> None:
    with pytest.raises(DeploymentManagerInputError):
        build_release("stack", 1, {}, CONFIG, timestamp=1.0)


# --------------------------------------------------------------------------- #
# Release registration and versioning
# --------------------------------------------------------------------------- #


def test_register_release_increments_versions() -> None:
    manager = _manager_with_two_releases()
    assert tuple(r.version for r in list_releases(manager, "stack")) == (1, 2)
    assert latest_release(manager, "stack").version == 2


def test_distinct_release_names_version_independently() -> None:
    manager, _ = register_release(DeploymentManager(), "a", COMPONENTS, CONFIG, timestamp=1.0)
    manager, first_b = register_release(manager, "b", COMPONENTS, CONFIG, timestamp=2.0)
    assert first_b.version == 1
    assert set(release_names(manager)) == {"a", "b"}


def test_list_releases_rejects_unknown_name() -> None:
    with pytest.raises(DeploymentManagerInputError):
        list_releases(DeploymentManager(), "missing")


def test_get_release_rejects_unknown_version() -> None:
    manager = _manager_with_two_releases()
    with pytest.raises(DeploymentManagerInputError):
        get_release(manager, "stack", 99)


# --------------------------------------------------------------------------- #
# Deployment
# --------------------------------------------------------------------------- #


def test_deploy_sets_active_release() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "staging", timestamp=3.0)
    assert _active(manager, "staging").version == 1


def test_deploy_records_replaced_version() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "staging", timestamp=3.0)
    manager = deploy(manager, "stack", 2, "staging", timestamp=4.0)
    history = deployment_history(manager, "staging")
    assert [r.version for r in history] == [1, 2]
    assert history[0].replaced_version is None
    assert history[1].replaced_version == 1
    assert not history[1].is_rollback


def test_deploy_is_environment_scoped() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "staging", timestamp=3.0)
    manager = deploy(manager, "stack", 2, "production", timestamp=4.0)
    assert _active(manager, "staging").version == 1
    assert _active(manager, "production").version == 2
    assert set(deployed_environments(manager)) == {"staging", "production"}


def test_deploy_rejects_blank_environment() -> None:
    manager = _manager_with_two_releases()
    with pytest.raises(DeploymentManagerInputError):
        deploy(manager, "stack", 1, "  ", timestamp=3.0)


def test_deploy_rejects_unknown_release() -> None:
    with pytest.raises(DeploymentManagerInputError):
        deploy(DeploymentManager(), "stack", 1, "staging", timestamp=3.0)


def test_deploy_rejects_redeploying_the_active_release() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "staging", timestamp=3.0)
    with pytest.raises(DeploymentManagerInputError):
        deploy(manager, "stack", 1, "staging", timestamp=4.0)


def test_deploy_rejects_a_release_that_fails_checksum() -> None:
    tampered = ReleasePackage(
        name="stack",
        version=1,
        components=COMPONENTS,
        config=CONFIG,
        checksum="not-the-real-digest",
        created_at=0.0,
    )
    manager = DeploymentManager(releases={"stack": (tampered,)})
    with pytest.raises(DeploymentManagerInputError):
        deploy(manager, "stack", 1, "staging", timestamp=1.0)


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def test_rollback_restores_previous_release() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "production", timestamp=3.0)
    manager = deploy(manager, "stack", 2, "production", timestamp=4.0)
    manager = rollback(manager, "production", timestamp=5.0)
    assert _active(manager, "production").version == 1
    last = deployment_history(manager, "production")[-1]
    assert last.is_rollback
    assert last.version == 1
    assert last.replaced_version == 2


def test_previous_release_reports_the_prior_pointer() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "production", timestamp=3.0)
    manager = deploy(manager, "stack", 2, "production", timestamp=4.0)
    assert previous_release(manager, "production") == ("stack", 1)


def test_rollback_without_active_release_is_rejected() -> None:
    manager = _manager_with_two_releases()
    with pytest.raises(DeploymentManagerInputError):
        rollback(manager, "production", timestamp=5.0)


def test_rollback_without_a_previous_release_is_rejected() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "production", timestamp=3.0)
    with pytest.raises(DeploymentManagerInputError):
        rollback(manager, "production", timestamp=4.0)


def test_rollback_then_forward_again() -> None:
    manager = _manager_with_two_releases()
    manager = deploy(manager, "stack", 1, "production", timestamp=3.0)
    manager = deploy(manager, "stack", 2, "production", timestamp=4.0)
    manager = rollback(manager, "production", timestamp=5.0)  # -> v1
    manager = deploy(manager, "stack", 2, "production", timestamp=6.0)  # -> v2
    assert _active(manager, "production").version == 2
    assert previous_release(manager, "production") == ("stack", 1)


# --------------------------------------------------------------------------- #
# Immutability and functional purity
# --------------------------------------------------------------------------- #


def test_release_package_is_frozen() -> None:
    package = build_release("stack", 1, COMPONENTS, CONFIG, timestamp=1.0)
    with pytest.raises(FrozenInstanceError):
        package.version = 2  # type: ignore[misc]


def test_deploy_does_not_mutate_input_manager() -> None:
    manager = _manager_with_two_releases()
    deploy(manager, "stack", 1, "staging", timestamp=3.0)
    assert manager.deployments == ()
    assert active_release(manager, "staging") is None


# --------------------------------------------------------------------------- #
# Integration: a release referencing model-registry and assistant artifacts
# --------------------------------------------------------------------------- #


def test_release_bundles_cross_package_references_and_rolls_back() -> None:
    manager, v1 = register_release(
        DeploymentManager(),
        "eu-equity",
        components={"strategy": "ma_crossover-004", "model": "momentum@2"},
        config={"venue": "paper"},
        timestamp=1.0,
    )
    manager, v2 = register_release(
        manager,
        "eu-equity",
        components={"strategy": "ma_crossover-004", "model": "momentum@3"},
        config={"venue": "paper"},
        timestamp=2.0,
    )
    assert v1.checksum != v2.checksum  # the model reference changed

    manager = deploy(manager, "eu-equity", 2, "staging", timestamp=3.0)
    manager = deploy(manager, "eu-equity", 1, "production", timestamp=4.0)
    manager = deploy(manager, "eu-equity", 2, "production", timestamp=5.0)
    assert _active(manager, "production").components["model"] == "momentum@3"

    manager = rollback(manager, "production", timestamp=6.0)
    assert _active(manager, "production").components["model"] == "momentum@2"
    assert _active(manager, "staging").version == 2  # staging untouched by the production rollback
