"""Immutable events related to Analytics generation."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class AnalyticsEvent(BaseEvent):
    """Base class for all Analytics system events."""

    pass


@dataclass(frozen=True, slots=True)
class ReportGenerated(AnalyticsEvent):
    """Emitted when a new performance report is compiled."""

    report_id: str
    num_snapshots: int
    num_trades: int
