"""Immutable report models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto

from alphalab.reporting.sections import ReportSection


class ReportType(Enum):
    """Standardized report classifications."""

    PERFORMANCE = auto()
    RISK = auto()
    PORTFOLIO = auto()
    TRADE = auto()
    EXECUTION = auto()
    OPTIMIZATION = auto()
    RUNTIME = auto()


@dataclass(frozen=True, slots=True)
class Report:
    """Immutable aggregate document encompassing structured presentation data."""

    report_id: str
    title: str
    timestamp: float
    report_type: ReportType
    sections: tuple[ReportSection, ...]
    summary: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)
