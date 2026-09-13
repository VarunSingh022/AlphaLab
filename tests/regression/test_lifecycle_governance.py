"""Who may change what is live, who did, and who approved it.

ADR-0018 was written in v2.7, marked *Proposed — deferred*, and recorded the
seam "so that the next release starts from a decision rather than from a
rediscovery". v2.16 implements it. What it closed, in the ADR's own words:

> So the lifecycle's audit trail answers *what* changed and *when*, and is
> silent on *who*.

and, about ``alphalab.enterprise``:

> Thirty-two tests, and no production consumer.

These tests hold each decision the ADR recorded, including the two it made by
*rejecting* alternatives -- because those are the ones a later release is most
likely to undo without noticing.
"""

from __future__ import annotations

import re
from dataclasses import fields, replace

import pytest

from alphalab.enterprise.exceptions import EnterpriseInputError, EnterprisePermissionError
from alphalab.enterprise.identity import register_principal
from alphalab.enterprise.models import EnterpriseState
from alphalab.enterprise.rbac import define_role, grant_role
from alphalab.experiment_tracking import complete_run, log_metrics, start_run
from alphalab.lifecycle import (
    LIFECYCLE_PERMISSIONS,
    PERMISSION_APPROVE,
    PERMISSION_DEPLOY,
    PERMISSION_PROMOTE,
    PERMISSION_RETIRE,
    PERMISSION_ROLLBACK,
    ApprovalRecord,
    Governance,
    LifecycleInputError,
    LifecycleState,
    LifecycleTransitionError,
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    approvals_of,
    approve_deployment,
    build_evidence,
    deploy_strategy_version,
    governance_log,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    retire_strategy_version,
    rollback_environment,
)
from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA, capture, from_primitives, restore
from alphalab.model_registry import ModelStage, promote
from alphalab.persistence import deserialize, serialize
from alphalab.studio.strategy import StrategyDefinition

POLICY = ValidationPolicy("prod-v1", (MetricThreshold("sharpe_ratio", 1.0),))
GOOD = {"sharpe_ratio": 1.4, "max_drawdown": 0.1}


# --------------------------------------------------------------------------- #
# Principals
# --------------------------------------------------------------------------- #


def _enterprise() -> EnterpriseState:
    """Four principals, each with exactly the permissions their role needs."""

    state = EnterpriseState()
    for actor, display in (
        ("releaser", "Release Engineer"),
        ("approver", "Head of Trading"),
        ("researcher", "Researcher"),
        ("auditor", "Auditor"),
    ):
        state, _ = register_principal(state, actor, display, 0.0)

    state = define_role(state, "release", LIFECYCLE_PERMISSIONS - {PERMISSION_APPROVE})
    state = define_role(state, "approve", {PERMISSION_APPROVE})
    state = define_role(state, "research", {PERMISSION_PROMOTE})
    state = define_role(state, "read", {"lifecycle.read"})

    state = grant_role(state, "releaser", "release")
    state = grant_role(state, "approver", "approve")
    state = grant_role(state, "researcher", "research")
    return grant_role(state, "auditor", "read")


ENTERPRISE = _enterprise()
RELEASER = Governance(ENTERPRISE, "releaser")
APPROVER = Governance(ENTERPRISE, "approver")
RESEARCHER = Governance(ENTERPRISE, "researcher")
AUDITOR = Governance(ENTERPRISE, "auditor")


# --------------------------------------------------------------------------- #
# A lifecycle up to a staged version
# --------------------------------------------------------------------------- #


def _staged(environment: str = "paper") -> tuple[LifecycleState, str, int]:
    state = LifecycleState()
    tracker, run_id = start_run(state.experiments, "sweep", {"fast": 5.0}, 1.0)
    tracker = complete_run(log_metrics(tracker, run_id, {"sharpe": 1.4}), run_id, 2.0)
    state = replace(state, experiments=tracker)

    state, model = register_model_version(
        state, "momentum", object(), 3.0, run_id=run_id, metrics={"sharpe": 1.4}
    )
    state = replace(
        state, models=promote(state.models, model.name, model.version, ModelStage.STAGING, 4.0)
    )
    state, ref = register_strategy(
        state,
        "ma-crossover",
        StrategyDefinition("ma-001", "MA", "1", "quant", "desc", {"fast": 5.0}),
        5.0,
        model=model,
        run_id=run_id,
    )
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(ref), "ds-1", GOOD, 6.0, seed=7, source_id="rep-1"
    )
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, RELEASER, ref.name, ref.version, POLICY, evidence.evidence_id, 7.0
    )
    return state, ref.name, ref.version


