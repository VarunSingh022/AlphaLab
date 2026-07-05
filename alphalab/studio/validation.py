"""Strict validation rules ensuring Studio integrity."""

from alphalab.studio.exceptions import StudioValidationError
from alphalab.studio.project import Project
from alphalab.studio.state import StrategyStudioState


def validate_project_creation(state: StrategyStudioState, project: Project) -> None:
    if not project.project_id.strip():
        raise StudioValidationError("Project ID cannot be empty.")
    if project.project_id in state.projects:
        raise StudioValidationError(f"Project '{project.project_id}' already exists.")


def validate_project_exists(state: StrategyStudioState, project_id: str) -> None:
    if project_id not in state.projects:
        raise StudioValidationError(f"Project '{project_id}' not found.")
