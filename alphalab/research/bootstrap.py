"""Deterministic Bootstrap Confidence Intervals."""

import random
from dataclasses import dataclass

from alphalab.research.metrics import calculate_sharpe
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    metric: str
    lower_bound_5th: float
    median_50th: float
    upper_bound_95th: float
    confidence_score: float


def bootstrap_statistics(
    payload: ResearchPayload, iterations: int = 1000, seed: int = 42
) -> BootstrapReport:
    """Samples returns with replacement to build metric confidence intervals."""
    if not payload.returns:
        return BootstrapReport("Sharpe", 0.0, 0.0, 0.0, 0.0)

    prng = random.Random(seed)
    results = []
    n = len(payload.returns)

    for _ in range(iterations):
        sample = prng.choices(payload.returns, k=n)
        results.append(calculate_sharpe(sample))

    results.sort()
    p5 = results[int(iterations * 0.05)]
    p50 = results[int(iterations * 0.50)]
    p95 = results[int(iterations * 0.95)]

    # Confidence Score: Higher if 5th percentile is still strongly positive
    conf_score = max(0.0, min(100.0, (p5 / (p50 + 0.001)) * 100.0))

    return BootstrapReport(
        "Sharpe", round(p5, 4), round(p50, 4), round(p95, 4), round(conf_score, 2)
    )
