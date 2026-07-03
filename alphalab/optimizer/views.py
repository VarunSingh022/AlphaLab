"""Pure queries exposing transparent Optimization Engine access."""

from collections.abc import Sequence

from alphalab.optimizer.objective import OptimizationDirection
from alphalab.optimizer.results import OptimizationResult, TrialResult
from alphalab.optimizer.state import OptimizerState, OptimizerStatus


def best_result(state: OptimizerState) -> TrialResult | None:
    """Returns the historically best performing trial result."""
    return state.best_trial


def top_results(state: OptimizerState, count: int = 5) -> Sequence[TrialResult]:
    """Returns the top N performing trials ordered by objective score."""
    if not state.completed_trials:
        return ()

    valid_trials = [t for t in state.completed_trials if t.error is None]

    is_reverse = state.objective.direction == OptimizationDirection.MAXIMIZE
    sorted_trials = sorted(valid_trials, key=lambda t: t.score, reverse=is_reverse)

    return tuple(sorted_trials[:count])


def trial_count(state: OptimizerState) -> int:
    """Returns the total number of evaluated trials."""
    return len(state.completed_trials)


def progress(state: OptimizerState) -> float:
    """Returns the completion percentage of the optimization run (0.0 to 1.0)."""
    total = len(state.pending_trials) + len(state.completed_trials)
    if total == 0:
        return 1.0
    return len(state.completed_trials) / total


def status(state: OptimizerState) -> OptimizerStatus:
    """Returns the explicit lifecycle stage of the optimizer."""
    return state.status


def optimization_summary(state: OptimizerState) -> OptimizationResult:
    """Compiles the final optimization result snapshot."""
    total_time = state.end_time - state.start_time if state.end_time > 0 else 0.0
    return OptimizationResult(
        best_trial=state.best_trial,
        all_trials=state.completed_trials,
        total_execution_time=total_time,
    )