# --------------------------------------------------------------------------- #
# 1 -- the gate is a gate
# --------------------------------------------------------------------------- #


def test_an_unpermitted_principal_cannot_promote() -> None:
    state = LifecycleState()

    with pytest.raises(EnterprisePermissionError, match=r"lifecycle\.promote"):
        promote_strategy_version(state, AUDITOR, "anything", 1, POLICY, "ev", 7.0)


@pytest.mark.parametrize(
    ("call", "permission"),
    [
        (
            lambda state, gov: promote_strategy_version(state, gov, "x", 1, POLICY, "ev", 7.0),
            PERMISSION_PROMOTE,
        ),
        (
            lambda state, gov: deploy_strategy_version(state, gov, "x", 1, "paper", 8.0),
            PERMISSION_DEPLOY,
        ),
        (lambda state, gov: rollback_environment(state, gov, "paper", 9.0), PERMISSION_ROLLBACK),
        (lambda state, gov: retire_strategy_version(state, gov, "x", 1, 9.0), PERMISSION_RETIRE),
        (
            lambda state, gov: approve_deployment(state, gov, "x", 1, "paper", 8.0),
            PERMISSION_APPROVE,
        ),
    ],
)
def test_every_governed_entry_point_checks_its_own_permission(call, permission) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(EnterprisePermissionError, match=re.escape(permission)):
        call(LifecycleState(), AUDITOR)


def test_the_permission_is_checked_before_anything_else() -> None:
    """An unpermitted caller learns nothing about the version, and nothing is written.

    ``deploy_strategy_version``'s contract has always been that "nothing is
    registered before any of these refusals". The gate joins that contract
    rather than sitting outside it.
    """

    state, name, version = _staged()

    with pytest.raises(EnterprisePermissionError):
        deploy_strategy_version(state, RESEARCHER, name, version, "paper", 8.0)

    # Not a LifecycleInputError about an unknown strategy -- the refusal came
    # first -- and the state is untouched.
    assert state.deployments.deployments.to_tuple() == ()
    assert len(state.strategies.promotions) == 1


def test_a_permitted_principal_lacking_the_specific_permission_is_still_refused() -> None:
    """``researcher`` may promote and may not deploy. Roles are not a ladder."""

    state, name, version = _staged()

    with pytest.raises(EnterprisePermissionError, match=r"lifecycle\.deploy"):
        deploy_strategy_version(state, RESEARCHER, name, version, "paper", 8.0)


def test_an_unknown_principal_is_refused() -> None:
    ghost = Governance(ENTERPRISE, "nobody")

    with pytest.raises(EnterpriseInputError, match="nobody"):
        promote_strategy_version(LifecycleState(), ghost, "x", 1, POLICY, "ev", 7.0)


def test_an_anonymous_actor_cannot_be_constructed() -> None:
    """A governed act is performed by someone."""

    with pytest.raises(LifecycleInputError, match="actor_id cannot be empty"):
        Governance(ENTERPRISE, "   ")


# --------------------------------------------------------------------------- #
# 2 -- the actor reaches the records
# --------------------------------------------------------------------------- #


def test_a_promotion_records_who_promoted_it() -> None:
    state, _name, _version = _staged()

    promotion = state.strategies.promotions[-1]
    assert promotion.actor_id == "releaser"
    assert promotion.to_stage is ModelStage.STAGING


def test_a_deployment_records_its_actor_on_the_ledger_not_only_the_model_note() -> None:
    """ADR-0018: a note on the model version is not the deployment record."""

    state, name, version = _staged()
    state, _ref = deploy_strategy_version(state, RELEASER, name, version, "paper", 8.0)

    record = state.deployments.deployments[-1]
    assert record.actor_id == "releaser"
    assert record.environment == "paper"


