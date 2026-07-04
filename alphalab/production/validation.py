"""Strict validation rules ensuring runtime safety."""

from alphalab.production.exceptions import InvalidRuntimeStateError, ProductionValidationError
from alphalab.production.state import ProductionState


def validate_start(state: ProductionState) -> None:
    if state.is_running:
        raise InvalidRuntimeStateError("Runtime is already active.")

def validate_stop(state: ProductionState) -> None:
    if not state.is_running:
        raise InvalidRuntimeStateError("Runtime is not active.")

def validate_module_registration(state: ProductionState, module_id: str) -> None:
    if not module_id.strip():
        raise ProductionValidationError("Module ID cannot be empty.")
    if module_id in state.processes:
        raise ProductionValidationError(f"Module '{module_id}' already registered.")