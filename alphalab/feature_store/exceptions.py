"""Domain exceptions for the Feature Store."""

from alphalab.common.exceptions import AlphaLabError


class FeatureStoreError(AlphaLabError):
    """Base exception for all Feature Store errors."""


class FeatureValidationError(FeatureStoreError):
    """Raised when a feature definition or value fails structural validation."""


class InvalidFeatureStateError(FeatureStoreError):
    """Raised when an illegal registry or lifecycle transition is attempted."""


class FeatureNotFoundError(FeatureStoreError):
    """Raised when a lookup references a feature that is not registered."""
