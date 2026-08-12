"""Domain exceptions for the Deep Learning Engine."""

from alphalab.common.exceptions import AlphaLabError


class DeepLearningError(AlphaLabError):
    """Base exception for all Deep Learning Engine errors."""


class DLInputError(DeepLearningError):
    """Raised when layer, network, or training inputs are invalid."""


class DLComputationError(DeepLearningError):
    """Raised when a forward or backward pass cannot produce a defined result."""
