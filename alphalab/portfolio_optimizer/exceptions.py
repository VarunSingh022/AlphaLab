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
