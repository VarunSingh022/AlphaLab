"""Strategy versions, the promotion gate, deployment and rollback.

Every test drives the real registries: a real ``ExperimentTracker``, a real
``ModelRegistry``, a real ``DeploymentManager``. Nothing here stubs a stage of
the lifecycle, because the thing under test is what happens *between* them.
"""

from dataclasses import replace

import pytest

from alphalab.experiment_tracking import complete_run, fail_run, log_metrics, start_run
from alphalab.lifecycle import (
    COMPONENT_EVIDENCE,
    COMPONENT_MODEL,
    COMPONENT_RUN,
    COMPONENT_STRATEGY,
    LifecycleInputError,
    LifecycleState,
    LifecycleTransitionError,
    MetricThreshold,
    ModelRef,
    StrategyVersionRef,
    ValidationMethod,
    ValidationPolicy,
    active_model_version,
    active_strategy_version,
    build_evidence,
    deploy_strategy_version,
    environments_running,
    evidence_for,
    get_strategy_version,
    latest_strategy_version,
    list_strategy_versions,
    live_environments,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    release_manifest,
    retire_strategy_version,
    rollback_environment,
    strategy_names,
    validate_strategy_version,
)
from alphalab.model_registry import ModelStage, deployment_metadata, promote
from alphalab.studio.strategy import StrategyDefinition

POLICY = ValidationPolicy(
    "prod-v1",
    (
        MetricThreshold("sharpe_ratio", minimum=1.0),
        MetricThreshold("max_drawdown", maximum=0.25),
    ),
)
GOOD = {"sharpe_ratio": 1.4, "max_drawdown": 0.12}
BAD = {"sharpe_ratio": 0.2, "max_drawdown": 0.60}


def _definition(strategy_id: str, version: str, fast: float = 5.0) -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id=strategy_id,
        name="MA crossover",
        version=version,
        author="quant",
        description="Moving-average crossover",
        parameters={"fast": fast, "slow": 20.0},
    )


def _completed_run(state: LifecycleState, name: str = "ma-sweep") -> tuple[LifecycleState, str]:
    tracker, run_id = start_run(state.experiments, name, {"fast": 5, "slow": 20}, 1.0)
    tracker = log_metrics(tracker, run_id, {"sharpe": 1.4})
    tracker = complete_run(tracker, run_id, 2.0)
    return replace(state, experiments=tracker), run_id


def _staged_model(state: LifecycleState, run_id: str) -> tuple[LifecycleState, ModelRef]:
    state, ref = register_model_version(
        state, "momentum", object(), 3.0, run_id=run_id, metrics={"sharpe": 1.4}
    )
    return replace(
        state, models=promote(state.models, ref.name, ref.version, ModelStage.STAGING, 4.0)
    ), ref


def _registered(
    state: LifecycleState | None = None, fast: float = 5.0
) -> tuple[LifecycleState, StrategyVersionRef, ModelRef, str]:
    """A strategy version at NONE, over a staged model and a completed run."""

    state, run_id = _completed_run(state or LifecycleState())
    state, model = _staged_model(state, run_id)
    version = len(state.strategies.versions.get("ma-crossover", ())) + 1
    state, ref = register_strategy(
        state,
        "ma-crossover",
        _definition(f"ma-{version:03d}", str(version), fast),
        5.0,
        model=model,
        run_id=run_id,
    )
    return state, ref, model, run_id


def _with_evidence(
    state: LifecycleState, ref: StrategyVersionRef, metrics: dict[str, float] = GOOD
) -> tuple[LifecycleState, str]:
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(ref), "ds-1", metrics, 6.0, seed=7, source_id="rep-1"
    )
    return record_evidence(state, evidence), evidence.evidence_id


def _staged(
    state: LifecycleState | None = None, fast: float = 5.0
) -> tuple[LifecycleState, StrategyVersionRef]:
    """A strategy version promoted to STAGING on passing evidence."""

    state, ref, _, _ = _registered(state, fast)
    state, evidence_id = _with_evidence(state, ref)
    return promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 7.0), ref


def _deployed(environment: str = "paper") -> tuple[LifecycleState, StrategyVersionRef]:
    state, ref = _staged()
    state, _ = deploy_strategy_version(state, ref.name, ref.version, environment, 8.0)
    return state, ref


