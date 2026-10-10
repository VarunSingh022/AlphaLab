"""Deterministic mathematical operations for research metrics.

The v1 research engine's metrics. Until v3.12 they assumed a **daily** return
series: annualization defaulted to 252 periods, and :func:`calculate_sharpe`
annualized with 252 whatever it was given (ledger RES-001). The periods a year
holds are now stated by the caller -- carried on the
:class:`~alphalab.research.protocol.ResearchPayload` -- and so is the risk-free
rate, exactly as the canonical analytics record the periodicity and the rate a
:class:`~alphalab.analytics.report.PerformanceReport` used.
"""

import math
from collections.abc import Sequence

from alphalab.common.statistics import compounded_max_drawdown, sample_variance
from alphalab.research.exceptions import ResearchValidationError


def _periods(periods_per_year: int) -> int:
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int):
        raise ResearchValidationError(
            f"periods_per_year is {periods_per_year!r}; it is a whole number of periods."
        )
    if periods_per_year <= 0:
        raise ResearchValidationError(
            f"periods_per_year is {periods_per_year}; a year holds a positive number of periods."
        )
    return periods_per_year


def calculate_cagr(returns: Sequence[float], periods_per_year: int) -> float:
    """Compound annual growth of ``returns``, a year being ``periods_per_year`` of them."""

    periods = _periods(periods_per_year)
    if not returns:
        return 0.0
    cumulative = 1.0
    for r in returns:
        cumulative *= 1.0 + r
    if cumulative <= 0:
        return -1.0
    years = len(returns) / periods
    growth: float = cumulative ** (1.0 / years)
    return growth - 1.0


#: The largest fall of the compounded path from its running peak -- one
#: implementation, in :mod:`alphalab.common.statistics`, shared with
#: ``alphalab.portfolio_optimizer`` since v3.13 (ledger API-001).
calculate_max_drawdown = compounded_max_drawdown


def calculate_volatility(returns: Sequence[float], periods_per_year: int) -> float:
    """Annualized standard deviation, over the one shared unbiased estimator."""

    periods = _periods(periods_per_year)
    if len(returns) < 2:
        return 0.0
    return math.sqrt(sample_variance(returns)) * math.sqrt(periods)


def calculate_sharpe(
    returns: Sequence[float], periods_per_year: int, risk_free_rate: float
) -> float:
    """Annualized excess mean over annualized volatility; ``0.0`` without dispersion.

    Both the mean and the volatility are annualized with ``periods_per_year``
    and ``risk_free_rate`` is an annual rate.
    """

    periods = _periods(periods_per_year)
    if not math.isfinite(risk_free_rate):
        raise ResearchValidationError(f"risk_free_rate is {risk_free_rate!r}; a rate is finite.")
    vol = calculate_volatility(returns, periods)
    if vol == 0.0:
        return 0.0
    mean_return = (sum(returns) / len(returns)) * periods
    return (mean_return - risk_free_rate) / vol
