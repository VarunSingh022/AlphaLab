"""Domain exceptions for the Allocation Engine."""


class AllocationError(Exception):
    """Base exception for all Allocation Engine errors."""

    pass


class AllocationValidationError(AllocationError):
    """Raised when an intent or allocation fails structural validation."""

    pass


class BudgetExceededError(AllocationError):
    """Raised when requested allocations exceed available capital or limits."""

    pass