def _stage(state: LifecycleState, ref: StrategyVersionRef) -> ModelStage:
    return get_strategy_version(state.strategies, ref.name, ref.version).stage


# --------------------------------------------------------------------------- #
# Registration and identity
# --------------------------------------------------------------------------- #


def test_a_new_strategy_version_starts_at_none() -> None:
    """Registering something is not a claim that it works."""
    state, ref, _, _ = _registered()
    assert ref == StrategyVersionRef("ma-crossover", 1)
    assert _stage(state, ref) is ModelStage.NONE


def test_successive_registrations_increment_the_version() -> None:
    state, first, _, _ = _registered()
    state, second, _, _ = _registered(state, fast=8.0)

    assert (first.version, second.version) == (1, 2)
    assert len(list_strategy_versions(state.strategies, "ma-crossover")) == 2
    assert latest_strategy_version(state.strategies, "ma-crossover").version == 2
    assert strategy_names(state.strategies) == ("ma-crossover",)


def test_a_version_carries_the_canonical_studio_definition() -> None:
    state, ref, _, _ = _registered()
    version = get_strategy_version(state.strategies, ref.name, ref.version)

    assert isinstance(version.definition, StrategyDefinition)
    assert version.definition.parameters == {"fast": 5.0, "slow": 20.0}


def test_a_version_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    state, ref, _, _ = _registered()
    version = get_strategy_version(state.strategies, ref.name, ref.version)
    with pytest.raises(FrozenInstanceError):
        version.stage = ModelStage.PRODUCTION  # type: ignore[misc]


def test_registering_does_not_mutate_the_input_state() -> None:
    state, _, model, run_id = _registered()
    before = len(list_strategy_versions(state.strategies, "ma-crossover"))
    register_strategy(state, "ma-crossover", _definition("x", "9"), 9.0, model=model, run_id=run_id)
    assert len(list_strategy_versions(state.strategies, "ma-crossover")) == before


def test_unknown_strategies_and_versions_are_rejected() -> None:
    state, _, _, _ = _registered()
    with pytest.raises(LifecycleInputError):
        get_strategy_version(state.strategies, "missing", 1)
    with pytest.raises(LifecycleInputError):
        get_strategy_version(state.strategies, "ma-crossover", 99)


# --------------------------------------------------------------------------- #
# Checked references -- the thing neither package could do alone
# --------------------------------------------------------------------------- #


def test_a_model_version_cannot_cite_a_run_that_does_not_exist() -> None:
    with pytest.raises(LifecycleInputError, match="No experiment run"):
        register_model_version(LifecycleState(), "momentum", object(), 1.0, run_id="nope")


def test_a_version_cannot_cite_a_run_that_has_not_finished() -> None:
    tracker, run_id = start_run(LifecycleState().experiments, "m", {}, 1.0)
    state = replace(LifecycleState(), experiments=tracker)
    with pytest.raises(LifecycleInputError, match="not COMPLETED"):
        register_model_version(state, "momentum", object(), 2.0, run_id=run_id)


def test_a_version_cannot_cite_a_failed_run() -> None:
    tracker, run_id = start_run(LifecycleState().experiments, "m", {}, 1.0)
    tracker = fail_run(tracker, run_id, 2.0)
    state = replace(LifecycleState(), experiments=tracker)
    with pytest.raises(LifecycleInputError, match="FAILED"):
        register_model_version(state, "momentum", object(), 3.0, run_id=run_id)


def test_a_strategy_version_cannot_cite_a_model_that_does_not_exist() -> None:
    from alphalab.model_registry import ModelRegistryInputError

    state, run_id = _completed_run(LifecycleState())
    with pytest.raises(ModelRegistryInputError):
        register_strategy(
            state, "s", _definition("x", "1"), 1.0, model=ModelRef("ghost", 1), run_id=run_id
        )


def test_a_strategy_name_containing_the_reference_separator_is_refused() -> None:
    with pytest.raises(LifecycleInputError, match="cannot contain"):
        register_strategy(LifecycleState(), "ma@crossover", _definition("x", "1"), 1.0)


# --------------------------------------------------------------------------- #
# Evidence handling
# --------------------------------------------------------------------------- #


