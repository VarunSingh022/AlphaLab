"""Domain exceptions for the Live Trading Runtime."""

from alphalab.common.exceptions import AlphaLabError


class AlphaLabRuntimeError(AlphaLabError):
    """Base exception for all Runtime orchestration errors."""


class RuntimeValidationError(AlphaLabRuntimeError):
    """Raised when runtime configurations or parameters are invalid."""


class InvalidRuntimeTransitionError(AlphaLabRuntimeError):
    """Raised when an illegal lifecycle state transition is attempted."""
