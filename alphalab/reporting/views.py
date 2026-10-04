"""Pure queries exposing transparent Reporting Engine access."""

from alphalab.reporting.report import Report
from alphalab.reporting.state import ReportingState, ReportingStatistics


def latest_report(state: ReportingState) -> Report | None:
    """Returns the most recently generated report by timestamp."""
    if not state.reports:
        return None
    return max(state.reports.values(), key=lambda r: r.timestamp)


def report_count(state: ReportingState) -> int:
    """Returns the total number of synthesized reports stored in state."""
    return len(state.reports)


def export_statistics(state: ReportingState) -> ReportingStatistics:
    """Returns global reporting metrics including generation and export counts."""
    return state.statistics


def get_export(state: ReportingState, report_id: str, format_type: str) -> str | None:
    """Retrieves the generated raw export string if it exists."""
    export_key = f"{report_id}.{format_type.lower()}"
    return state.exports.get(export_key)