def test_a_rollback_records_its_actor() -> None:
    state, name, version = _staged()
    state, _ = deploy_strategy_version(state, RELEASER, name, version, "paper", 8.0)

    state, ref2 = register_strategy(
        state,
        "ma-crossover",
        StrategyDefinition("ma-002", "MA", "2", "quant", "desc", {"fast": 8.0}),
        9.0,
    )
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(ref2), "ds-1", GOOD, 9.5, seed=7, source_id="rep-2"
    )
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, RELEASER, ref2.name, ref2.version, POLICY, evidence.evidence_id, 10.0
    )
    state, _ = deploy_strategy_version(state, RELEASER, ref2.name, ref2.version, "paper", 11.0)

    state, _ = rollback_environment(state, RELEASER, "paper", 12.0)

    assert state.deployments.deployments[-1].actor_id == "releaser"
    assert state.deployments.deployments[-1].is_rollback


def test_a_move_no_principal_requested_records_no_actor() -> None:
    """An incumbent archived because it was displaced was not archived *by* anyone.

    Attributing it to the deployer would say someone archived that version, when
    what happened is that the ledger did. The empty actor is the honest answer,
    which is why the field is a reference with an empty value rather than a
    required one.
    """

    state, name, version = _staged()
    state, _ = deploy_strategy_version(state, RELEASER, name, version, "paper", 8.0)

    state, ref2 = register_strategy(
        state,
        "ma-crossover",
        StrategyDefinition("ma-002", "MA", "2", "quant", "desc", {"fast": 8.0}),
        9.0,
    )
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(ref2), "ds-1", GOOD, 9.5, seed=7, source_id="rep-2"
    )
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, RELEASER, ref2.name, ref2.version, POLICY, evidence.evidence_id, 10.0
    )
    state, _ = deploy_strategy_version(state, RELEASER, ref2.name, ref2.version, "paper", 11.0)

    archived = [
        record
        for record in state.strategies.promotions
        if record.to_stage is ModelStage.ARCHIVED and record.version == version
    ]
    assert archived, "the displaced incumbent was not archived"
    assert archived[-1].actor_id == "", "an automatic move was attributed to a principal"


# --------------------------------------------------------------------------- #
# 3 -- approval, and separation of duties
# --------------------------------------------------------------------------- #


def _gated(environment: str = "live") -> Governance:
    return Governance(ENTERPRISE, "releaser", frozenset({environment}))


def test_a_gated_environment_refuses_a_deployment_with_no_approval() -> None:
    state, name, version = _staged()

    with pytest.raises(LifecycleTransitionError, match="requires a recorded approval"):
        deploy_strategy_version(state, _gated(), name, version, "live", 8.0)

    assert state.deployments.deployments.to_tuple() == (), "nothing was written"


def test_an_approval_by_the_deployer_does_not_satisfy_separation_of_duties() -> None:
    state, name, version = _staged()
    self_approver = Governance(ENTERPRISE, "releaser", frozenset({"live"}))
    approving = Governance(
        grant_role(ENTERPRISE, "releaser", "approve"), "releaser", frozenset({"live"})
    )
    state = approve_deployment(state, approving, name, version, "live", 7.5)

    with pytest.raises(LifecycleTransitionError, match="other than the deployer"):
        deploy_strategy_version(state, self_approver, name, version, "live", 8.0)


def test_an_independent_approval_lets_the_deployment_through() -> None:
    state, name, version = _staged()
    state = approve_deployment(state, APPROVER, name, version, "live", 7.5, note="reviewed")

    state, ref = deploy_strategy_version(state, _gated(), name, version, "live", 8.0)

    assert ref.environment == "live"
    assert state.deployments.deployments[-1].actor_id == "releaser"
    assert approvals_of(state, name, version, "live")[-1].approver_id == "approver"


def test_an_approval_is_for_an_exact_version_and_environment() -> None:
    state, name, version = _staged()
    state = approve_deployment(state, APPROVER, name, version, "staging", 7.5)

    # Approved for staging says nothing about live.
    with pytest.raises(LifecycleTransitionError, match="requires a recorded approval"):
        deploy_strategy_version(state, _gated(), name, version, "live", 8.0)

    assert approvals_of(state, name, version, "live") == ()
    assert len(approvals_of(state, name, version, "staging")) == 1


