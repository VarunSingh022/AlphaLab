"""Domain exceptions for the Portfolio Engine."""

from alphalab.common.exceptions import AlphaLabError


class PortfolioEngineError(AlphaLabError):
    """Base exception for all Portfolio Engine errors."""


class PortfolioValidationError(PortfolioEngineError):
    """Raised when portfolio data or constraints fail structural validation."""


class OptimizationError(PortfolioEngineError):
    """Raised when an analytical optimization routine fails (e.g., singular matrix)."""


class ConstraintViolationError(PortfolioEngineError):
    """Raised when a portfolio's weights violate strict constraints."""


class InvalidPortfolioStateError(PortfolioEngineError):
    """Raised when an illegal lifecycle transition is attempted."""


class ConstructionInputError(PortfolioValidationError):
    """Raised when a construction problem is malformed (v3.8).

    Missing, non-finite, mis-sized, currency- or period-inconsistent inputs,
    a covariance that is not positive definite, or a constraint an objective
    cannot express. A well-formed problem whose constraints cannot all hold is
    *not* this error: it is an ordinary ``INFEASIBLE`` result, because a
    refused allocation is an outcome a caller plans for, not a mistake in the
    request.
    """
