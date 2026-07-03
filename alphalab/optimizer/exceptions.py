"""Domain exceptions for the Optimization Engine."""


class OptimizerError(Exception):
    """Base exception for all Optimization Engine errors."""

    pass


class OptimizerValidationError(OptimizerError):
    """Raised when optimization parameters, search spaces, or inputs are invalid."""

    pass


class InvalidOptimizerStateError(OptimizerError):
    """Raised when an illegal lifecycle transition is attempted in the optimizer."""

    pass
