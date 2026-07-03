"""Immutable interface protocol for Reporting engines."""

from typing import Protocol

from alphalab.reporting.report import Report


class ExportProtocol(Protocol):
    """Pure functional interface defining export behaviors."""

    def export(self, report: Report) -> str:
        """Serializes the report to a specific string format."""
        ...
