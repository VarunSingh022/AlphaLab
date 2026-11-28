"""The numerical foundation's linear algebra at scale (v4.3, ADR-0058, ledger LIM-010).

The core computes in pure Python, so its budgets are stated and measured here.
The plan's budget for WS-03 is an eigensystem of a 500 x 500 matrix within
30 s. Cyclic Jacobi took 147.5 s for it on the reference machine (CPython 3.12,
Apple arm64); Householder tridiagonalization and implicit QL, which the core
uses, took 20.7 s. The thin SVD stays one-sided Jacobi for its accuracy and
has no budget in the plan. It took 240.8 s at 500 x 500, which is why it is
reported here at 200, as is the 500 x 500 Cholesky factorization.

What this guards: every method is ``O(n^3)``, so doubling ``n`` should cost
about 8x. Each doubling may cost at most ``MAX_GROWTH`` times, judged through
``_stable_timing``. A step that turned ``O(n^4)`` would cost 16x and fail. The
absolute times are printed against the budget, not gated: shared runners are
too noisy to gate on (``.github/workflows/benchmarks.yml``).
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import growth

from alphalab.common.linalg import cholesky, singular_value_decomposition, symmetric_eigensystem

#: The plan's WS-03 budget for a 500 x 500 eigensystem, in seconds.
EIGEN_BUDGET_SECONDS = 30.0
#: Ceiling on a doubling of ``n``; ``O(n^3)`` is 8x.
MAX_GROWTH = 12.0
TOLERANCE = 1e-14
LIMIT = 60


def _symmetric(size: int) -> list[list[float]]:
    rng = random.Random(size)
    draws = [[rng.uniform(-1.0, 1.0) for _ in range(size)] for _ in range(size)]
    return [[(draws[i][j] + draws[j][i]) / 2.0 for j in range(size)] for i in range(size)]


def _positive(size: int) -> list[list[float]]:
    rng = random.Random(size + 1)
    draws = [[rng.gauss(0.0, 1.0) for _ in range(size)] for _ in range(size + 10)]
    return [
        [sum(row[i] * row[j] for row in draws) + (1.0 if i == j else 0.0) for j in range(size)]
        for i in range(size)
    ]


def _eigen(size: int) -> object:
    return symmetric_eigensystem(_symmetric(size), tolerance=TOLERANCE, max_iterations=LIMIT)


def _svd(size: int) -> object:
    return singular_value_decomposition(_symmetric(size), tolerance=TOLERANCE, max_sweeps=LIMIT)


def main() -> int:
    failures = 0

    matrix = _symmetric(500)
    start = time.perf_counter()
    symmetric_eigensystem(matrix, tolerance=TOLERANCE, max_iterations=LIMIT)
    elapsed = time.perf_counter() - start
    print(
        f"eigensystem 500 x 500: {elapsed:.1f} s (budget {EIGEN_BUDGET_SECONDS:.0f} s on the "
        "reference machine)"
    )

    positive = _positive(500)
    start = time.perf_counter()
    cholesky(positive)
    print(f"cholesky 500 x 500: {time.perf_counter() - start:.1f} s")

    start = time.perf_counter()
    _svd(200)
    print(f"singular value decomposition 200 x 200: {time.perf_counter() - start:.1f} s")

    for name, work, small, large in (
        ("eigensystem", _eigen, 100, 200),
        ("singular value decomposition", _svd, 60, 120),
    ):
        ratio = growth(work, small, large)
        verdict = "ok" if ratio <= MAX_GROWTH else "FAIL"
        print(f"{name} {small} -> {large}: {ratio:.1f}x (ceiling {MAX_GROWTH:.0f}x) {verdict}")
        failures += ratio > MAX_GROWTH
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
