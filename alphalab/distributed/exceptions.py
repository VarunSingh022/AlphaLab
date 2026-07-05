"""Domain exceptions for the Distributed Research Framework."""

from alphalab.common.exceptions import AlphaLabError


class DistributedError(AlphaLabError):
    """Base exception for all Distributed execution errors."""


class DistributedValidationError(DistributedError):
    """Raised when jobs or workers fail structural/logical validation."""


class InvalidJobStateError(DistributedError):
    """Raised when an illegal lifecycle transition is attempted on a Job."""


class InvalidNodeStateError(DistributedError):
    """Raised when an illegal operation is attempted on a WorkerNode."""
