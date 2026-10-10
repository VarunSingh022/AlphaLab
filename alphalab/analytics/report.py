"""Aggregated Performance Report Models.

v3.10 changed what a report *says* about its own basis, because a ratio whose
annualization cannot be read off the report cannot be compared with anything:

* returns are taken between **one equity point per instant** (the last one at
  each timestamp), not between every portfolio snapshot -- a multi-asset run
  records several per instant, and the returns between them are not returns;
* ``period_returns`` replaces ``daily_returns``: the periods are whatever the
  run's instants are, and nothing made them days;
* ``periods_per_year`` is recorded with :class:`Periodicity`, saying whether it
  was declared by the caller or observed from the equity curve;
* ``years_elapsed`` is the curve's own span unless the caller declared one --
  it defaulted to ``1.0``, so every run's CAGR assumed it lasted a year;
* every statistic that is undefined is ``None`` (see
  :mod:`alphalab.analytics.metrics`).
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from alphalab.analytics.attribution import AttributionMetrics
from alphalab.analytics.drawdown import DrawdownMetrics
from alphalab.analytics.exposure import ExposureMetrics
from alphalab.analytics.summary import TradeMetrics


class Periodicity(StrEnum):
    """Where a report's periods-per-year figure came from."""

    #: Stated by the caller (``RunConfig.periods_per_year``).
    DECLARED = "DECLARED"
    #: Observed: the number of return periods divided by the years they span.
    OBSERVED = "OBSERVED"
    #: The curve spans no time, so no annualization is defined.
    UNDEFINED = "UNDEFINED"
    #: Assumed by the writer rather than declared or observed. Only a report
    #: written before v3.10 carries it: those annualized every run with 252.
    ASSUMED = "ASSUMED"


@dataclass(frozen=True, slots=True)
class RiskSummary:
    """Annualized risk figures, each ``None`` where undefined.

    Attributes:
        risk_free_rate: The annual risk-free rate the Sharpe and Sortino ratios
            are in excess of, recorded so a ratio can be read with it. ``None``
            on a report written before v3.10, which did not record it.
    """

    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    value_at_risk_95: float | None
    cvar_95: float | None
    annualized_volatility: float | None
    risk_free_rate: float | None = None


@dataclass(frozen=True, slots=True)
class ReturnSummary:
    """Return figures, and the basis they were computed on.

    Attributes:
        period_returns: Simple returns between consecutive instants of the
            equity curve, one equity point per instant.
        periods_per_year: How many of those periods make a year -- declared or
            observed, per :attr:`periodicity` -- or ``None`` when undefined.
        periodicity: Where :attr:`periods_per_year` came from.
        years_elapsed: The span CAGR was compounded over: declared, or the
            curve's first to last instant in years of 365.25 days.
    """

    total_return: float | None
    cagr: float | None
    arithmetic_return: float | None
    geometric_return: float | None
    period_returns: tuple[float, ...]
    periods_per_year: float | None = None
    periodicity: Periodicity = Periodicity.UNDEFINED
    years_elapsed: float | None = None


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Immutable aggregate document encompassing all computed portfolio analytics."""

    report_id: str
    timestamp: float
    returns: ReturnSummary
    risk: RiskSummary
    drawdowns: DrawdownMetrics
    exposure: ExposureMetrics
    trades: TradeMetrics
    attribution: AttributionMetrics
    ending_capital: Decimal
