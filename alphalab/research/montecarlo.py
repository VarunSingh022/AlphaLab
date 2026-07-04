"""Seeded deterministic Monte Carlo path evaluations."""

import random
from dataclasses import dataclass

from alphalab.research.metrics import calculate_max_drawdown
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class MonteCarloReport:
    simulations: int
    median_drawdown: float
    worst_drawdown: float
    ruin_probability: float


def monte_carlo_simulation(
    payload: ResearchPayload, simulations: int = 1000, seed: int = 42
) -> MonteCarloReport:
    """Resamples returns deterministically to estimate extreme drawdown risks."""
    if not payload.returns:
        return MonteCarloReport(0, 0.0, 0.0, 0.0)

    prng = random.Random(seed)
    base_returns = list(payload.returns)
    drawdowns = []
    ruin_count = 0

    for _ in range(simulations):
        prng.shuffle(base_returns)
        dd = calculate_max_drawdown(base_returns)
        drawdowns.append(dd)
        if dd > 0.20:  # 20% ruin threshold
            ruin_count += 1

    drawdowns.sort()
    median_dd = drawdowns[len(drawdowns) // 2]
    worst_dd = drawdowns[-1]
    ruin_prob = ruin_count / simulations

    return MonteCarloReport(
        simulations, round(median_dd, 4), round(worst_dd, 4), round(ruin_prob, 4)
    )
