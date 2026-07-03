"""Domain exceptions for the Distributed Research Framework."""


class DistributedError(Exception):
    """Base exception for all Distributed execution errors."""


class DistributedValidationError(DistributedError):
    """Raised when jobs or workers fail structural/logical validation."""


class InvalidJobStateError(DistributedError):
    """Raised when an illegal lifecycle transition is attempted on a Job."""


class InvalidNodeStateError(DistributedError):
    """Raised when an illegal operation is attempted on a WorkerNode."""
