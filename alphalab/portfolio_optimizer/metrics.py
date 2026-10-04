"""Immutable metrics tracking and deterministic mathematical evaluators."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.common.statistics import compounded_max_drawdown, sample_variance


@dataclass(frozen=True, slots=True)
class PortfolioMetrics:
    portfolio_id: str
    timestamp: float
    total_return: float
    annual_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    turnover: float
    diversification_ratio: float


#: The largest fall of the compounded path from its running peak -- one
#: implementation, in :mod:`alphalab.common.statistics`, shared with
#: ``alphalab.research`` since v3.13 (ledger API-001).
calculate_max_drawdown = compounded_max_drawdown


def calculate_volatility(returns: Sequence[float], periods: int) -> float:
    """Annualized volatility, over the one shared unbiased estimator.

    ``periods`` is how many returns make a year. Required since v3.10: it
    defaulted to 252, a daily series' count, whatever the series was
    (ledger API-003).

    ``sqrt(var * periods)`` rather than ``sqrt(var) * sqrt(periods)``: the two
    are equal in exact arithmetic and not always in floating point, and this
    module has always used the first. Changing it would move published numbers
    for no reason.
    """
    if len(returns) < 2:
        return 0.0
    return math.sqrt(sample_variance(returns) * periods)
