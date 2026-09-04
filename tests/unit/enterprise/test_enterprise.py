"""Comprehensive tests for the Enterprise layer: identity/session lifecycle,
RBAC, audit logging, workspaces, secret references, compliance reporting, the
explicit no-credential / no-secret-value boundaries, and functional purity."""

import inspect
from dataclasses import FrozenInstanceError, fields

import pytest

from alphalab.enterprise import (
    EnterpriseInputError,
    EnterprisePermissionError,
    EnterpriseState,
    Principal,
    SecretRef,
    Workspace,
    add_member,
    audit_trail,
    close_session,
    compliance_report,
    create_workspace,
    define_role,
    get_principal,
    grant_role,
    has_permission,
    list_secret_refs,
    mark_rotated,
    open_session,
    orphaned_workspaces,
    permissions_for,
    privileged_principals,
    record_audit,
    register_principal,
    register_secret_ref,
    remove_member,
    require_permission,
    resolve_session,
    revoke_role,
    secrets_due_for_rotation,
    workspace_members,
    workspaces_for,
)

DAY = 86_400.0


def _state_with_principals(*ids: str) -> EnterpriseState:
    state = EnterpriseState()
    for principal_id in ids:
        state, _ = register_principal(state, principal_id, principal_id.title(), timestamp=0.0)
    return state


# --------------------------------------------------------------------------- #
# Identity and session lifecycle
# --------------------------------------------------------------------------- #


def test_register_principal() -> None:
    state, principal = register_principal(EnterpriseState(), "alice", "Alice", timestamp=1.0)
    assert principal.principal_id == "alice"
    assert principal.roles == frozenset()
    assert get_principal(state, "alice") == principal


def test_register_principal_rejects_blank_and_duplicate() -> None:
    state = _state_with_principals("alice")
    with pytest.raises(EnterpriseInputError):
        register_principal(state, "  ", "x", timestamp=1.0)
    with pytest.raises(EnterpriseInputError):
        register_principal(state, "alice", "Alice Again", timestamp=1.0)


def test_get_principal_rejects_unknown() -> None:
    with pytest.raises(EnterpriseInputError):
        get_principal(EnterpriseState(), "nobody")


def test_open_and_resolve_session() -> None:
    state = _state_with_principals("alice")
    state, session = open_session(state, "alice", timestamp=100.0, ttl_seconds=60.0)
    assert session.expires_at == 160.0
    assert resolve_session(state, session.session_id, now=159.0).principal_id == "alice"


def test_open_session_rejects_unknown_principal_and_bad_ttl() -> None:
    state = _state_with_principals("alice")
    with pytest.raises(EnterpriseInputError):
        open_session(state, "bob", timestamp=1.0, ttl_seconds=60.0)
    with pytest.raises(EnterpriseInputError):
        open_session(state, "alice", timestamp=1.0, ttl_seconds=0.0)


def test_resolve_session_rejects_expired() -> None:
    state = _state_with_principals("alice")
    state, session = open_session(state, "alice", timestamp=100.0, ttl_seconds=60.0)
    with pytest.raises(EnterprisePermissionError):
        resolve_session(state, session.session_id, now=160.0)


def test_resolve_session_rejects_unknown() -> None:
    with pytest.raises(EnterpriseInputError):
        resolve_session(EnterpriseState(), "no-such-session", now=1.0)


def test_close_session() -> None:
    state = _state_with_principals("alice")
    state, session = open_session(state, "alice", timestamp=1.0, ttl_seconds=60.0)
    state = close_session(state, session.session_id)
    with pytest.raises(EnterpriseInputError):
        resolve_session(state, session.session_id, now=2.0)
    with pytest.raises(EnterpriseInputError):
        close_session(state, session.session_id)


def test_authentication_api_accepts_no_credential_material() -> None:
    """The safety boundary, asserted: nothing in the identity surface takes a
    password/secret/token/key argument."""
    for func in (register_principal, open_session, resolve_session):
        params = set(inspect.signature(func).parameters)
        assert not params & {"password", "secret", "token", "key", "credential"}


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #


def test_define_grant_and_check_permissions() -> None:
    state = _state_with_principals("alice")
    state = define_role(state, "researcher", ["research.run", "research.read"])
    state = grant_role(state, "alice", "researcher")
    assert get_principal(state, "alice").roles == frozenset({"researcher"})
    assert permissions_for(state, "alice") == frozenset({"research.run", "research.read"})
    assert has_permission(state, "alice", "research.run")
    assert not has_permission(state, "alice", "admin.delete")


