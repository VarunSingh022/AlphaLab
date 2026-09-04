"""Role-based access control: role definitions, grants, and permission checks."""

from collections.abc import Iterable
from dataclasses import replace

from alphalab.common.registry import with_mapping_item
from alphalab.enterprise.exceptions import EnterpriseInputError, EnterprisePermissionError
from alphalab.enterprise.identity import get_principal
from alphalab.enterprise.models import PRIVILEGED_WILDCARD, EnterpriseState


def define_role(state: EnterpriseState, role: str, permissions: Iterable[str]) -> EnterpriseState:
    """Defines or replaces a role and the permissions it grants.

    Raises:
        EnterpriseInputError: If ``role`` is blank or ``permissions`` is empty
            or contains a blank entry.
    """
    if not role.strip():
        raise EnterpriseInputError("role cannot be empty.")
    permission_set = frozenset(permissions)
    if not permission_set:
        raise EnterpriseInputError(f"Role '{role}' must grant at least one permission.")
    if any(not permission.strip() for permission in permission_set):
        raise EnterpriseInputError(f"Role '{role}' has a blank permission.")
    return replace(state, roles=with_mapping_item(state.roles, role, permission_set))


def grant_role(state: EnterpriseState, principal_id: str, role: str) -> EnterpriseState:
    """Grants ``role`` to a principal.

    Raises:
        EnterpriseInputError: If the principal or role is unknown.
    """
    principal = get_principal(state, principal_id)
    if role not in state.roles:
        raise EnterpriseInputError(f"Role '{role}' is not defined.")
    updated = replace(principal, roles=principal.roles | {role})
    return replace(state, principals=with_mapping_item(state.principals, principal_id, updated))


def revoke_role(state: EnterpriseState, principal_id: str, role: str) -> EnterpriseState:
    """Revokes ``role`` from a principal.

    Raises:
        EnterpriseInputError: If the principal is unknown or does not hold the
            role.
    """
    principal = get_principal(state, principal_id)
    if role not in principal.roles:
        raise EnterpriseInputError(f"Principal '{principal_id}' does not hold role '{role}'.")
    updated = replace(principal, roles=principal.roles - {role})
    return replace(state, principals=with_mapping_item(state.principals, principal_id, updated))


def permissions_for(state: EnterpriseState, principal_id: str) -> frozenset[str]:
    """Returns the union of permissions across all roles a principal holds.

    Raises:
        EnterpriseInputError: If the principal is unknown.
    """
    principal = get_principal(state, principal_id)
    granted: set[str] = set()
    for role in principal.roles:
        granted |= state.roles.get(role, frozenset())
    return frozenset(granted)


def has_permission(state: EnterpriseState, principal_id: str, permission: str) -> bool:
    """Returns whether a principal has ``permission`` (``"*"`` grants everything).

    Raises:
        EnterpriseInputError: If the principal is unknown.
    """
    granted = permissions_for(state, principal_id)
    return PRIVILEGED_WILDCARD in granted or permission in granted


def require_permission(state: EnterpriseState, principal_id: str, permission: str) -> None:
    """Raises unless a principal has ``permission``.

    Raises:
        EnterpriseInputError: If the principal is unknown.
        EnterprisePermissionError: If the principal lacks ``permission``.
    """
    if not has_permission(state, principal_id, permission):
        raise EnterprisePermissionError(
            f"Principal '{principal_id}' lacks permission '{permission}'."
        )
