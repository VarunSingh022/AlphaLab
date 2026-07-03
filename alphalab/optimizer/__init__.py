"""AlphaLab Optimization Engine."""

from alphalab.optimizer.adapter import OptimizationAdapter
from alphalab.optimizer.engine import OptimizationEngine
from alphalab.optimizer.events import (
    OptimizationCompleted,
    OptimizationFailed,
    OptimizationStarted,
    OptimizerEvent,
    TrialCompleted,
    TrialStarted,
)
from alphalab.optimizer.exceptions import (
    InvalidOptimizerStateError,
    OptimizerError,
    OptimizerValidationError,
)
from alphalab.optimizer.objective import (
    ObjectiveFunction,
    OptimizationDirection,
    evaluate_cagr,
    evaluate_calmar,
    evaluate_max_drawdown,
    evaluate_sharpe,
    evaluate_sortino,
    evaluate_total_return,
)
from alphalab.optimizer.optimizer import Optimizer
from alphalab.optimizer.parameter import Parameter, ParameterType
from alphalab.optimizer.protocol import TrialEvaluatorProtocol
from alphalab.optimizer.results import OptimizationResult, TrialResult
from alphalab.optimizer.search import (
    generate_grid_search,
    generate_random_search,
    generate_single_run,
)
from alphalab.optimizer.state import OptimizerState, OptimizerStatus
from alphalab.optimizer.validation import validate_parameter, validate_search_space
from alphalab.optimizer.views import (
    best_result,
    optimization_summary,
    progress,
    status,
    top_results,
    trial_count,
)

__all__ = [
    "InvalidOptimizerStateError",
    "ObjectiveFunction",
    "OptimizationAdapter",
    "OptimizationCompleted",
    "OptimizationDirection",
    "OptimizationEngine",
    "OptimizationFailed",
    "OptimizationResult",
    "OptimizationStarted",
    "Optimizer",
    "OptimizerError",
    "OptimizerEvent",
    "OptimizerState",
    "OptimizerStatus",
    "OptimizerValidationError",
    "Parameter",
    "ParameterType",
    "TrialCompleted",
    "TrialEvaluatorProtocol",
    "TrialResult",
    "TrialStarted",
    "best_result",
    "evaluate_cagr",
    "evaluate_calmar",
    "evaluate_max_drawdown",
    "evaluate_sharpe",
    "evaluate_sortino",
    "evaluate_total_return",
    "generate_grid_search",
    "generate_random_search",
    "generate_single_run",
    "optimization_summary",
    "progress",
    "status",
    "top_results",
    "trial_count",
    "validate_parameter",
    "validate_search_space",
]
