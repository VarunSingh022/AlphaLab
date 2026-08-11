"""Domain exceptions for the Machine Learning Engine."""

from alphalab.common.exceptions import AlphaLabError


class MLError(AlphaLabError):
    """Base exception for all Machine Learning Engine errors."""


class MLInputError(MLError):
    """Raised when dataset, matrix, or hyperparameter inputs are invalid."""


class MLComputationError(MLError):
    """Raised when a computation cannot produce a defined result."""
