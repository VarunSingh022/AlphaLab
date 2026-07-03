"""Immutable dashboard presentation models."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DashboardCard:
    """Immutable metric card definition for a dashboard."""

    title: str
    value: str
    subtitle: str = ""


@dataclass(frozen=True, slots=True)
class DashboardTable:
    """Immutable tabular data definition for a dashboard."""

    title: str
    columns: tuple[str, ...]
    data_ref: str  # Reference to the data source key


@dataclass(frozen=True, slots=True)
class DashboardChart:
    """Immutable charting metadata for a dashboard (no rendering logic)."""

    title: str
    chart_type: str  # e.g., 'line', 'bar', 'scatter'
    data_ref: str


@dataclass(frozen=True, slots=True)
class DashboardSection:
    """Immutable layout grouping for dashboard widgets."""

    title: str
    cards: tuple[DashboardCard, ...] = field(default_factory=tuple)
    tables: tuple[DashboardTable, ...] = field(default_factory=tuple)
    charts: tuple[DashboardChart, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Dashboard:
    """Immutable aggregate representing a full dashboard layout."""

    dashboard_id: str
    title: str
    timestamp: float
    sections: tuple[DashboardSection, ...]
