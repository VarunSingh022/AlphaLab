"""What the econometrics package's tests cost at the plan's scale (v4.3, ADR-0074 section 9).

The plan budgets Johansen's test at two seconds for five series in pure Python
(plan WS-07 item 15). Ten years of daily data are 2,520 points; the VECM here
has two lagged differences and a restricted constant, so each regression
partials out ten lagged differences. The ceiling is judged through
``_stable_timing``: CPU time, collector paused, fastest of several.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import SAMPLES, fastest

from alphalab.econometrics import FixedLags, JohansenCase, johansen

#: The plan's budget for Johansen with five series, in CPU seconds.
JOHANSEN_BUDGET_SECONDS = 2.0
POINTS = 2_520
SERIES = 5


def _system() -> list[list[float]]:
    """Five walks, two of them tied to the first: a system of rank two."""

    draws = random.Random(20261116)
    common = [0.0]
    for _ in range(POINTS - 1):
        common.append(common[-1] + draws.gauss(0.0, 1.0))
    system = [common]
    for weight in (0.8, 1.6):
        system.append([weight * value + draws.gauss(0.0, 1.0) for value in common])
    for _ in range(SERIES - 3):
        walk = [0.0]
        for _ in range(POINTS - 1):
            walk.append(walk[-1] + draws.gauss(0.0, 1.0))
        system.append(walk)
    return system


def run_benchmark() -> int:
    print(f"Starting Econometrics Benchmark (Johansen, {SERIES} series of {POINTS} points)...")
    system = _system()

    def test() -> object:
        return johansen(
            system,
            case=JohansenCase.RESTRICTED_CONSTANT,
            lags=FixedLags(2),
            tolerance=1e-15,
            max_iterations=60,
            maximum_condition=1e10,
        )

    result = test()
    (seconds,) = fastest([test])
    verdict = "ok" if seconds <= JOHANSEN_BUDGET_SECONDS else "OVER THE PLAN'S BUDGET"
    traces = ", ".join(f"{rank.trace:.1f}" for rank in result.ranks)  # type: ignore[attr-defined]
    print(
        f"  Johansen: {seconds:.3f} CPU s (budget {JOHANSEN_BUDGET_SECONDS:.0f} s), "
        f"trace statistics {traces} {verdict}"
    )
    print(f"  judged: CPU time, collector paused, fastest of {SAMPLES}")
    return 0 if verdict == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