def test_recording_the_same_evidence_twice_is_a_no_op() -> None:
    state, ref, _, _ = _registered()
    once, evidence_id = _with_evidence(state, ref)
    twice, _ = _with_evidence(once, ref)

    assert len(once.evidence) == 1
    assert len(twice.evidence) == 1
    assert twice.evidence[evidence_id] == once.evidence[evidence_id]


def test_reusing_an_id_for_different_content_is_refused() -> None:
    """Ids are digests; a clash is a hand-built id, and silently replacing one
    measurement with another is exactly what must not happen."""
    state, ref, _, _ = _registered()
    state, evidence_id = _with_evidence(state, ref)
    impostor = replace(state.evidence[evidence_id], metrics={"sharpe_ratio": 99.0})

    with pytest.raises(LifecycleInputError, match="already held by different content"):
        record_evidence(state, impostor)


def test_evidence_about_another_version_is_rejected() -> None:
    """A copy-pasted evidence id would otherwise defeat the whole gate."""
    state, first, _, _ = _registered()
    state, second, _, _ = _registered(state, fast=8.0)
    state, evidence_id = _with_evidence(state, first)

    with pytest.raises(LifecycleInputError, match="is about"):
        validate_strategy_version(state, second.name, second.version, POLICY, evidence_id)


def test_validating_against_unknown_evidence_is_rejected() -> None:
    state, ref, _, _ = _registered()
    with pytest.raises(LifecycleInputError, match="No evidence recorded"):
        validate_strategy_version(state, ref.name, ref.version, POLICY, "nope")


def test_validating_changes_nothing() -> None:
    state, ref, _, _ = _registered()
    state, evidence_id = _with_evidence(state, ref)
    validate_strategy_version(state, ref.name, ref.version, POLICY, evidence_id)
    assert _stage(state, ref) is ModelStage.NONE


def test_a_promoted_version_records_what_it_passed() -> None:
    state, ref = _staged()
    version = get_strategy_version(state.strategies, ref.name, ref.version)

    assert version.policy_id == "prod-v1"
    assert version.evidence_id is not None
    recorded = evidence_for(state, ref.name, ref.version)
    assert recorded is not None
    assert recorded.metrics == GOOD


# --------------------------------------------------------------------------- #
# The promotion gate
# --------------------------------------------------------------------------- #


def test_passing_evidence_promotes_to_staging() -> None:
    state, ref = _staged()
    assert _stage(state, ref) is ModelStage.STAGING


def test_failing_evidence_refuses_the_promotion_and_names_every_failure() -> None:
    state, ref, _, _ = _registered()
    state, evidence_id = _with_evidence(state, ref, BAD)

    with pytest.raises(LifecycleTransitionError) as error:
        promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 7.0)

    message = str(error.value)
    assert "sharpe_ratio" in message
    assert "max_drawdown" in message


def test_a_refused_promotion_leaves_the_state_untouched() -> None:
    state, ref, _, _ = _registered()
    state, evidence_id = _with_evidence(state, ref, BAD)
    with pytest.raises(LifecycleTransitionError):
        promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 7.0)

    assert _stage(state, ref) is ModelStage.NONE
    assert len(state.strategies.promotions) == 0


def test_a_strategy_cannot_be_staged_on_an_unstaged_model() -> None:
    """A strategy cannot be more validated than the model inside it."""
    state, run_id = _completed_run(LifecycleState())
    state, model = register_model_version(state, "momentum", object(), 3.0, run_id=run_id)
    state, ref = register_strategy(
        state, "ma-crossover", _definition("ma-001", "1"), 5.0, model=model, run_id=run_id
    )
    state, evidence_id = _with_evidence(state, ref)

    with pytest.raises(LifecycleTransitionError, match="which is in stage NONE"):
        promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 7.0)


def test_a_strategy_with_no_model_needs_only_its_own_evidence() -> None:
    state, run_id = _completed_run(LifecycleState())
    state, ref = register_strategy(
        state, "rules-only", _definition("r-001", "1"), 5.0, run_id=run_id
    )
    state, evidence_id = _with_evidence(state, ref)
    state = promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 7.0)

    assert _stage(state, ref) is ModelStage.STAGING


def test_promoting_an_already_staged_version_is_refused() -> None:
    state, ref = _staged()
    evidence_id = get_strategy_version(state.strategies, ref.name, ref.version).evidence_id
    assert evidence_id is not None

    with pytest.raises(LifecycleTransitionError, match="already in stage STAGING"):
        promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 9.0)


