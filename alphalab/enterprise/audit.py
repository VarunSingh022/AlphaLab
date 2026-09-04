"""Append-only audit logging.

This is a standalone capability: RBAC, workspace, and secret operations do not
auto-emit audit events, keeping those modules single-purpose. A caller that
needs an audit trail records one explicitly alongside the mutation.
"""

from collections.abc import Mapping
from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.enterprise.exceptions import EnterpriseInputError
from alphalab.enterprise.models import AuditEvent, EnterpriseState


def record_audit(
    state: EnterpriseState,
    actor_id: str,
    action: str,
    target: str,
    timestamp: float,
    metadata: Mapping[str, str] | None = None,
) -> tuple[EnterpriseState, AuditEvent]:
    """Appends an audit event to the log.

    Raises:
        EnterpriseInputError: If ``actor_id``, ``action``, or ``target`` is
            blank.
    """
    if not actor_id.strip():
        raise EnterpriseInputError("actor_id cannot be empty.")
    if not action.strip():
        raise EnterpriseInputError("action cannot be empty.")
    if not target.strip():
        raise EnterpriseInputError("target cannot be empty.")

    event = AuditEvent(
        event_id=str(new_id()),
        actor_id=actor_id,
        action=action,
        target=target,
        timestamp=timestamp,
        metadata=dict(metadata) if metadata else {},
    )
    return replace(state, audit_log=(*state.audit_log, event)), event


def audit_trail(
    state: EnterpriseState, actor_id: str | None = None, action: str | None = None
) -> tuple[AuditEvent, ...]:
    """Returns audit events in order, optionally filtered by actor and/or action."""
    events = state.audit_log
    if actor_id is not None:
        events = tuple(event for event in events if event.actor_id == actor_id)
    if action is not None:
        events = tuple(event for event in events if event.action == action)
    return events
