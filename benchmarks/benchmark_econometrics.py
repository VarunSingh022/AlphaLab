"""What the econometrics package's estimators cost at the plan's scale (v4.3, ADR-0074 s. 9).

The plan budgets, in pure Python (plan WS-07 item 15), ten years of daily data
being 2,520 points:

* **Johansen's test** at two seconds for five series. The VECM here has two
  lagged differences and a restricted constant, so each regression partials out
  ten lagged differences.
* **A GARCH fit** at five seconds for 2,520 returns: Gaussian likelihood, BFGS
  with central differences, and the curvature's standard errors.

Each ceiling is judged through ``_stable_timing``: CPU time, collector paused,
fastest of several.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import SAMPLES, fastest

from alphalab.econometrics import (
    FixedLags,
    JohansenCase,
    VolatilityModel,
    VolatilityStart,
    fit_volatility,
    johansen,
)

#: The plan's budget for Johansen with five series, in CPU seconds.
JOHANSEN_BUDGET_SECONDS = 2.0
#: The plan's budget for a GARCH fit to 2,520 returns, in CPU seconds.
GARCH_BUDGET_SECONDS = 5.0
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


def _returns() -> list[float]:
    """2,520 returns from a GARCH(1, 1): omega 0.05, alpha 0.08, beta 0.9."""

    draws = random.Random(7)
    values, variance, last = [], 0.05 / 0.02, 0.0
    for _ in range(POINTS + 500):
        variance = 0.05 + 0.08 * last * last + 0.9 * variance
        last = variance**0.5 * draws.gauss(0.0, 1.0)
        values.append(last)
    return values[-POINTS:]


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

    returns = _returns()

    def garch() -> object:
        return fit_volatility(
            returns,
            model=VolatilityModel.GARCH,
            start=VolatilityStart.MOMENTS,
            tolerance=1e-6,
            max_iterations=200,
            step=1e-5,
        )

    result = test()
    fitted = garch()
    seconds, garch_seconds = fastest([test, garch])
    failures = 0
    verdict = "ok" if seconds <= JOHANSEN_BUDGET_SECONDS else "OVER THE PLAN'S BUDGET"
    failures += verdict != "ok"
    traces = ", ".join(f"{rank.trace:.1f}" for rank in result.ranks)  # type: ignore[attr-defined]
    print(
        f"  Johansen: {seconds:.3f} CPU s (budget {JOHANSEN_BUDGET_SECONDS:.0f} s), "
        f"trace statistics {traces} {verdict}"
    )
    verdict = "ok" if garch_seconds <= GARCH_BUDGET_SECONDS else "OVER THE PLAN'S BUDGET"
    failures += verdict != "ok"
    parameters = fitted.estimate.parameters  # type: ignore[attr-defined]
    print(
        f"  GARCH(1, 1) on {POINTS} returns: {garch_seconds:.3f} CPU s (budget "
        f"{GARCH_BUDGET_SECONDS:.0f} s), alpha {parameters['alpha']:.3f}, beta "
        f"{parameters['beta']:.3f} {verdict}"
    )
    print(f"  judged: CPU time, collector paused, fastest of {SAMPLES}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