def test_an_ungated_environment_needs_no_approval() -> None:
    """AlphaLab does not decide which environments a firm gates."""

    state, name, version = _staged()

    state, ref = deploy_strategy_version(state, RELEASER, name, version, "paper", 8.0)

    assert ref.environment == "paper"
    assert state.approvals.to_tuple() == ()


def test_approving_an_unknown_version_is_refused() -> None:
    with pytest.raises(LifecycleInputError):
        approve_deployment(LifecycleState(), APPROVER, "ghost", 1, "live", 7.5)


def test_an_approval_may_precede_promotion() -> None:
    """Firms approve a release before it is staged; refusing that invents an order."""

    state = LifecycleState()
    state, ref = register_strategy(
        state, "ma-crossover", StrategyDefinition("ma-001", "MA", "1", "q", "d", {}), 5.0
    )

    state = approve_deployment(state, APPROVER, ref.name, ref.version, "live", 5.5)

    assert len(approvals_of(state, ref.name, ref.version, "live")) == 1


def test_a_rollback_needs_no_approval_and_that_is_deliberate() -> None:
    """A governance control that makes an incident worse is not one.

    A rollback returns an environment to a version that already ran there, and
    therefore already passed whatever gate was in force. Requiring a fresh
    approval would mean a firm whose approver is unreachable cannot take a bad
    release down.
    """

    state, name, version = _staged()
    state = approve_deployment(state, APPROVER, name, version, "live", 7.5)
    state, _ = deploy_strategy_version(state, _gated(), name, version, "live", 8.0)

    state, ref2 = register_strategy(
        state,
        "ma-crossover",
        StrategyDefinition("ma-002", "MA", "2", "q", "d", {"fast": 8.0}),
        9.0,
    )
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(ref2), "ds-1", GOOD, 9.5, seed=7, source_id="rep-2"
    )
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, RELEASER, ref2.name, ref2.version, POLICY, evidence.evidence_id, 10.0
    )
    state = approve_deployment(state, APPROVER, ref2.name, ref2.version, "live", 10.5)
    state, _ = deploy_strategy_version(state, _gated(), ref2.name, ref2.version, "live", 11.0)

    # No approval recorded for the rollback, and it still succeeds.
    state, _ = rollback_environment(state, _gated(), "live", 12.0)

    assert state.deployments.deployments[-1].is_rollback


# --------------------------------------------------------------------------- #
# 4 -- the audit trail
# --------------------------------------------------------------------------- #


def test_the_governance_log_answers_who_promoted_and_who_deployed() -> None:
    """The exact question ADR-0018 recorded as unanswerable."""

    state, name, version = _staged()
    state = approve_deployment(state, APPROVER, name, version, "live", 7.5)
    state, _ = deploy_strategy_version(state, _gated(), name, version, "live", 8.0)

    log = governance_log(state)
    by_action = {act.action: act for act in log}

    assert by_action["promotion"].actor_id == "releaser"
    assert by_action["approval"].actor_id == "approver"
    assert by_action["deployment"].actor_id == "releaser"
    assert [act.timestamp for act in log] == sorted(act.timestamp for act in log)
    assert all(act.attributed for act in log)


def test_the_governance_log_keeps_no_records_of_its_own() -> None:
    """A projection over three append-only logs, not a fourth one."""

    state, name, version = _staged()
    state, _ = deploy_strategy_version(state, RELEASER, name, version, "paper", 8.0)

    expected = (
        len(state.strategies.promotions) + len(state.deployments.deployments) + len(state.approvals)
    )
    assert len(governance_log(state)) == expected


def test_an_unattributed_act_says_so_rather_than_guessing() -> None:
    state, name, version = _staged()
    state, _ = deploy_strategy_version(state, RELEASER, name, version, "paper", 8.0)

    unattributed = [act for act in governance_log(state) if not act.attributed]
    assert all(act.actor_id == "" for act in unattributed)


