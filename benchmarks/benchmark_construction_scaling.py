"""Portfolio construction over a growing universe, through its factor structure (PRF-005).

Until v3.12 every construction program was solved by the dense dual active-set
method: ``O(n^2)`` per step over a number of steps that grows with the
universe. Measured on this workload, a long-only mean-variance construction
under a five-factor covariance took 1.5 s at 200 assets, 13 s at 400 and 142 s
at 800. A covariance built by ``CovarianceMatrix.factor_model`` now carries its
structure, and from ``FACTOR_STRUCTURED_MINIMUM_ASSETS`` assets construction
solves it in ``O(n k^2)`` per step.

What this guards: the construction of a 4x universe may cost at most
``MAX_GROWTH`` times the small one. The dense solve grows about 90x over that
span; what remains linear-algebra-free in a construction -- the covariance's
identity and its Euler decomposition, both ``O(n^2)`` in the dense matrix every
consumer reads -- grows 16x. Building the factor-model covariance itself is
reported, not judged: it is the matrix, not the solve.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import SAMPLES, fastest

from alphalab.analytics.risk_model import CovarianceMatrix, FactorLoadings
from alphalab.portfolio_optimizer.construction import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    ExpectedReturns,
    ExposureRange,
    MeanVariance,
    SolverSettings,
    WeightBounds,
    construct,
)

UNIVERSES = (200, 400, 800)
FACTORS = ("f0", "f1", "f2", "f3", "f4")
#: Ceiling on the 800-asset construction over the 200-asset one.
MAX_GROWTH = 30.0


def _problem(count: int) -> ConstructionProblem:
    rng = random.Random(7)
    assets = [f"A{index:05d}" for index in range(count)]
    loadings = FactorLoadings.of(
        {asset: {factor: rng.gauss(0, 1) for factor in FACTORS} for asset in assets},
        source="benchmark model",
        lineage=dict.fromkeys(FACTORS, "synthetic"),
        as_of=None,
    )
    factor_covariance = CovarianceMatrix.from_rows(
        FACTORS,
        [[0.04 if i == j else 0.0 for j in range(len(FACTORS))] for i in range(len(FACTORS))],
        currency="USD",
        period="1D",
        source="benchmark",
        observations=None,
    )
    covariance = CovarianceMatrix.factor_model(
        loadings, factor_covariance, {asset: 0.01 + 0.02 * rng.random() for asset in assets}
    )
    returns = random.Random(11)
    expected = ExpectedReturns(
        {asset: returns.gauss(0.05, 0.10) for asset in assets}, "USD", "1D", "benchmark"
    )
    return ConstructionProblem(
        covariance,
        MeanVariance(expected, 1.0),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(0.05)),
        SolverSettings(1e-9, 1e-9, 100_000),
    )


def run_benchmark() -> None:
    print("Construction scaling benchmark: long-only mean-variance, five-factor covariance")
    problems = {}
    for count in UNIVERSES:
        start = time.perf_counter()
        problems[count] = _problem(count)
        built = time.perf_counter() - start
        start = time.perf_counter()
        result = construct(problems[count])
        solved = time.perf_counter() - start
        if result.status is not ConstructionStatus.OPTIMAL:
            raise SystemExit(f"{count} assets: {result.status.name}: {result.diagnostics.detail}")
        print(
            f"  {count:5d} assets: covariance built in {built:.2f}s, constructed in {solved:.2f}s "
            f"({result.diagnostics.iterations} steps)"
        )

    small, large = UNIVERSES[0], UNIVERSES[-1]
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
            f"Construction grew {growth:.1f}x over a {large // small}x universe; the ceiling is "
            f"{MAX_GROWTH:.0f}x."
        )


if __name__ == "__main__":
    run_benchmark()
