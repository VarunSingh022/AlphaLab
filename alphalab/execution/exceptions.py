"""Domain exceptions for the Execution Engine."""


class ExecutionError(Exception):
    """Base exception for all Execution Engine errors."""

    pass


class ExecutionValidationError(ExecutionError):
    """Raised when an execution fails validation rules."""

    pass


class InvalidExecutionStateError(ExecutionError):
    """Raised when an execution state transition is invalid."""

    pass