def test_the_lifecycle_writes_nothing_into_the_enterprise_audit_log() -> None:
    """Two logs, one authority each. ADR-0018's audit-semantics decision."""

    state, name, version = _staged()
    before = len(ENTERPRISE.audit_log)
    state, _ = deploy_strategy_version(state, RELEASER, name, version, "paper", 8.0)

    assert len(ENTERPRISE.audit_log) == before == 0
    # And no governance entry point returns an EnterpriseState.
    import inspect

    for function in (
        promote_strategy_version,
        deploy_strategy_version,
        rollback_environment,
        retire_strategy_version,
        approve_deployment,
    ):
        annotation = str(inspect.signature(function).return_annotation)
        assert "EnterpriseState" not in annotation


# --------------------------------------------------------------------------- #
# 5 -- ADR-0018's rejected alternatives stay rejected
# --------------------------------------------------------------------------- #


def test_lifecycle_state_holds_no_enterprise_state() -> None:
    """Option (a), rejected: it would put enterprise data in the snapshot."""

    names = {f.name for f in fields(LifecycleState)}
    assert "enterprise" not in names
    assert "principals" not in names
    assert "roles" not in names

    from alphalab.lifecycle.snapshot import LifecycleSnapshot

    assert "enterprise" not in {f.name for f in fields(LifecycleSnapshot)}


def test_the_dependency_is_visible_in_the_signature() -> None:
    """Option (b), taken: an explicit parameter, and a required one.

    An optional ``governance=None`` that skipped the check when omitted would be
    option (c) -- "a gate anyone can bypass by calling the function directly" --
    wearing option (b)'s clothes.
    """

    import inspect

    for function in (
        promote_strategy_version,
        deploy_strategy_version,
        rollback_environment,
        retire_strategy_version,
        approve_deployment,
    ):
        parameter = inspect.signature(function).parameters["governance"]
        assert parameter.default is inspect.Parameter.empty, (
            f"{function.__name__} makes governance optional, which is a bypass"
        )
        assert list(inspect.signature(function).parameters)[:2] == ["state", "governance"]


def test_deployed_by_is_still_the_model_notes_own_field() -> None:
    """It is not the governance actor and never was."""

    import inspect

    parameters = inspect.signature(deploy_strategy_version).parameters
    assert "deployed_by" in parameters
    assert parameters["deployed_by"].default == ""

    state, name, version = _staged()
    state, _ = deploy_strategy_version(
        state, RELEASER, name, version, "paper", 8.0, deployed_by="ci-pipeline"
    )

    assert state.deployments.deployments[-1].actor_id == "releaser"
    note = state.models.versions["momentum"][1].deployment
    assert note is not None and note.deployed_by == "ci-pipeline"


# --------------------------------------------------------------------------- #
# 6 -- persistence
# --------------------------------------------------------------------------- #


def test_the_actor_and_the_approvals_survive_a_round_trip() -> None:
    state, name, version = _staged()
    state = approve_deployment(state, APPROVER, name, version, "live", 7.5, note="reviewed")
    state, _ = deploy_strategy_version(state, _gated(), name, version, "live", 8.0)

    snapshot = capture(state)
    decoded = from_primitives(deserialize(serialize(snapshot)))
    restored = restore(decoded, {"momentum@1": state.models.versions["momentum"][1].model})

    assert restored.strategies.promotions[0].actor_id == "releaser"
    assert restored.deployments.deployments[-1].actor_id == "releaser"
    assert restored.approvals.to_tuple() == state.approvals.to_tuple()
    assert restored.approvals[0].note == "reviewed"
    assert governance_log(restored) == governance_log(state)


def test_the_schema_moved_once_and_says_what_for() -> None:
    assert LIFECYCLE_SNAPSHOT_SCHEMA == 2

    snapshot = capture(LifecycleState())
    assert snapshot.schema_version == 2
    assert "approvals" in {f.name for f in fields(snapshot)}


def test_an_approval_record_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    record = ApprovalRecord("x", 1, "live", "approver", 1.0)
    with pytest.raises(FrozenInstanceError):
        record.approver_id = "someone-else"  # type: ignore[misc]
