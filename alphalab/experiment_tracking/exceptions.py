"""Domain exceptions for Experiment Tracking."""

from alphalab.common.exceptions import AlphaLabError


class ExperimentTrackingError(AlphaLabError):
    """Base exception for all Experiment Tracking errors."""


class ExperimentTrackingInputError(ExperimentTrackingError):
    """Raised when run, metric, or lineage inputs are invalid."""
