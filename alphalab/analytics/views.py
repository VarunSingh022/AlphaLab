"""Pure queries exposing transparent Analytics Engine access."""

from alphalab.analytics.report import PerformanceReport
from alphalab.analytics.state import AnalyticsState


def latest_performance_summary(state: AnalyticsState) -> PerformanceReport | None:
    """Returns the most recently generated full performance report."""
    if not state.reports:
        return None
    return state.reports[-1]


def all_reports(state: AnalyticsState) -> tuple[PerformanceReport, ...]:
    """Returns the immutable sequence of all historical reports."""
    return state.reports


def total_reports_generated(state: AnalyticsState) -> int:
    """Returns the count of analytics reports compiled."""
    return len(state.reports)
