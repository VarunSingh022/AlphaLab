"""Domain exceptions for the Execution Engine."""

from alphalab.common.exceptions import AlphaLabError


class ExecutionError(AlphaLabError):
    """Base exception for all Execution Engine errors."""

    pass


class ExecutionValidationError(ExecutionError):
    """Raised when an execution fails validation rules."""

    pass


class InvalidExecutionStateError(ExecutionError):
    """Raised when an execution state transition is invalid."""

    pass
