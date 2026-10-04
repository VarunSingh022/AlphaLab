"""Immutable metrics tracking and deterministic mathematical evaluators.

Until v3.13 this module also exported its own ``calculate_volatility``: the
research metric of the same name, with another keyword (``periods``), no check
that a year holds a positive number of periods, and another rounding order.
One name had two implementations of one contract (ledger API-001); the
duplicate is removed, and annualized volatility is
:func:`alphalab.research.calculate_volatility` or
:func:`alphalab.analytics.annualized_volatility`.
"""

from dataclasses import dataclass

from alphalab.common.statistics import compounded_max_drawdown


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
