"""AlphaLab Reporting Layer.

Reports and their exports. The dashboard layouts this package held until v3.12
-- cards, tables and charts arranged for a screen -- were presentation, which is
the host application's (ledger SCF-003, ADR-0047); a report's exports are the
data a presentation is built from.
"""

from alphalab.reporting.adapter import ReportingAdapter
from alphalab.reporting.engine import ReportingEngine
from alphalab.reporting.events import (
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
from alphalab.reporting.validation import validate_report
from alphalab.reporting.views import (
    export_statistics,
    get_export,
    latest_report,
    report_count,
)

__all__ = [
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
    "export_csv",
    "export_json",
    "export_markdown",
    "export_statistics",
    "get_export",
    "latest_report",
    "report_count",
    "validate_report",
]