def test_define_role_rejects_bad_input() -> None:
    state = EnterpriseState()
    with pytest.raises(EnterpriseInputError):
        define_role(state, "  ", ["p"])
    with pytest.raises(EnterpriseInputError):
        define_role(state, "r", [])
    with pytest.raises(EnterpriseInputError):
        define_role(state, "r", ["ok", "  "])


def test_grant_role_rejects_unknown_principal_or_role() -> None:
    state = _state_with_principals("alice")
    with pytest.raises(EnterpriseInputError):
        grant_role(state, "alice", "undefined-role")
    state = define_role(state, "r", ["p"])
    with pytest.raises(EnterpriseInputError):
        grant_role(state, "bob", "r")


def test_revoke_role() -> None:
    state = _state_with_principals("alice")
    state = define_role(state, "r", ["p"])
    state = grant_role(state, "alice", "r")
    state = revoke_role(state, "alice", "r")
    assert get_principal(state, "alice").roles == frozenset()
    with pytest.raises(EnterpriseInputError):
        revoke_role(state, "alice", "r")


def test_wildcard_permission_grants_everything() -> None:
    state = _state_with_principals("root")
    state = define_role(state, "admin", ["*"])
    state = grant_role(state, "root", "admin")
    assert has_permission(state, "root", "anything.at.all")
    require_permission(state, "root", "some.rare.permission")  # does not raise


def test_require_permission_raises_when_missing() -> None:
    state = _state_with_principals("alice")
    with pytest.raises(EnterprisePermissionError):
        require_permission(state, "alice", "admin.delete")


# --------------------------------------------------------------------------- #
# Audit logging
# --------------------------------------------------------------------------- #


def test_record_audit_appends_and_filters() -> None:
    state = EnterpriseState()
    state, first = record_audit(state, "alice", "role.grant", "bob", timestamp=1.0)
    state, _ = record_audit(state, "alice", "workspace.create", "ws1", timestamp=2.0)
    state, _ = record_audit(state, "bob", "role.grant", "carol", timestamp=3.0)

    assert audit_trail(state) == state.audit_log
    assert len(audit_trail(state)) == 3
    assert [e.event_id for e in audit_trail(state, actor_id="alice")] == [
        first.event_id,
        state.audit_log[1].event_id,
    ]
    assert len(audit_trail(state, action="role.grant")) == 2
    assert len(audit_trail(state, actor_id="alice", action="role.grant")) == 1


def test_record_audit_rejects_blank_fields() -> None:
    state = EnterpriseState()
    with pytest.raises(EnterpriseInputError):
        record_audit(state, " ", "a", "t", timestamp=1.0)
    with pytest.raises(EnterpriseInputError):
        record_audit(state, "actor", " ", "t", timestamp=1.0)
    with pytest.raises(EnterpriseInputError):
        record_audit(state, "actor", "a", " ", timestamp=1.0)


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #


def test_create_workspace_and_membership() -> None:
    state = _state_with_principals("alice", "bob", "carol")
    state, workspace = create_workspace(state, "ws1", "Research", "alice", timestamp=1.0)
    assert workspace.owner_id == "alice"
    assert workspace_members(state, "ws1") == frozenset({"alice"})

    state = add_member(state, "ws1", "bob")
    state = add_member(state, "ws1", "carol")
    assert workspace_members(state, "ws1") == frozenset({"alice", "bob", "carol"})

    state = remove_member(state, "ws1", "bob")
    assert workspace_members(state, "ws1") == frozenset({"alice", "carol"})


def test_create_workspace_rejects_bad_input() -> None:
    state = _state_with_principals("alice")
    with pytest.raises(EnterpriseInputError):
        create_workspace(state, " ", "n", "alice", timestamp=1.0)
    with pytest.raises(EnterpriseInputError):
        create_workspace(state, "ws1", " ", "alice", timestamp=1.0)
    with pytest.raises(EnterpriseInputError):
        create_workspace(state, "ws1", "n", "ghost", timestamp=1.0)
    state, _ = create_workspace(state, "ws1", "n", "alice", timestamp=1.0)
    with pytest.raises(EnterpriseInputError):
        create_workspace(state, "ws1", "n2", "alice", timestamp=1.0)


