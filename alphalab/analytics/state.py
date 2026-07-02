"""Global immutable state container for the Analytics Engine."""

from dataclasses import dataclass, field

from alphalab.analytics.events import AnalyticsEvent
from alphalab.analytics.report import PerformanceReport


@dataclass(frozen=True, slots=True)
class AnalyticsState:
    """Deterministic snapshot of generated reports and analytical events."""

    reports: tuple[PerformanceReport, ...] = field(default_factory=tuple)
    events: tuple[AnalyticsEvent, ...] = field(default_factory=tuple)
