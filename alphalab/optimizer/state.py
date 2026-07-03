"""Global immutable state container for the Optimization Engine."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from alphalab.optimizer.events import OptimizerEvent
from alphalab.optimizer.objective import ObjectiveFunction
from alphalab.optimizer.results import TrialResult


class OptimizerStatus(Enum):
    """Explicit pure state machine stages for the Optimization Engine."""
    CREATED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class OptimizerState:
    """Deterministic snapshot of an active optimization run."""
    engine_id: str
    status: OptimizerStatus
    objective: ObjectiveFunction
    pending_trials: tuple[dict[str, Any], ...]
    completed_trials: tuple[TrialResult, ...] = field(default_factory=tuple)
    best_trial: TrialResult | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    events: tuple[OptimizerEvent, ...] = field(default_factory=tuple)