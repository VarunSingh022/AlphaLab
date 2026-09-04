"""Domain exceptions for the Model Registry."""

from alphalab.common.exceptions import AlphaLabError


class ModelRegistryError(AlphaLabError):
    """Base exception for all Model Registry errors."""


class ModelRegistryInputError(ModelRegistryError):
    """Raised when registration, promotion, rollback, or deployment inputs are invalid."""
