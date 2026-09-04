"""Multi-user collaboration workspaces and their membership."""

from dataclasses import replace

from alphalab.common.registry import with_mapping_item
from alphalab.enterprise.exceptions import EnterpriseInputError
from alphalab.enterprise.identity import get_principal
from alphalab.enterprise.models import EnterpriseState, Workspace


def create_workspace(
    state: EnterpriseState,
    workspace_id: str,
    name: str,
    owner_id: str,
    timestamp: float,
) -> tuple[EnterpriseState, Workspace]:
    """Creates a workspace owned by an existing principal.

    Raises:
        EnterpriseInputError: If ``workspace_id`` or ``name`` is blank, the
            workspace already exists, or ``owner_id`` is not a registered
            principal.
    """
    if not workspace_id.strip():
        raise EnterpriseInputError("workspace_id cannot be empty.")
    if not name.strip():
        raise EnterpriseInputError("name cannot be empty.")
    if workspace_id in state.workspaces:
        raise EnterpriseInputError(f"Workspace '{workspace_id}' already exists.")
    get_principal(state, owner_id)

    workspace = Workspace(
        workspace_id=workspace_id, name=name, owner_id=owner_id, created_at=timestamp
    )
    return replace(
        state, workspaces=with_mapping_item(state.workspaces, workspace_id, workspace)
    ), workspace


def get_workspace(state: EnterpriseState, workspace_id: str) -> Workspace:
    """Returns a workspace.

    Raises:
        EnterpriseInputError: If ``workspace_id`` is unknown.
    """
    workspace = state.workspaces.get(workspace_id)
    if workspace is None:
        raise EnterpriseInputError(f"Workspace '{workspace_id}' does not exist.")
    return workspace


def add_member(state: EnterpriseState, workspace_id: str, principal_id: str) -> EnterpriseState:
    """Adds a principal as a member of a workspace.

    Raises:
        EnterpriseInputError: If the workspace or principal is unknown, the
            principal is the owner, or the principal is already a member.
    """
    workspace = get_workspace(state, workspace_id)
    get_principal(state, principal_id)
    if principal_id == workspace.owner_id:
        raise EnterpriseInputError(
            f"Principal '{principal_id}' owns workspace '{workspace_id}' and is already a member."
        )
    if principal_id in workspace.member_ids:
        raise EnterpriseInputError(
            f"Principal '{principal_id}' is already a member of workspace '{workspace_id}'."
        )
    updated = replace(workspace, member_ids=workspace.member_ids | {principal_id})
    return replace(state, workspaces=with_mapping_item(state.workspaces, workspace_id, updated))


def remove_member(state: EnterpriseState, workspace_id: str, principal_id: str) -> EnterpriseState:
    """Removes a member from a workspace.

    Raises:
        EnterpriseInputError: If the workspace is unknown, the principal is the
            owner, or the principal is not a member.
    """
    workspace = get_workspace(state, workspace_id)
    if principal_id == workspace.owner_id:
        raise EnterpriseInputError(
            f"Cannot remove the owner '{principal_id}' from workspace '{workspace_id}'."
        )
    if principal_id not in workspace.member_ids:
        raise EnterpriseInputError(
            f"Principal '{principal_id}' is not a member of workspace '{workspace_id}'."
        )
    updated = replace(workspace, member_ids=workspace.member_ids - {principal_id})
    return replace(state, workspaces=with_mapping_item(state.workspaces, workspace_id, updated))


def workspace_members(state: EnterpriseState, workspace_id: str) -> frozenset[str]:
    """Returns every member of a workspace, including the owner.

    Raises:
        EnterpriseInputError: If ``workspace_id`` is unknown.
    """
    workspace = get_workspace(state, workspace_id)
    return workspace.member_ids | {workspace.owner_id}


def workspaces_for(state: EnterpriseState, principal_id: str) -> tuple[Workspace, ...]:
    """Returns every workspace a principal owns or is a member of, id-sorted."""
    matches = [
        workspace
        for workspace in state.workspaces.values()
        if principal_id == workspace.owner_id or principal_id in workspace.member_ids
    ]
    return tuple(sorted(matches, key=lambda workspace: workspace.workspace_id))
