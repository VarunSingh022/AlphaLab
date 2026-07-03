"""High-level optimization orchestration runner."""

import time
from typing import Any

from alphalab.optimizer.engine import OptimizationEngine
from alphalab.optimizer.objective import ObjectiveFunction
from alphalab.optimizer.parameter import Parameter
from alphalab.optimizer.protocol import TrialEvaluatorProtocol
from alphalab.optimizer.search import (
    generate_grid_search,
    generate_random_search,
    generate_single_run,
)
from alphalab.optimizer.state import OptimizerState
from alphalab.optimizer.validation import validate_search_space


class Optimizer:
    """Facade orchestrating the complete evaluation of a parameter space."""

    @staticmethod
    def run_grid_search(
        engine_id: str,
        parameters: tuple[Parameter, ...],
        objective: ObjectiveFunction,
        evaluator: TrialEvaluatorProtocol,
    ) -> OptimizerState:
        """Executes a full exhaustive grid search optimization loop."""
        validate_search_space(parameters)
        trials = generate_grid_search(parameters)
        return Optimizer._execute_loop(engine_id, objective, trials, evaluator, "Grid")

    @staticmethod
    def run_random_search(
        engine_id: str,
        parameters: tuple[Parameter, ...],
        objective: ObjectiveFunction,
        evaluator: TrialEvaluatorProtocol,
        num_trials: int,
        seed: int = 42,
    ) -> OptimizerState:
        """Executes a seeded random search optimization loop."""
        validate_search_space(parameters)
        trials = generate_random_search(parameters, num_trials, seed)
        return Optimizer._execute_loop(engine_id, objective, trials, evaluator, "Random")

    @staticmethod
    def run_single(
        engine_id: str,
        parameters: tuple[Parameter, ...],
        objective: ObjectiveFunction,
        evaluator: TrialEvaluatorProtocol,
    ) -> OptimizerState:
        """Executes exactly one trial using default parameter values."""
        validate_search_space(parameters)
        trials = generate_single_run(parameters)
        return Optimizer._execute_loop(engine_id, objective, trials, evaluator, "Single")

    @staticmethod
    def _execute_loop(
        engine_id: str,
        objective: ObjectiveFunction,
        trials: tuple[dict[str, Any], ...],
        evaluator: TrialEvaluatorProtocol,
        search_method: str,
    ) -> OptimizerState:
        """Internal pure loop advancing the engine step-by-step until completion."""
        state = OptimizationEngine.initialize(engine_id, objective, trials)
        state = OptimizationEngine.start(state, time.time(), search_method=search_method)
        
        while state.pending_trials:
            state, _ = OptimizationEngine.step(state, evaluator, time.time())
            
        return state