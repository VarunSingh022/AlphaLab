"""Domain exceptions for the Cloud Research Engine."""

from alphalab.common.exceptions import AlphaLabError


class CloudResearchError(AlphaLabError):
    """Base exception for all Cloud Research Engine errors."""


class CloudResearchInputError(CloudResearchError):
    """Raised when cluster, task, or sweep inputs are invalid."""
