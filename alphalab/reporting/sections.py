"""Immutable components for building report layouts."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class ReportSectionType(Enum):
    """Types of visual or structured data representation in a report."""

    TEXT = auto()
    METRICS = auto()
    TABLE = auto()
    CHART_METADATA = auto()


@dataclass(frozen=True, slots=True)
class ReportSection:
    """Immutable fragment of a report containing categorized data."""

    name: str
    section_type: ReportSectionType
    content: Any  # Can be str, list of dicts, or flat dict
    description: str = ""
