"""Pure functional Reporting Engine orchestrating synthesis and exports."""

import uuid
from dataclasses import replace

from alphalab.reporting.dashboard import Dashboard
from alphalab.reporting.events import (
    DashboardGenerated,
    ExportCompleted,
    ExportFailed,
    ReportGenerated,
)
from alphalab.reporting.exceptions import ReportingError
from alphalab.reporting.export import export_csv, export_json, export_markdown
from alphalab.reporting.report import Report
from alphalab.reporting.state import ReportingState
from alphalab.reporting.validation import validate_dashboard, validate_report


class ReportingEngine:
    """Facade orchestrating pure functional state machine logic for Reporting."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def initialize(engine_id: str) -> ReportingState:
        """Constructs an empty base state for the reporting layer."""
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return ReportingState(engine_id=engine_id)

    @staticmethod
    def register_report(state: ReportingState, report: Report) -> ReportingState:
        """Validates and immutably registers a new Report into the state."""
        validate_report(state, report)

        evt = ReportGenerated(
            event_id=ReportingEngine._create_id(),
            timestamp=report.timestamp,
            report_id=report.report_id,
            report_type=report.report_type.name,
        )

        new_reports = dict(state.reports)
        new_reports[report.report_id] = report

        new_stats = replace(
            state.statistics,
            total_reports_generated=state.statistics.total_reports_generated + 1,
        )

        return replace(
            state,
            reports=new_reports,
            statistics=new_stats,
            events=(*state.events, evt),
        )

    @staticmethod
    def register_dashboard(state: ReportingState, dashboard: Dashboard) -> ReportingState:
        """Validates and immutably registers a new Dashboard into the state."""
        validate_dashboard(state, dashboard)

        evt = DashboardGenerated(
            event_id=ReportingEngine._create_id(),
            timestamp=dashboard.timestamp,
            dashboard_id=dashboard.dashboard_id,
        )

        new_dashboards = dict(state.dashboards)
        new_dashboards[dashboard.dashboard_id] = dashboard

        new_stats = replace(
            state.statistics,
            total_dashboards_generated=state.statistics.total_dashboards_generated + 1,
        )

        return replace(
            state,
            dashboards=new_dashboards,
            statistics=new_stats,
            events=(*state.events, evt),
        )

    @staticmethod
    def export_report(
        state: ReportingState, report_id: str, format_type: str, timestamp: float
    ) -> ReportingState:
        """Generates an export payload and stores it deterministically."""
        if report_id not in state.reports:
            raise ReportingError(f"Cannot export: Report {report_id} not found.")

        report = state.reports[report_id]
        fmt = format_type.upper()

        try:
            if fmt == "JSON":
                output = export_json(report)
            elif fmt == "CSV":
                output = export_csv(report)
            elif fmt == "MARKDOWN":
                output = export_markdown(report)
            else:
                raise ValueError(f"Unsupported export format: {format_type}")

            byte_size = len(output.encode("utf-8"))
            evt = ExportCompleted(
                event_id=ReportingEngine._create_id(),
                timestamp=timestamp,
                report_id=report_id,
                export_format=fmt,
                byte_size=byte_size,
            )

            export_key = f"{report_id}.{fmt.lower()}"
            new_exports = dict(state.exports)
            new_exports[export_key] = output

            new_stats = replace(
                state.statistics,
                total_exports_completed=state.statistics.total_exports_completed + 1,
            )

            return replace(
                state, exports=new_exports, statistics=new_stats, events=(*state.events, evt)
            )

        except Exception as e:
            fail_evt = ExportFailed(
                event_id=ReportingEngine._create_id(),
                timestamp=timestamp,
                report_id=report_id,
                export_format=fmt,
                reason=str(e),
            )
            new_stats = replace(
                state.statistics,
                total_exports_failed=state.statistics.total_exports_failed + 1,
            )
            return replace(state, statistics=new_stats, events=(*state.events, fail_evt))
