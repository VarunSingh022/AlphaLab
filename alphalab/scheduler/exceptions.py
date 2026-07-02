"""Domain exceptions for the Scheduler & Time Engine."""


class SchedulerError(Exception):
    """Base exception for all Scheduler Engine errors."""

    pass


class SchedulerValidationError(SchedulerError):
    """Raised when a schedule or timer fails structural or temporal validation."""

    pass


class InvalidClockStateError(SchedulerError):
    """Raised when an invalid clock operation is attempted, such as moving backwards."""

    pass
