"""Adapter translating external engine structs to Reporting abstractions."""

from typing import Any

from alphalab.reporting.report import Report, ReportType
from alphalab.reporting.sections import ReportSection, ReportSectionType


class ReportingAdapter:
    """Stateless translator mapping generic outputs to formal Report models."""

    @staticmethod
    def extract_flat_metrics(data: Any) -> dict[str, Any]:
        """Safely extracts dictionary attributes from dataclasses or mappings."""
        if isinstance(data, dict):
            return data
        if hasattr(data, "__dict__"):
            # Avoid private or magic attributes
            return {k: v for k, v in data.__dict__.items() if not k.startswith("_")}
        if hasattr(data, "__slots__"):
            return {k: getattr(data, k) for k in data.__slots__}
        return {}

    @staticmethod
    def generic_performance_report(
        report_id: str, timestamp: float, title: str, metrics: Any
    ) -> Report:
        """Converts generic performance metrics into a Performance Report."""
        extracted = ReportingAdapter.extract_flat_metrics(metrics)

        section = ReportSection(
            name="Key Metrics",
            section_type=ReportSectionType.METRICS,
            content=extracted,
            description="Aggregated performance and risk statistics.",
        )

        return Report(
            report_id=report_id,
            title=title,
            timestamp=timestamp,
            report_type=ReportType.PERFORMANCE,
            sections=(section,),
            summary="Automated performance summary report.",
        )
