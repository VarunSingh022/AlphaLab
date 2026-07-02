"""Domain exceptions for the Replay Engine."""


class ReplayError(Exception):
    """Base exception for all Replay Engine errors."""


class ReplayValidationError(ReplayError):
    """Raised when replay data or configuration fails structural validation."""


class InvalidReplayStateError(ReplayError):
    """Raised when an invalid lifecycle transition is attempted on the Replay Engine."""
