"""Domain exceptions for the Cluster Scheduler."""

from alphalab.common.exceptions import AlphaLabError


class ClusterSchedulerError(AlphaLabError):
    """Base exception for all Cluster Scheduler errors."""


class ClusterSchedulerInputError(ClusterSchedulerError):
    """Raised when scheduling, queue, or affinity inputs are invalid."""
