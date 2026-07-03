"""High-performance benchmark suite for the functional Optimization Engine."""

import time
from typing import Any

from alphalab.optimizer import (
    ObjectiveFunction,
    OptimizationDirection,
    OptimizationEngine,
    Parameter,
    ParameterType,
    TrialEvaluatorProtocol,
    evaluate_sharpe,
    generate_random_search,
)


class FastMockEvaluator(TrialEvaluatorProtocol):
    """A zero-sleep, high-throughput mock evaluator."""
    def evaluate(self, parameters: dict[str, Any]) -> dict[str, float]:
        x = float(parameters.get("x", 0.0))
        y = float(parameters.get("y", 0.0))
        return {"sharpe_ratio": x + y}


def run_benchmark() -> None:
    params = (
        Parameter("x", ParameterType.FLOAT, default=0.0, minimum=-10.0, maximum=10.0),
        Parameter("y", ParameterType.FLOAT, default=0.0, minimum=-10.0, maximum=10.0),
        Parameter("z", ParameterType.FLOAT, default=0.0, minimum=-10.0, maximum=10.0),
    )
    
    obj = ObjectiveFunction("Sharpe", OptimizationDirection.MAXIMIZE, evaluate_sharpe)
    evaluator = FastMockEvaluator()

    N = 10_000
    print(f"Starting Optimizer Benchmark: Evaluating {N} Parameter Sets...")

    # 1. Generate Search Space
    start_gen = time.perf_counter()
    trials = generate_random_search(params, num_trials=N, seed=42)
    gen_duration = time.perf_counter() - start_gen
    
    # 2. Setup Engine
    state = OptimizationEngine.initialize("BENCH-OPT", obj, trials)
    state = OptimizationEngine.start(state, 1000.0)

    # 3. Evaluate Loop
    start_eval = time.perf_counter()
    
    for i in range(N):
        state, _ = OptimizationEngine.step(state, evaluator, float(1001 + i))

    eval_duration = time.perf_counter() - start_eval
    
    ops_sec = N / eval_duration
    print(f"Generation Time: {gen_duration:.4f}s")
    print(f"Evaluation Time: {eval_duration:.4f}s")
    print(f"Throughput: {ops_sec:.2f} evaluations/sec")
    
    if state.best_trial:
        print(f"Best Score Achieved: {state.best_trial.score:.4f}")


if __name__ == "__main__":
    run_benchmark()