def test_promoting_a_deployed_version_back_to_staging_is_refused() -> None:
    state, ref = _deployed()
    evidence_id = get_strategy_version(state.strategies, ref.name, ref.version).evidence_id
    assert evidence_id is not None

    with pytest.raises(LifecycleTransitionError, match="can only move to ARCHIVED"):
        promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 9.0)


# --------------------------------------------------------------------------- #
# Deployment
# --------------------------------------------------------------------------- #


def test_deploying_makes_a_version_the_active_one_and_moves_it_to_production() -> None:
    state, ref = _deployed()
    active = active_strategy_version(state, "paper")

    assert active is not None
    assert active.ref == ref
    assert _stage(state, ref) is ModelStage.PRODUCTION
    assert live_environments(state) == ("paper",)


def test_a_version_that_was_never_promoted_cannot_be_deployed() -> None:
    state, ref, _, _ = _registered()
    with pytest.raises(LifecycleTransitionError, match="Promote it on passing evidence first"):
        deploy_strategy_version(state, ref.name, ref.version, "paper", 8.0)


def test_deploying_the_version_already_running_there_is_refused() -> None:
    state, ref = _deployed()
    with pytest.raises(LifecycleTransitionError, match="already active in 'paper'"):
        deploy_strategy_version(state, ref.name, ref.version, "paper", 9.0)


def test_a_refused_redeploy_registers_no_release() -> None:
    """Nothing is written before the refusal."""
    state, ref = _deployed()
    releases_before = len(state.deployments.releases[ref.name])
    ledger_before = len(state.deployments.deployments)

    with pytest.raises(LifecycleTransitionError):
        deploy_strategy_version(state, ref.name, ref.version, "paper", 9.0)

    assert len(state.deployments.releases[ref.name]) == releases_before
    assert len(state.deployments.deployments) == ledger_before


def test_a_blank_environment_is_refused() -> None:
    state, ref = _staged()
    with pytest.raises(LifecycleInputError):
        deploy_strategy_version(state, ref.name, ref.version, "   ", 8.0)


def test_the_release_manifest_carries_typed_references_not_loose_strings() -> None:
    state, ref = _staged()
    version = get_strategy_version(state.strategies, ref.name, ref.version)
    components, config = release_manifest(version)

    assert components[COMPONENT_STRATEGY] == "ma-crossover@1"
    assert components[COMPONENT_MODEL] == "momentum@1"
    assert components[COMPONENT_RUN] == version.run_id
    assert components[COMPONENT_EVIDENCE] == version.evidence_id
    assert config == {"fast": "5.0", "slow": "20.0"}


def test_a_manifest_omits_a_reference_it_does_not_have() -> None:
    """Absent, not empty: a manifest never claims a reference that isn't there."""
    state, run_id = _completed_run(LifecycleState())
    state, ref = register_strategy(state, "rules-only", _definition("r", "1"), 5.0, run_id=run_id)
    components, _ = release_manifest(get_strategy_version(state.strategies, ref.name, ref.version))

    assert COMPONENT_MODEL not in components
    assert COMPONENT_EVIDENCE not in components


def test_the_deployed_release_is_verifiable_and_names_the_model() -> None:
    from alphalab.deployment_manager import active_release, verify_checksum

    state, _ = _deployed()
    release = active_release(state.deployments, "paper")

    assert release is not None
    assert verify_checksum(release)
    assert active_model_version(state, "paper") == ModelRef("momentum", 1)


def test_deploying_records_the_deployment_on_the_model_version() -> None:
    """The note is derived from the deployment that happened, not asserted."""
    state, ref = _staged()
    state, _ = deploy_strategy_version(state, ref.name, ref.version, "paper", 8.0, deployed_by="ci")
    note = deployment_metadata(state.models, "momentum", 1)

    assert note is not None
    assert (note.environment, note.deployed_at, note.deployed_by) == ("paper", 8.0, "ci")


def test_deploying_a_replacement_archives_the_version_it_displaced() -> None:
    state, first = _deployed()
    state, second = _staged(state, fast=8.0)
    state, _ = deploy_strategy_version(state, second.name, second.version, "paper", 11.0)

    assert _stage(state, second) is ModelStage.PRODUCTION
    assert _stage(state, first) is ModelStage.ARCHIVED


