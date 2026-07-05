"""Domain exceptions for the Order Management System."""

from alphalab.common.exceptions import AlphaLabError


class OMSError(AlphaLabError):
    """Base exception for all OMS errors."""

    pass


class DuplicateOrderError(OMSError):
    """Raised when an order ID already exists in the book."""

    pass


class UnknownOrderError(OMSError):
    """Raised when an operation targets a non-existent order."""

    pass


class InvalidTransitionError(OMSError):
    """Raised when an order transition is not allowed."""

    pass


class OrderValidationError(OMSError):
    """Raised when an order fails business validation rules."""

    pass
