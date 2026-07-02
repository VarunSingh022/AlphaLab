"""AlphaLab Analytics & Performance Engine."""

from alphalab.analytics.attribution import AttributionMetrics, TradeRecord, calculate_attribution
from alphalab.analytics.drawdown import DrawdownMetrics, calculate_drawdowns
from alphalab.analytics.engine import AnalyticsEngine, PortfolioSnapshot
from alphalab.analytics.events import AnalyticsEvent, ReportGenerated
from alphalab.analytics.exceptions import AnalyticsError, AnalyticsValidationError
from alphalab.analytics.exposure import ExposureMetrics, calculate_exposure
from alphalab.analytics.metrics import (
    calmar_ratio,
    conditional_var,
    sharpe_ratio,
    sortino_ratio,
    value_at_risk,
)
from alphalab.analytics.report import PerformanceReport, ReturnSummary, RiskSummary
from alphalab.analytics.returns import (
    annualized_volatility,
    arithmetic_return,
    cagr,
    geometric_return,
    total_return,
)
from alphalab.analytics.rolling import rolling_return, rolling_sharpe, rolling_volatility
from alphalab.analytics.state import AnalyticsState
from alphalab.analytics.summary import TradeMetrics, calculate_trade_metrics
from alphalab.analytics.validation import validate_capital, validate_returns
from alphalab.analytics.views import (
    all_reports,
    latest_performance_summary,
    total_reports_generated,
)

__all__ = [
    "AnalyticsEngine",
    "AnalyticsError",
    "AnalyticsEvent",
    "AnalyticsState",
    "AnalyticsValidationError",
    "AttributionMetrics",
    "DrawdownMetrics",
    "ExposureMetrics",
    "PerformanceReport",
    "PortfolioSnapshot",
    "ReportGenerated",
    "ReturnSummary",
    "RiskSummary",
    "TradeMetrics",
    "TradeRecord",
    "all_reports",
    "annualized_volatility",
    "arithmetic_return",
    "cagr",
    "calculate_attribution",
    "calculate_drawdowns",
    "calculate_exposure",
    "calculate_trade_metrics",
    "calmar_ratio",
    "conditional_var",
    "geometric_return",
    "latest_performance_summary",
    "rolling_return",
    "rolling_sharpe",
    "rolling_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "total_reports_generated",
    "total_return",
    "validate_capital",
    "validate_returns",
    "value_at_risk",
]
