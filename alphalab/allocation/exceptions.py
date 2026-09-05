"""Domain exceptions for the Allocation Engine."""

from alphalab.common.exceptions import AlphaLabError


class AllocationError(AlphaLabError):
    """Base exception for all Allocation Engine errors."""

    pass


class AllocationValidationError(AllocationError):
    """Raised when an intent or allocation fails structural validation."""

    pass


class BudgetExceededError(AllocationError):
    """Raised when requested allocations exceed available capital or limits."""

    pass


class UnknownReservationError(AllocationError):
    """Raised when releasing capital an order does not hold.

    A reservation is released exactly once. A second release -- or a release of
    an order that was never allocated, or whose capital an execution already
    consumed -- is a lifecycle defect, so it raises rather than quietly
    subtracting from the running total.
    """

    pass
