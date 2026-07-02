"""Domain exceptions for the Strategy Runtime."""


class StrategyRuntimeError(Exception):
    """Base exception for all Strategy Runtime errors."""


class InvalidTransitionError(StrategyRuntimeError):
    """Raised when an invalid lifecycle transition is attempted."""


class InvalidIntentError(StrategyRuntimeError):
    """Raised when a strategy emits a malformed Intent."""


class HookExecutionError(StrategyRuntimeError):
    """Raised when a strategy hook throws an unhandled exception or exceeds timeout."""
