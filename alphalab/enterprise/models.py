"""Every immutable type the Enterprise layer works with, in one module.

Kept together -- like ``alphalab.model_registry.registry`` -- so the identity,
RBAC, audit, workspace, compliance, and secrets modules can each depend on this
one without import cycles. ``EnterpriseState`` is the single value all of those
functions thread.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

PRIVILEGED_WILDCARD = "*"
"""A role permission of ``"*"`` grants every permission."""


@dataclass(frozen=True, slots=True)
class Principal:
    """A user or service identity.

    Attributes:
        principal_id: Stable unique identifier.
        display_name: Human-readable name.
        roles: Names of roles granted to this principal.
        created_at: Unix timestamp the principal was registered.
    """

    principal_id: str
    display_name: str
    roles: frozenset[str] = frozenset()
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class Session:
    """An issued, time-bounded session for a principal.

    This models session *lifecycle* only. It is deliberately not a credential
    store: no password, key, or other secret is accepted or held here. A real
    identity provider is expected to sit in front of ``open_session``.

    Attributes:
        session_id: Opaque non-secret session identifier.
        principal_id: The principal the session belongs to.
        opened_at: Unix timestamp the session was opened.
        expires_at: Unix timestamp at/after which the session is invalid.
    """

    session_id: str
    principal_id: str
    opened_at: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class Workspace:
    """A multi-user collaboration workspace.

    Attributes:
        workspace_id: Stable unique identifier.
        name: Human-readable name.
        owner_id: Principal id of the owner. The owner is always a member.
        member_ids: Principal ids of members, excluding the owner.
        created_at: Unix timestamp the workspace was created.
    """

    workspace_id: str
    name: str
    owner_id: str
    member_ids: frozenset[str] = frozenset()
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class SecretRef:
    """A reference to a secret held in an external provider -- never its value.

    This module records only where a secret lives and when it was last rotated.
    Secret material is never accepted as an argument, stored on this type,
    returned, or logged.

    Attributes:
        name: Logical secret name used by AlphaLab code, e.g. ``"broker_api"``.
        provider: External secret provider, e.g. ``"vault"`` or ``"aws-sm"``.
        external_id: Identifier/path within that provider. An address, not a
            value.
        rotation_period_days: How often the secret should be rotated.
        last_rotated_at: Unix timestamp of the last recorded rotation.
    """

    name: str
    provider: str
    external_id: str
    rotation_period_days: float
    last_rotated_at: float = 0.0


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One append-only audit-log entry.

    Attributes:
        event_id: Unique identifier.
        actor_id: Principal id that performed the action.
        action: Verb describing what happened, e.g. ``"role.grant"``.
        target: What the action was performed on, e.g. a principal or workspace
            id.
        timestamp: Unix timestamp of the action.
        metadata: Free-form non-secret context.
    """

    event_id: str
    actor_id: str
    action: str
    target: str
    timestamp: float
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    """A deterministic snapshot of governance posture.

    Attributes:
        generated_at: Unix timestamp the report was produced.
        principal_count: Number of registered principals.
        privileged_principal_ids: Principals holding the privileged permission,
            sorted.
        orphaned_workspace_ids: Workspaces whose owner is no longer a principal,
            sorted.
        stale_secret_names: Secret references due for rotation, sorted.
        open_session_count: Sessions not yet expired as of ``generated_at``.
    """

    generated_at: float
    principal_count: int
    privileged_principal_ids: tuple[str, ...]
    orphaned_workspace_ids: tuple[str, ...]
    stale_secret_names: tuple[str, ...]
    open_session_count: int


@dataclass(frozen=True, slots=True)
class EnterpriseState:
    """The single immutable value every Enterprise function threads.

    Attributes:
        principals: Principal id -> principal.
        roles: Role name -> the permissions it grants.
        sessions: Session id -> session.
        workspaces: Workspace id -> workspace.
        secret_refs: Secret name -> reference.
        audit_log: Every audit event, in the order recorded.
    """

    principals: Mapping[str, Principal] = field(default_factory=dict)
    roles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    sessions: Mapping[str, Session] = field(default_factory=dict)
    workspaces: Mapping[str, Workspace] = field(default_factory=dict)
    secret_refs: Mapping[str, SecretRef] = field(default_factory=dict)
    audit_log: tuple[AuditEvent, ...] = ()
