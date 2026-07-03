"""Immutable domain events describing the Reporting Engine lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReportingEvent:
    """Base class for all Reporting system events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class ReportGenerated(ReportingEvent):
    """Emitted when a new report is successfully synthesized and stored."""

    report_id: str
    report_type: str


@dataclass(frozen=True, slots=True)
class DashboardGenerated(ReportingEvent):
    """Emitted when a dashboard layout is successfully synthesized."""

    dashboard_id: str


@dataclass(frozen=True, slots=True)
class ExportCompleted(ReportingEvent):
    """Emitted when a report is successfully exported to an external format."""

    report_id: str
    export_format: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class ExportFailed(ReportingEvent):
    """Emitted when an export process fails."""

    report_id: str
    export_format: str
    reason: str
