"""Strict validation rules for report synthesis."""

from alphalab.reporting.exceptions import ReportingValidationError
from alphalab.reporting.report import Report
from alphalab.reporting.state import ReportingState


def validate_report(state: ReportingState, report: Report) -> None:
    """Ensures structural integrity and uniqueness of a synthesized report."""
    if not report.report_id.strip():
        raise ReportingValidationError("Report ID cannot be empty.")

    if report.report_id in state.reports:
        raise ReportingValidationError(f"Duplicate Report ID detected: {report.report_id}")

    if not report.sections:
        raise ReportingValidationError("Report must contain at least one section.")

    seen_sections = set()
    for section in report.sections:
        if not section.name.strip():
            raise ReportingValidationError("Section name cannot be empty.")
        if section.name in seen_sections:
            raise ReportingValidationError(f"Duplicate section name detected: {section.name}")
        seen_sections.add(section.name)