def test_one_version_deployed_to_two_environments_stays_in_production() -> None:
    state, ref = _deployed("paper")
    state, _ = deploy_strategy_version(state, ref.name, ref.version, "live-eu", 9.0)

    assert environments_running(state, ref.name, ref.version) == ("paper", "live-eu")
    assert _stage(state, ref) is ModelStage.PRODUCTION


def test_a_version_still_running_elsewhere_is_not_archived_when_replaced() -> None:
    state, first = _deployed("paper")
    state, _ = deploy_strategy_version(state, first.name, first.version, "live-eu", 9.0)
    state, second = _staged(state, fast=8.0)
    state, _ = deploy_strategy_version(state, second.name, second.version, "paper", 11.0)

    assert _stage(state, first) is ModelStage.PRODUCTION
    assert environments_running(state, first.name, first.version) == ("live-eu",)


def test_one_release_stands_for_one_strategy_version_however_many_environments() -> None:
    state, ref = _deployed("paper")
    state, _ = deploy_strategy_version(state, ref.name, ref.version, "live-eu", 9.0)

    assert len(state.deployments.releases[ref.name]) == 1
    assert len(state.deployments.deployments) == 2


def test_deploying_does_not_mutate_the_input_state() -> None:
    state, ref = _staged()
    deploy_strategy_version(state, ref.name, ref.version, "paper", 8.0)

    assert active_strategy_version(state, "paper") is None
    assert _stage(state, ref) is ModelStage.STAGING


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def _two_deployments() -> tuple[LifecycleState, StrategyVersionRef, StrategyVersionRef]:
    state, first = _deployed()
    state, second = _staged(state, fast=8.0)
    state, _ = deploy_strategy_version(state, second.name, second.version, "paper", 11.0)
    return state, first, second


def test_rollback_restores_the_previous_version_and_archives_the_current_one() -> None:
    state, first, second = _two_deployments()
    state, restored = rollback_environment(state, "paper", 12.0)

    assert restored.release_version == 1
    active = active_strategy_version(state, "paper")
    assert active is not None and active.ref == first
    assert _stage(state, first) is ModelStage.PRODUCTION
    assert _stage(state, second) is ModelStage.ARCHIVED


def test_rollback_is_recorded_as_a_rollback_in_the_ledger() -> None:
    from alphalab.deployment_manager import deployment_history

    state, _, _ = _two_deployments()
    state, _ = rollback_environment(state, "paper", 12.0)
    history = deployment_history(state.deployments, "paper")

    assert [record.is_rollback for record in history] == [False, False, True]
    assert history[-1].replaced_version == 2


def test_rollback_is_deterministic() -> None:
    """The ledger is append-only, so the answer does not depend on when it is asked."""
    state, _, _ = _two_deployments()
    once, first_ref = rollback_environment(state, "paper", 12.0)
    twice, second_ref = rollback_environment(state, "paper", 12.0)

    assert first_ref == second_ref
    assert active_strategy_version(once, "paper") == active_strategy_version(twice, "paper")


def test_rolling_back_an_environment_that_was_never_deployed_to_is_refused() -> None:
    state, _ = _staged()
    with pytest.raises(LifecycleInputError, match="no previous deployment"):
        rollback_environment(state, "paper", 12.0)


def test_rolling_back_a_single_deployment_is_refused() -> None:
    """There is no previously valid version to return to."""
    state, _ = _deployed()
    with pytest.raises(LifecycleInputError, match="no previous deployment"):
        rollback_environment(state, "paper", 12.0)


def test_rolling_back_twice_walks_the_ledger_back_again() -> None:
    state, first, second = _two_deployments()
    state, _ = rollback_environment(state, "paper", 12.0)
    state, _ = rollback_environment(state, "paper", 13.0)

    active = active_strategy_version(state, "paper")
    assert active is not None and active.ref == second
    assert _stage(state, first) is ModelStage.ARCHIVED


def test_rollback_updates_the_model_deployment_note() -> None:
    state, _, _ = _two_deployments()
    state, _ = rollback_environment(state, "paper", 12.0, deployed_by="oncall")
    note = deployment_metadata(state.models, "momentum", 1)

    assert note is not None
    assert (note.deployed_at, note.deployed_by) == (12.0, "oncall")


