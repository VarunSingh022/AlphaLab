"""Aggregated Performance Report Models."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.analytics.attribution import AttributionMetrics
from alphalab.analytics.drawdown import DrawdownMetrics
from alphalab.analytics.exposure import ExposureMetrics
from alphalab.analytics.summary import TradeMetrics


@dataclass(frozen=True, slots=True)
class RiskSummary:
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    value_at_risk_95: float
    cvar_95: float
    annualized_volatility: float


@dataclass(frozen=True, slots=True)
class ReturnSummary:
    total_return: float
    cagr: float
    arithmetic_return: float
    geometric_return: float
    daily_returns: tuple[float, ...]


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
