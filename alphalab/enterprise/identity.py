"""Principal registration and session lifecycle.

Session handling here is lifecycle-only -- issue, resolve, expire, close. It is
not authentication against a credential: no password or key is accepted. Put a
real identity provider in front of :func:`open_session`.
"""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.common.registry import with_mapping_item, without_mapping_key
from alphalab.enterprise.exceptions import EnterpriseInputError, EnterprisePermissionError
from alphalab.enterprise.models import EnterpriseState, Principal, Session


def register_principal(
    state: EnterpriseState, principal_id: str, display_name: str, timestamp: float
) -> tuple[EnterpriseState, Principal]:
    """Registers a new principal.

    Raises:
        EnterpriseInputError: If ``principal_id`` or ``display_name`` is blank,
            or ``principal_id`` is already registered.
    """
    if not principal_id.strip():
        raise EnterpriseInputError("principal_id cannot be empty.")
    if not display_name.strip():
        raise EnterpriseInputError("display_name cannot be empty.")
    if principal_id in state.principals:
        raise EnterpriseInputError(f"Principal '{principal_id}' is already registered.")

    principal = Principal(
        principal_id=principal_id, display_name=display_name, created_at=timestamp
    )
    return replace(
        state, principals=with_mapping_item(state.principals, principal_id, principal)
    ), principal


def get_principal(state: EnterpriseState, principal_id: str) -> Principal:
    """Returns a registered principal.

    Raises:
        EnterpriseInputError: If ``principal_id`` is unknown.
    """
    principal = state.principals.get(principal_id)
    if principal is None:
        raise EnterpriseInputError(f"Principal '{principal_id}' is not registered.")
    return principal


def open_session(
    state: EnterpriseState, principal_id: str, timestamp: float, ttl_seconds: float
) -> tuple[EnterpriseState, Session]:
    """Opens a session for a principal, expiring ``ttl_seconds`` after ``timestamp``.

    Raises:
        EnterpriseInputError: If the principal is unknown or ``ttl_seconds`` is
            not positive.
    """
    get_principal(state, principal_id)
    if ttl_seconds <= 0:
        raise EnterpriseInputError(f"ttl_seconds must be positive, got {ttl_seconds}.")

    session = Session(
        session_id=str(new_id()),
        principal_id=principal_id,
        opened_at=timestamp,
        expires_at=timestamp + ttl_seconds,
    )
    return replace(
        state, sessions=with_mapping_item(state.sessions, session.session_id, session)
    ), session


def resolve_session(state: EnterpriseState, session_id: str, now: float) -> Principal:
    """Returns the principal for a live session.

    Raises:
        EnterpriseInputError: If ``session_id`` is unknown.
        EnterprisePermissionError: If the session has expired at ``now``.
    """
    session = state.sessions.get(session_id)
    if session is None:
        raise EnterpriseInputError(f"Session '{session_id}' does not exist.")
    if now >= session.expires_at:
        raise EnterprisePermissionError(f"Session '{session_id}' has expired.")
    return get_principal(state, session.principal_id)


def close_session(state: EnterpriseState, session_id: str) -> EnterpriseState:
    """Closes a session.

    Raises:
        EnterpriseInputError: If ``session_id`` is unknown.
    """
    if session_id not in state.sessions:
        raise EnterpriseInputError(f"Session '{session_id}' does not exist.")
    return replace(state, sessions=without_mapping_key(state.sessions, session_id))
