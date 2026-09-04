"""Deterministic governance/compliance reporting over ``EnterpriseState``."""

from alphalab.enterprise.models import PRIVILEGED_WILDCARD, ComplianceReport, EnterpriseState
from alphalab.enterprise.rbac import permissions_for
from alphalab.enterprise.secrets import secrets_due_for_rotation


def privileged_principals(
    state: EnterpriseState, permission: str = PRIVILEGED_WILDCARD
) -> tuple[str, ...]:
    """Returns ids of principals whose roles grant ``permission``, sorted."""
    return tuple(
        sorted(
            principal_id
            for principal_id in state.principals
            if permission in permissions_for(state, principal_id)
        )
    )


def orphaned_workspaces(state: EnterpriseState) -> tuple[str, ...]:
    """Returns ids of workspaces whose owner is no longer a principal, sorted."""
    return tuple(
        sorted(
            workspace_id
            for workspace_id, workspace in state.workspaces.items()
            if workspace.owner_id not in state.principals
        )
    )


def compliance_report(
    state: EnterpriseState, now: float, privileged_permission: str = PRIVILEGED_WILDCARD
) -> ComplianceReport:
    """Builds a :class:`ComplianceReport` snapshot as of ``now``."""
    open_sessions = sum(1 for session in state.sessions.values() if now < session.expires_at)
    return ComplianceReport(
        generated_at=now,
        principal_count=len(state.principals),
        privileged_principal_ids=privileged_principals(state, privileged_permission),
        orphaned_workspace_ids=orphaned_workspaces(state),
        stale_secret_names=tuple(ref.name for ref in secrets_due_for_rotation(state, now)),
        open_session_count=open_sessions,
    )
