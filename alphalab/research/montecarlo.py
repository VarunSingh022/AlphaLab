"""Seeded Monte Carlo reordering of a return series, and the drawdowns it produces.

Until v3.12 a path counted as "ruin" past a hard-coded 20% drawdown (ledger
RES-001). The bound is now the caller's
(:attr:`~alphalab.research.research.ResearchPolicy.ruin_drawdown`).
"""

import random
from dataclasses import dataclass

from alphalab.research.metrics import calculate_max_drawdown
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class MonteCarloReport:
    """The maximum-drawdown distribution over reordered paths.

    Attributes:
        simulations: How many reorderings were drawn; ``0`` without returns.
        median_drawdown: The median path's maximum drawdown.
        percentile_95_drawdown: The 95th percentile.
        worst_drawdown: The worst path's.
        ruin_drawdown: The bound a path's drawdown was judged against.
        ruin_probability: The fraction of paths past that bound.
    """

    simulations: int
    median_drawdown: float | None
    percentile_95_drawdown: float | None
    worst_drawdown: float | None
    ruin_drawdown: float
    ruin_probability: float | None


def monte_carlo_simulation(
    payload: ResearchPayload, seed: int, ruin_drawdown: float, simulations: int = 1000
) -> MonteCarloReport:
    """Reorders the returns ``simulations`` times, seeded by ``seed``."""

    if not payload.returns:
        return MonteCarloReport(0, None, None, None, ruin_drawdown, None)

    prng = random.Random(seed)
    base_returns = list(payload.returns)
    drawdowns = []
    for _ in range(simulations):
        prng.shuffle(base_returns)
        drawdowns.append(calculate_max_drawdown(base_returns))

    drawdowns.sort()
    return MonteCarloReport(
        simulations=simulations,
        median_drawdown=drawdowns[len(drawdowns) // 2],
        percentile_95_drawdown=drawdowns[int(simulations * 0.95)],
        worst_drawdown=drawdowns[-1],
        ruin_drawdown=ruin_drawdown,
        ruin_probability=sum(1 for dd in drawdowns if dd > ruin_drawdown) / simulations,
    )
