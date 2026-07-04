"""Strict validation preventing impossible UI configurations."""

from alphalab.workbench.exceptions import WorkbenchValidationError
from alphalab.workbench.state import WorkbenchState


def validate_layout(layout_id: str, state: WorkbenchState) -> None:
    if not layout_id.strip():
        raise WorkbenchValidationError("Layout ID cannot be empty.")
    if layout_id in state.saved_layouts:
        raise WorkbenchValidationError(f"Layout '{layout_id}' already exists.")

def validate_tab_operation(tab_id: str, state: WorkbenchState) -> None:
    if not any(t.tab_id == tab_id for t in state.tabs):
        raise WorkbenchValidationError(f"Tab '{tab_id}' is not open.")