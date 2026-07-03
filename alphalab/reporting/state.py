"""Global immutable state container for the Reporting Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.reporting.dashboard import Dashboard
from alphalab.reporting.events import ReportingEvent
from alphalab.reporting.report import Report


@dataclass(frozen=True, slots=True)
class ReportingStatistics:
    """Immutable tracking metrics for the Reporting engine."""

    total_reports_generated: int = 0
    total_dashboards_generated: int = 0
    total_exports_completed: int = 0
    total_exports_failed: int = 0


@dataclass(frozen=True, slots=True)
class ReportingState:
    """Deterministic snapshot of generated reports and dashboard definitions."""

    engine_id: str
    reports: Mapping[str, Report] = field(default_factory=dict)
    dashboards: Mapping[str, Dashboard] = field(default_factory=dict)
    exports: Mapping[str, str] = field(default_factory=dict)
    statistics: ReportingStatistics = field(default_factory=ReportingStatistics)
    events: tuple[ReportingEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