def test_rollback_does_not_mutate_the_input_state() -> None:
    state, _, second = _two_deployments()
    rollback_environment(state, "paper", 12.0)

    active = active_strategy_version(state, "paper")
    assert active is not None and active.ref == second


# --------------------------------------------------------------------------- #
# Retirement
# --------------------------------------------------------------------------- #


def test_a_staged_version_can_be_retired() -> None:
    state, ref = _staged()
    state = retire_strategy_version(state, ref.name, ref.version, 9.0)
    assert _stage(state, ref) is ModelStage.ARCHIVED


def test_a_live_version_cannot_be_retired_by_a_stage_edit() -> None:
    state, ref = _deployed()
    with pytest.raises(LifecycleTransitionError, match="still active in paper"):
        retire_strategy_version(state, ref.name, ref.version, 9.0)


def test_retiring_an_archived_version_again_is_refused() -> None:
    state, ref = _staged()
    state = retire_strategy_version(state, ref.name, ref.version, 9.0)
    with pytest.raises(LifecycleTransitionError, match="already in stage ARCHIVED"):
        retire_strategy_version(state, ref.name, ref.version, 10.0)


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_every_stage_change_is_recorded_with_its_reason() -> None:
    state, _, _ = _two_deployments()
    state, _ = rollback_environment(state, "paper", 12.0)
    moves = [
        (record.version, record.from_stage.name, record.to_stage.name)
        for record in state.strategies.promotions
    ]

    assert moves == [
        (1, "NONE", "STAGING"),
        (1, "STAGING", "PRODUCTION"),
        (2, "NONE", "STAGING"),
        (2, "STAGING", "PRODUCTION"),
        (1, "PRODUCTION", "ARCHIVED"),
        (1, "ARCHIVED", "PRODUCTION"),
        (2, "PRODUCTION", "ARCHIVED"),
    ]
    assert all(record.reason for record in state.strategies.promotions)


# --------------------------------------------------------------------------- #
# A model archived after the strategy was promoted
# --------------------------------------------------------------------------- #


def test_deploying_a_strategy_whose_model_was_archived_is_refused() -> None:
    """Promotion established the model was staged. It can be archived after,
    and before the strategy is deployed -- which would put an artifact with
    nothing standing behind it into an environment."""
    state, ref = _staged()
    state = replace(state, models=promote(state.models, "momentum", 1, ModelStage.ARCHIVED, 8.0))

    with pytest.raises(LifecycleTransitionError, match="which is now in stage ARCHIVED"):
        deploy_strategy_version(state, ref.name, ref.version, "paper", 9.0)


def test_that_refusal_writes_nothing() -> None:
    state, ref = _staged()
    state = replace(state, models=promote(state.models, "momentum", 1, ModelStage.ARCHIVED, 8.0))
    with pytest.raises(LifecycleTransitionError):
        deploy_strategy_version(state, ref.name, ref.version, "paper", 9.0)

    assert len(state.deployments.deployments) == 0
    assert "ma-crossover" not in state.deployments.releases
    assert _stage(state, ref) is ModelStage.STAGING


def test_rolling_back_to_a_version_whose_model_was_archived_is_refused() -> None:
    """The same rule on the way back: a rollback must restore something still
    fit to run, not merely something that ran once."""
    state, first, _ = _two_deployments()
    # Each staged strategy version got its own model version; v1 runs momentum@1.
    assert get_strategy_version(state.strategies, first.name, first.version).model == ModelRef(
        "momentum", 1
    )
    state = replace(state, models=promote(state.models, "momentum", 1, ModelStage.ARCHIVED, 13.0))

    with pytest.raises(LifecycleTransitionError, match="which is now in stage ARCHIVED"):
        rollback_environment(state, "paper", 14.0)
    assert _stage(state, first) is ModelStage.ARCHIVED


def test_a_strategy_with_no_model_is_unaffected_by_that_rule() -> None:
    state, run_id = _completed_run(LifecycleState())
    state, ref = register_strategy(
        state, "rules-only", _definition("r-001", "1"), 5.0, run_id=run_id
    )
    state, evidence_id = _with_evidence(state, ref)
    state = promote_strategy_version(state, ref.name, ref.version, POLICY, evidence_id, 7.0)
    state, _ = deploy_strategy_version(state, ref.name, ref.version, "paper", 8.0)

    assert _stage(state, ref) is ModelStage.PRODUCTION