def test_membership_rejections() -> None:
    state = _state_with_principals("alice", "bob")
    state, _ = create_workspace(state, "ws1", "n", "alice", timestamp=1.0)
    with pytest.raises(EnterpriseInputError):
        add_member(state, "ws1", "alice")  # owner already a member
    with pytest.raises(EnterpriseInputError):
        add_member(state, "ws1", "ghost")  # unknown principal
    with pytest.raises(EnterpriseInputError):
        add_member(state, "ghost-ws", "bob")  # unknown workspace
    state = add_member(state, "ws1", "bob")
    with pytest.raises(EnterpriseInputError):
        add_member(state, "ws1", "bob")  # already a member
    with pytest.raises(EnterpriseInputError):
        remove_member(state, "ws1", "alice")  # cannot remove owner
    state = remove_member(state, "ws1", "bob")
    with pytest.raises(EnterpriseInputError):
        remove_member(state, "ws1", "bob")  # not a member


def test_workspaces_for_lists_owned_and_joined_sorted() -> None:
    state = _state_with_principals("alice", "bob")
    state, _ = create_workspace(state, "ws-b", "B", "alice", timestamp=1.0)
    state, _ = create_workspace(state, "ws-a", "A", "bob", timestamp=1.0)
    state = add_member(state, "ws-a", "alice")
    owned_and_joined = workspaces_for(state, "alice")
    assert [w.workspace_id for w in owned_and_joined] == ["ws-a", "ws-b"]


# --------------------------------------------------------------------------- #
# Secret references -- no values, ever
# --------------------------------------------------------------------------- #


def test_secret_ref_type_holds_no_value() -> None:
    field_names = {f.name for f in fields(SecretRef)}
    assert not field_names & {"value", "secret", "material", "plaintext"}
    params = set(inspect.signature(register_secret_ref).parameters)
    assert not params & {"value", "secret", "material", "plaintext"}


def test_register_and_rotate_secret_ref() -> None:
    state = EnterpriseState()
    state, ref = register_secret_ref(
        state, "broker_api", "vault", "kv/data/broker", rotation_period_days=30.0, timestamp=0.0
    )
    assert ref.provider == "vault"
    assert ref.last_rotated_at == 0.0

    # Not yet due after 10 days; due after 30.
    assert secrets_due_for_rotation(state, now=10 * DAY) == ()
    assert [r.name for r in secrets_due_for_rotation(state, now=30 * DAY)] == ["broker_api"]

    state = mark_rotated(state, "broker_api", timestamp=30 * DAY)
    assert secrets_due_for_rotation(state, now=45 * DAY) == ()


def test_secret_ref_rejections() -> None:
    state = EnterpriseState()
    with pytest.raises(EnterpriseInputError):
        register_secret_ref(state, " ", "vault", "x", rotation_period_days=1.0, timestamp=0.0)
    with pytest.raises(EnterpriseInputError):
        register_secret_ref(state, "n", " ", "x", rotation_period_days=1.0, timestamp=0.0)
    with pytest.raises(EnterpriseInputError):
        register_secret_ref(state, "n", "vault", " ", rotation_period_days=1.0, timestamp=0.0)
    with pytest.raises(EnterpriseInputError):
        register_secret_ref(state, "n", "vault", "x", rotation_period_days=0.0, timestamp=0.0)
    state, _ = register_secret_ref(
        state, "n", "vault", "x", rotation_period_days=1.0, timestamp=0.0
    )
    with pytest.raises(EnterpriseInputError):
        register_secret_ref(state, "n", "vault", "y", rotation_period_days=1.0, timestamp=0.0)
    with pytest.raises(EnterpriseInputError):
        mark_rotated(state, "missing", timestamp=1.0)


def test_list_secret_refs_sorted() -> None:
    state = EnterpriseState()
    state, _ = register_secret_ref(
        state, "zeta", "vault", "z", rotation_period_days=1.0, timestamp=0.0
    )
    state, _ = register_secret_ref(
        state, "alpha", "vault", "a", rotation_period_days=1.0, timestamp=0.0
    )
    assert [r.name for r in list_secret_refs(state)] == ["alpha", "zeta"]


# --------------------------------------------------------------------------- #
# Compliance
# --------------------------------------------------------------------------- #


