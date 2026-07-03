"""Immutable domain events describing the Optimization Engine lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptimizerEvent:
    """Base class for all Optimizer system events."""
    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class OptimizationStarted(OptimizerEvent):
    """Emitted when an optimization run begins."""
    total_trials: int
    search_method: str


@dataclass(frozen=True, slots=True)
class OptimizationCompleted(OptimizerEvent):
    """Emitted when an optimization run successfully evaluates all trials."""
    best_score: float
    total_time_seconds: float


@dataclass(frozen=True, slots=True)
class OptimizationFailed(OptimizerEvent):
    """Emitted when an optimization run fails prematurely."""
    reason: str


@dataclass(frozen=True, slots=True)
class TrialStarted(OptimizerEvent):
    """Emitted when a specific parameter set trial begins evaluation."""
    trial_id: str


@dataclass(frozen=True, slots=True)
class TrialCompleted(OptimizerEvent):
    """Emitted when a trial finishes evaluation and records its score."""
    trial_id: str
    score: float
    execution_time_seconds: float