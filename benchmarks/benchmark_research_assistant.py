"""High-performance benchmark suite for the Research Assistant."""

import time
from collections.abc import Mapping

from alphalab.experiment_tracking.tracker import ExperimentTracker
from alphalab.research_assistant import (
    StrategyCandidate,
    evaluate_candidates,
    generate_candidates,
    run_research_workflow,
)


def _evaluator(candidate: StrategyCandidate) -> Mapping[str, float]:
    fast = candidate.parameters["fast"]
    slow = candidate.parameters["slow"]
    return {"sharpe": -abs(fast - 25.0) - abs(slow - 75.0), "turnover": fast + slow}


def run_benchmark() -> None:
    space: Mapping[str, tuple[float, ...]] = {
        "fast": tuple(float(v) for v in range(1, 51)),
        "slow": tuple(float(v) for v in range(1, 51)),
    }

    print("Starting Research Assistant Benchmark: 2500-candidate grid...")
    start = time.perf_counter()
    candidates = generate_candidates("grid", space)
    duration = time.perf_counter() - start
    print(f"  generate_candidates ({len(candidates)}): {duration:.4f}s")

    N_EVAL = 20
    start = time.perf_counter()
    for _ in range(N_EVAL):
        evaluate_candidates(candidates, _evaluator, "sharpe")
    duration = time.perf_counter() - start
    print(
        f"  evaluate_candidates x{N_EVAL} ({len(candidates)} each): {duration:.4f}s, "
        f"{N_EVAL * len(candidates) / duration:.2f} evals/sec"
    )

    N_WORKFLOW = 20
    start = time.perf_counter()
    for _ in range(N_WORKFLOW):
        run_research_workflow(
            "grid", space, _evaluator, "sharpe", timestamp=0.0, tracker=ExperimentTracker()
        )
    duration = time.perf_counter() - start
    print(
        f"  run_research_workflow x{N_WORKFLOW} (with tracker): {duration:.4f}s, "
        f"{N_WORKFLOW / duration:.2f} workflows/sec"
    )


if __name__ == "__main__":
    run_benchmark()