def test_compliance_report_snapshot() -> None:
    state = _state_with_principals("root", "alice")
    state = define_role(state, "admin", ["*"])
    state = grant_role(state, "root", "admin")
    state, _ = open_session(state, "alice", timestamp=0.0, ttl_seconds=100.0)
    state, _ = register_secret_ref(
        state, "old", "vault", "x", rotation_period_days=1.0, timestamp=0.0
    )

    report = compliance_report(state, now=50.0)
    assert report.principal_count == 2
    assert report.privileged_principal_ids == ("root",)
    assert report.orphaned_workspace_ids == ()
    assert report.stale_secret_names == ()  # only 50s elapsed, well under the 1-day period
    assert report.open_session_count == 1


def test_compliance_flags_stale_secret_after_period() -> None:
    state = EnterpriseState()
    state, _ = register_secret_ref(
        state, "old", "vault", "x", rotation_period_days=1.0, timestamp=0.0
    )
    fresh = compliance_report(state, now=0.5 * DAY)
    stale = compliance_report(state, now=2 * DAY)
    assert fresh.stale_secret_names == ()
    assert stale.stale_secret_names == ("old",)


def test_orphaned_workspace_detection() -> None:
    # A workspace whose owner was never (or is no longer) a principal.
    state = EnterpriseState(
        workspaces={"ws1": Workspace(workspace_id="ws1", name="n", owner_id="ghost")}
    )
    assert orphaned_workspaces(state) == ("ws1",)
    assert compliance_report(state, now=1.0).orphaned_workspace_ids == ("ws1",)


def test_privileged_principals_helper() -> None:
    state = _state_with_principals("root", "alice")
    state = define_role(state, "admin", ["*"])
    state = define_role(state, "reader", ["read"])
    state = grant_role(state, "root", "admin")
    state = grant_role(state, "alice", "reader")
    assert privileged_principals(state) == ("root",)
    assert privileged_principals(state, "read") == ("alice",)


# --------------------------------------------------------------------------- #
# Immutability and functional purity
# --------------------------------------------------------------------------- #


def test_state_and_models_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        EnterpriseState().audit_log = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        Principal(principal_id="x", display_name="X").roles = frozenset()  # type: ignore[misc]


def test_operations_do_not_mutate_input_state() -> None:
    base = _state_with_principals("alice")
    base = define_role(base, "r", ["p"])

    grant_role(base, "alice", "r")
    assert get_principal(base, "alice").roles == frozenset()

    record_audit(base, "alice", "x", "y", timestamp=1.0)
    assert base.audit_log == ()


# --------------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------------- #


def test_end_to_end_governance_flow() -> None:
    state = EnterpriseState()
    state, _ = register_principal(state, "root", "Root", timestamp=0.0)
    state, _ = register_principal(state, "quant", "Quant", timestamp=0.0)

    state = define_role(state, "admin", ["*"])
    state = define_role(state, "researcher", ["research.run", "workspace.join"])
    state = grant_role(state, "root", "admin")
    state = grant_role(state, "quant", "researcher")

    state, session = open_session(state, "quant", timestamp=1_000.0, ttl_seconds=3_600.0)
    assert resolve_session(state, session.session_id, now=1_500.0).principal_id == "quant"
    require_permission(state, "quant", "research.run")

    state, _ = create_workspace(state, "ws-alpha", "Alpha", "root", timestamp=1_000.0)
    state = add_member(state, "ws-alpha", "quant")
    assert "quant" in workspace_members(state, "ws-alpha")
    assert [w.workspace_id for w in workspaces_for(state, "quant")] == ["ws-alpha"]

    state, _ = register_secret_ref(
        state, "broker_api", "aws-sm", "prod/broker", rotation_period_days=90.0, timestamp=1_000.0
    )

    for action, target in (
        ("role.grant", "quant"),
        ("workspace.create", "ws-alpha"),
        ("workspace.add_member", "quant"),
        ("secret.register", "broker_api"),
    ):
        state, _ = record_audit(state, "root", action, target, timestamp=1_000.0)

    report = compliance_report(state, now=1_500.0)
    assert report.principal_count == 2
    assert report.privileged_principal_ids == ("root",)
    assert report.open_session_count == 1
    assert report.orphaned_workspace_ids == ()
    assert len(audit_trail(state, actor_id="root")) == 4
