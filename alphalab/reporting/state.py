"""Global immutable state container for the Reporting Engine.

The indexes and the event log use the canonical containers from
:mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them. Until
v2.17 every mutator here rebuilt a whole ``dict`` and a whole ``tuple`` per
transition, so ``N`` transitions copied ``O(N^2)`` entries -- ADR-0032 category C
finding 1, closed by ADR-0034. Both containers are immutable, both define value
equality, and a ``PersistentMap`` iterates in first-insertion order of the keys
still present, which is what a ``dict`` does.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    reports: PersistentMap[str, Report] = field(default_factory=PersistentMap)
    dashboards: PersistentMap[str, Dashboard] = field(default_factory=PersistentMap)
    exports: PersistentMap[str, str] = field(default_factory=PersistentMap)
    statistics: ReportingStatistics = field(default_factory=ReportingStatistics)
    events: AppendOnlyLog[ReportingEvent] = field(default_factory=AppendOnlyLog)
    metadata: Mapping[str, str] = field(default_factory=dict)
