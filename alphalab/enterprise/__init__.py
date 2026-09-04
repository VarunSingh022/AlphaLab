"""AlphaLab Enterprise.

Authentication, RBAC, audit logging, collaboration, multi-user workspaces,
compliance, and secrets management -- as a deterministic, in-memory governance
layer threaded through a single immutable ``EnterpriseState``.

Deliberate scope boundaries:

- Authentication is *session lifecycle only* (``open_session`` /
  ``resolve_session`` / ``close_session``). No password, key, or other
  credential is ever accepted or stored. A real identity provider is expected
  in front of this layer.
- Secrets management stores *references and rotation metadata only*
  (``SecretRef`` = provider + external id + last-rotated time). Secret values
  are never accepted, stored, returned, or logged.
- Audit logging is a standalone capability; RBAC / workspace / secret
  operations do not auto-emit audit events, so those modules stay
  single-purpose. Callers record audit entries explicitly.

Capabilities:

- identity: ``register_principal``, ``open_session``, ``resolve_session``,
  ``close_session``.
- rbac: ``define_role``, ``grant_role``, ``revoke_role``, ``permissions_for``,
  ``has_permission``, ``require_permission`` (a ``"*"`` permission grants all).
- audit: ``record_audit``, ``audit_trail``.
- workspaces: ``create_workspace``, ``add_member``, ``remove_member``,
  ``workspace_members``, ``workspaces_for``.
- secrets: ``register_secret_ref``, ``mark_rotated``, ``list_secret_refs``,
  ``secrets_due_for_rotation``.
- compliance: ``compliance_report`` plus ``privileged_principals`` /
  ``orphaned_workspaces``.
"""

from alphalab.enterprise.audit import audit_trail, record_audit
from alphalab.enterprise.compliance import (
    compliance_report,
    orphaned_workspaces,
    privileged_principals,
)
from alphalab.enterprise.exceptions import (
    EnterpriseError,
    EnterpriseInputError,
    EnterprisePermissionError,
)
from alphalab.enterprise.identity import (
    close_session,
    get_principal,
    open_session,
    register_principal,
    resolve_session,
)
from alphalab.enterprise.models import (
    PRIVILEGED_WILDCARD,
    AuditEvent,
    ComplianceReport,
    EnterpriseState,
    Principal,
    SecretRef,
    Session,
    Workspace,
)
from alphalab.enterprise.rbac import (
    define_role,
    grant_role,
    has_permission,
    permissions_for,
    require_permission,
    revoke_role,
)
from alphalab.enterprise.secrets import (
    list_secret_refs,
    mark_rotated,
    register_secret_ref,
    secrets_due_for_rotation,
)
from alphalab.enterprise.workspaces import (
    add_member,
    create_workspace,
    get_workspace,
    remove_member,
    workspace_members,
    workspaces_for,
)

__all__ = [
    "PRIVILEGED_WILDCARD",
    "AuditEvent",
    "ComplianceReport",
    "EnterpriseError",
    "EnterpriseInputError",
    "EnterprisePermissionError",
    "EnterpriseState",
    "Principal",
    "SecretRef",
    "Session",
    "Workspace",
    "add_member",
    "audit_trail",
    "close_session",
    "compliance_report",
    "create_workspace",
    "define_role",
    "get_principal",
    "get_workspace",
    "grant_role",
    "has_permission",
    "list_secret_refs",
    "mark_rotated",
    "open_session",
    "orphaned_workspaces",
    "permissions_for",
    "privileged_principals",
    "record_audit",
    "register_principal",
    "register_secret_ref",
    "remove_member",
    "require_permission",
    "resolve_session",
    "revoke_role",
    "secrets_due_for_rotation",
    "workspace_members",
    "workspaces_for",
]
