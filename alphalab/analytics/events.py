"""Immutable events related to Analytics generation."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AnalyticsEvent:
    """Base class for all Analytics system events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class ReportGenerated(AnalyticsEvent):
    """Emitted when a new performance report is compiled."""

    report_id: str
    num_snapshots: int
    num_trades: int
