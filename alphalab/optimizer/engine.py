"""Pure functional Optimization Engine orchestrating trial evaluation."""

import time
import uuid
from dataclasses import replace
from typing import Any

from alphalab.optimizer.events import (
    OptimizationCompleted,
    OptimizationFailed,
    OptimizationStarted,
    TrialCompleted,
    TrialStarted,
)
from alphalab.optimizer.exceptions import InvalidOptimizerStateError
from alphalab.optimizer.objective import ObjectiveFunction, OptimizationDirection
from alphalab.optimizer.protocol import TrialEvaluatorProtocol
from alphalab.optimizer.results import TrialResult
from alphalab.optimizer.state import OptimizerState, OptimizerStatus


class OptimizationEngine:
    """Facade orchestrating pure functional state machine transitions for optimization."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def initialize(
        engine_id: str,
        objective: ObjectiveFunction,
        trials: tuple[dict[str, Any], ...],
    ) -> OptimizerState:
        """Constructs an empty base state for the optimization layer."""
        if not trials:
            raise ValueError("Cannot initialize optimizer with zero trials.")
            
        return OptimizerState(
            engine_id=engine_id,
            status=OptimizerStatus.CREATED,
            objective=objective,
            pending_trials=trials,
        )

    @staticmethod
    def start(
        state: OptimizerState, timestamp: float, search_method: str = "Grid"
    ) -> OptimizerState:
        """Transitions the optimizer to RUNNING and logs the start event."""
        if state.status != OptimizerStatus.CREATED:
            raise InvalidOptimizerStateError(f"Cannot start from {state.status.name}")

        start_evt = OptimizationStarted(
            event_id=OptimizationEngine._create_id(),
            timestamp=timestamp,
            total_trials=len(state.pending_trials),
            search_method=search_method,
        )

        return replace(
            state,
            status=OptimizerStatus.RUNNING,
            start_time=timestamp,
            events=(*state.events, start_evt),
        )

    @staticmethod
    def step(
        state: OptimizerState, 
        evaluator: TrialEvaluatorProtocol, 
        timestamp: float
    ) -> tuple[OptimizerState, TrialResult | None]:
        """
        Pops one pending trial, delegates execution to the evaluator, computes the
        objective score, updates rankings, and advances state.
        """
        if state.status != OptimizerStatus.RUNNING:
            return state, None

        if not state.pending_trials:
            return OptimizationEngine.complete(state, timestamp), None

        # 1. Pop Trial
        params = state.pending_trials[0]
        remaining_trials = state.pending_trials[1:]
        trial_id = OptimizationEngine._create_id()

        start_evt = TrialStarted(OptimizationEngine._create_id(), timestamp, trial_id)

        # 2. Evaluate (Mocking real-world wall-clock measurement safely)
        eval_start = time.perf_counter()
        try:
            metrics = evaluator.evaluate(params)
            score = state.objective.evaluator(metrics)
            error = None
        except Exception as e:
            metrics = {}
            score = (
                float("-inf") 
                if state.objective.direction == OptimizationDirection.MAXIMIZE 
                else float("inf")
            )
            error = str(e)
            
        eval_time = time.perf_counter() - eval_start

        result = TrialResult(
            trial_id=trial_id,
            parameters=params,
            metrics=metrics,
            score=score,
            execution_time_seconds=eval_time,
            error=error,
        )

        comp_evt = TrialCompleted(
            OptimizationEngine._create_id(), timestamp, trial_id, score, eval_time
        )

        # 3. Update Best Result
        new_best = state.best_trial
        if error is None:
            if new_best is None:
                new_best = result
            else:
                if state.objective.direction == OptimizationDirection.MAXIMIZE:
                    if score > new_best.score:
                        new_best = result
                else:
                    if score < new_best.score:
                        new_best = result

        # 4. Advance State
        new_state = replace(
            state,
            pending_trials=remaining_trials,
            completed_trials=(*state.completed_trials, result),
            best_trial=new_best,
            events=(*state.events, start_evt, comp_evt),
        )

        # 5. Auto-Complete if exhausted
        if not remaining_trials:
            new_state = OptimizationEngine.complete(new_state, timestamp)

        return new_state, result

    @staticmethod
    def complete(state: OptimizerState, timestamp: float) -> OptimizerState:
        """Transitions the optimizer to COMPLETED."""
        if state.status not in {OptimizerStatus.RUNNING, OptimizerStatus.CREATED}:
            raise InvalidOptimizerStateError("Cannot complete from current state.")
            
        best_score = state.best_trial.score if state.best_trial else 0.0
        total_time = timestamp - state.start_time
        
        comp_evt = OptimizationCompleted(
            OptimizationEngine._create_id(), timestamp, best_score, total_time
        )
        
        return replace(
            state,
            status=OptimizerStatus.COMPLETED,
            end_time=timestamp,
            events=(*state.events, comp_evt),
        )

    @staticmethod
    def fail(state: OptimizerState, reason: str, timestamp: float) -> OptimizerState:
        """Transitions the optimizer to FAILED."""
        fail_evt = OptimizationFailed(
            OptimizationEngine._create_id(), timestamp, reason
        )
        return replace(
            state,
            status=OptimizerStatus.FAILED,
            end_time=timestamp,
            events=(*state.events, fail_evt),
        )