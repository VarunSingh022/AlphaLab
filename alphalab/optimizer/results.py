"""Immutable result tracking models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TrialResult:
    """Immutable record of a single evaluated parameter set."""
    trial_id: str
    parameters: Mapping[str, Any]
    metrics: Mapping[str, float]
    score: float
    execution_time_seconds: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Immutable aggregate output of a completed optimization run."""
    best_trial: TrialResult | None
    all_trials: tuple[TrialResult, ...] = field(default_factory=tuple)
    total_execution_time: float = 0.0