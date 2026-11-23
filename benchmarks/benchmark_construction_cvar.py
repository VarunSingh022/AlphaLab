"""Minimum-CVaR construction over scenarios, through the certified linear program (ADR-0063).

WS-12's budget is a construction at 1,000 assets with CVaR over 2,520
scenarios within 60 seconds, in pure Python or through the compute backend's
cross-check. The linear program forms and factors normal equations with one row
per scenario, so its cost grows with the cube of the scenario count. Measured
at the budget's own size at M9's close on the reference machine (arm64,
CPython 3.12.4, alone), one construction took 10,265 seconds. It needed 29
iterations and was certified ``OPTIMAL`` at a vertex. Forming and factoring the
2,521-row normal equations took 9,958 of those seconds, and the vertex finish
86. The budget is held for M11's compute backend (WS-15), open in the progress
register until then.

What this guards, in CI: a universe twice the size, with twice the scenarios,
may cost at most ``MAX_GROWTH`` times the small one. A cubic method grows
about 8x over that span; a step that grew faster, such as a normal matrix formed
densely over every pair of columns, would cross the ceiling.

``python benchmarks/benchmark_construction_cvar.py --budget`` measures the
budget's size itself. It takes hours, so CI never runs it; run it alone.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import SAMPLES, fastest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    ExposureRange,
    MinimumCVaR,
    ScenarioSet,
    SolverSettings,
    WeightBounds,
    construct,
)

#: The universes judged in CI, each with 2.52 scenarios per asset.
UNIVERSES = (40, 80)
#: The most a doubled universe may cost; cubic is about 8x.
MAX_GROWTH = 16.0
#: The budget's size, and its ceiling in seconds.
BUDGET_ASSETS = 1000
BUDGET_SECONDS = 60.0


def _problem(count: int) -> ConstructionProblem:
    """Minimum CVaR at 0.95 over ``round(2.52 * count)`` scenarios, fully invested, capped.

    The scenarios are drawn with ``random()`` and arithmetic alone, so every host
    builds the same program. The covariance is diagonal: the linear method does
    not read it, and a full one at the budget's size would cost minutes to state,
    which is not the construction's cost.
    """

    scenarios = round(2.52 * count)
    rng = random.Random(20261122 + count)
    assets = tuple(f"S{index:04d}" for index in range(count))
    vol = [0.01 + 0.02 * rng.random() for _ in assets]
    beta = [0.5 + rng.random() for _ in assets]
    rows = []
    for _ in range(scenarios):
        market = 0.02 * (rng.random() - 0.5)
        rows.append(
            tuple(
                0.0002 + beta[i] * market + vol[i] * 2.0 * (rng.random() - 0.5)
                for i in range(count)
            )
        )
    scenario_set = ScenarioSet(assets, tuple(rows), "USD", "1D", "benchmark scenarios")
    covariance = CovarianceMatrix.from_rows(
        assets,
        [[(vol[i] * vol[i] if i == j else 0.0) for j in range(count)] for i in range(count)],
        currency="USD",
        period="1D",
        source="benchmark",
        observations=scenarios,
    )
    cap = max(0.05, 2.0 / count)
    constraints = ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(0.0, cap))
    return ConstructionProblem(
        covariance, MinimumCVaR(scenario_set, 0.95), constraints, SolverSettings(1e-9, 1e-9, 500)
    )


def _solve(problem: ConstructionProblem, count: int) -> float:
    start = time.perf_counter()
    result = construct(problem)
    solved = time.perf_counter() - start
    if result.status is not ConstructionStatus.OPTIMAL:
        raise SystemExit(f"{count} assets: {result.status.name}: {result.diagnostics.detail}")
    print(
        f"  {count:5d} assets, {round(2.52 * count)} scenarios: constructed in {solved:.2f}s "
        f"({result.diagnostics.iterations} iterations, {result.diagnostics.method})"
    )
    return solved


def run_budget() -> None:
    print(f"WS-12's budget: {BUDGET_ASSETS} assets, {round(2.52 * BUDGET_ASSETS)} scenarios")
    solved = _solve(_problem(BUDGET_ASSETS), BUDGET_ASSETS)
    verdict = "within" if solved <= BUDGET_SECONDS else "over"
    print(f"  {solved:.0f}s against {BUDGET_SECONDS:.0f}s: {verdict} the budget")


def run_benchmark() -> None:
    print("Minimum-CVaR construction over scenarios, through the certified linear program")
    problems = {count: _problem(count) for count in UNIVERSES}
    for count in UNIVERSES:
        _solve(problems[count], count)
    small, large = UNIVERSES
    small_cpu, large_cpu = fastest(
        [lambda: construct(problems[small]), lambda: construct(problems[large])]
    )
    growth = large_cpu / max(small_cpu, 1e-9)
    print(
        f"  judged (CPU time, collector paused, fastest of {SAMPLES}): {large} assets cost "
        f"{growth:.1f}x {small} (ceiling {MAX_GROWTH:.0f}x)"
    )
    if growth > MAX_GROWTH:
        raise SystemExit(
            f"CVaR construction grew {growth:.1f}x over a {large // small}x universe; the ceiling "
            f"is {MAX_GROWTH:.0f}x."
        )


if __name__ == "__main__":
    if "--budget" in sys.argv[1:]:
        run_budget()
    else:
        run_benchmark()
