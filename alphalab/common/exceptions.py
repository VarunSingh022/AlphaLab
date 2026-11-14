"""Shared exception hierarchy for AlphaLab."""


class AlphaLabError(Exception):
    """Base exception for shared AlphaLab errors."""


class AlphaLabValidationError(AlphaLabError, ValueError):
    """Raised when shared validation rules fail."""


class AlphaLabSerializationError(AlphaLabError):
    """Raised when shared serialization helpers cannot encode a value."""


class AlphaLabRegistryError(AlphaLabError):
    """Raised when shared registry operations fail."""


class ConvergenceError(AlphaLabError, ArithmeticError):
    """An iterative method did not converge within its limit (ADR-0058, v4.3).

    Raised instead of returning a best effort: a result short of its own
    criterion is not the result asked for. It names what was asked and what
    was reached, so the caller can decide whether to ask again with another
    tolerance or limit.

    Attributes:
        method: What was iterating.
        tolerance: The criterion it had to meet.
        limit: The iterations it was allowed.
        reached: How close it came, in the criterion's own terms.
    """

    def __init__(self, method: str, tolerance: float, limit: int, reached: float) -> None:
        super().__init__(
            f"{method} did not converge to {tolerance!r} within {limit} iterations; "
            f"it reached {reached!r}."
        )
        self.method = method
        self.tolerance = tolerance
        self.limit = limit
        self.reached = reached
