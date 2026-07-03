"""AlphaLab Reporting Layer."""

from alphalab.reporting.adapter import ReportingAdapter
from alphalab.reporting.dashboard import (
    Dashboard,
    DashboardCard,
    DashboardChart,
    DashboardSection,
    DashboardTable,
)
from alphalab.reporting.engine import ReportingEngine
from alphalab.reporting.events import (
    DashboardGenerated,
    ExportCompleted,
    ExportFailed,
    ReportGenerated,
    ReportingEvent,
)
from alphalab.reporting.exceptions import ExportError, ReportingError, ReportingValidationError
from alphalab.reporting.export import export_csv, export_json, export_markdown
from alphalab.reporting.protocol import ExportProtocol
from alphalab.reporting.report import Report, ReportType
from alphalab.reporting.sections import ReportSection, ReportSectionType
from alphalab.reporting.state import ReportingState, ReportingStatistics
from alphalab.reporting.validation import validate_dashboard, validate_report
from alphalab.reporting.views import (
    dashboard_summary,
    export_statistics,
    get_export,
    latest_report,
    report_count,
)

__all__ = [
    "Dashboard",
    "DashboardCard",
    "DashboardChart",
    "DashboardGenerated",
    "DashboardSection",
    "DashboardTable",
    "ExportCompleted",
    "ExportError",
    "ExportFailed",
    "ExportProtocol",
    "Report",
    "ReportGenerated",
    "ReportSection",
    "ReportSectionType",
    "ReportType",
    "ReportingAdapter",
    "ReportingEngine",
    "ReportingError",
    "ReportingEvent",
    "ReportingState",
    "ReportingStatistics",
    "ReportingValidationError",
    "dashboard_summary",
    "export_csv",
    "export_json",
    "export_markdown",
    "export_statistics",
    "get_export",
    "latest_report",
    "report_count",
    "validate_dashboard",
    "validate_report",
]
