"""Strict validation rules for parameters and search spaces."""

from alphalab.optimizer.exceptions import OptimizerValidationError
from alphalab.optimizer.parameter import Parameter, ParameterType


def validate_parameter(param: Parameter) -> None:
    """Validates the bounds and types of a single parameter definition."""
    if not param.name or not param.name.strip():
        raise OptimizerValidationError("Parameter name cannot be empty.")

    if param.choices is not None:
        if len(param.choices) == 0:
            raise OptimizerValidationError(f"Parameter '{param.name}' has empty choices.")
        return  # If choices are provided, min/max bounds are ignored.

    if param.param_type in {ParameterType.INT, ParameterType.FLOAT}:
        if param.minimum is None or param.maximum is None:
            raise OptimizerValidationError(
                f"Numeric parameter '{param.name}' must have minimum and maximum bounds."
            )
        if param.minimum > param.maximum:
            err_msg = (
                f"Parameter '{param.name}' minimum ({param.minimum}) "
                f"exceeds maximum ({param.maximum})."
            )
            raise OptimizerValidationError(err_msg)

        if param.step is not None and param.step <= 0:
            raise OptimizerValidationError(
                f"Parameter '{param.name}' step must be positive, got {param.step}."
            )


def validate_search_space(parameters: tuple[Parameter, ...]) -> None:
    """Validates an entire collection of parameters for an optimization run."""
    if not parameters:
        raise OptimizerValidationError("Search space must contain at least one parameter.")

    seen_names = set()
    for param in parameters:
        if param.name in seen_names:
            raise OptimizerValidationError(f"Duplicate parameter name detected: '{param.name}'.")
        seen_names.add(param.name)
        validate_parameter(param)
