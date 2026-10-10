"""Seeded bootstrap of the Sharpe ratio.

Until v3.12 the report also carried a ``confidence_score`` -- the 5th
percentile over the median, plus 0.001, times 100, clipped to [0, 100] -- which
measured nothing a reader could check (ledger RES-001). The percentiles are the
measurement.
"""

import random
from dataclasses import dataclass

from alphalab.research.metrics import calculate_sharpe
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    """Percentiles of the resampled Sharpe ratio; ``None`` with fewer than two returns.

    Attributes:
        metric: What was resampled.
        iterations: How many resamples were drawn.
        lower_bound_5th: The 5th percentile.
        median_50th: The median.
        upper_bound_95th: The 95th percentile.
    """

    metric: str
    iterations: int
    lower_bound_5th: float | None
    median_50th: float | None
    upper_bound_95th: float | None


def bootstrap_statistics(
    payload: ResearchPayload, seed: int, iterations: int = 1000
) -> BootstrapReport:
    """Samples returns with replacement, ``iterations`` times, seeded by ``seed``."""

    if len(payload.returns) < 2:
        return BootstrapReport("Sharpe", 0, None, None, None)

    prng = random.Random(seed)
    results = []
    n = len(payload.returns)

    for _ in range(iterations):
        sample = prng.choices(payload.returns, k=n)
        results.append(calculate_sharpe(sample, payload.periods_per_year, payload.risk_free_rate))

    results.sort()
    return BootstrapReport(
        "Sharpe",
        iterations,
        results[int(iterations * 0.05)],
        results[int(iterations * 0.50)],
        results[int(iterations * 0.95)],
    )
