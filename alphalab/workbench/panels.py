"""Immutable definitions for dockable UI sections."""

from dataclasses import dataclass
from enum import Enum, auto


class PanelType(Enum):
    DASHBOARD = auto()
    PROJECTS = auto()
    DATASETS = auto()
    STRATEGIES = auto()
    RESEARCH = auto()
    EXPERIMENTS = auto()
    BACKTESTS = auto()
    PORTFOLIO = auto()
    MARKET_DATA = auto()
    PRODUCTION = auto()
    BROKERS = auto()
    REPORTS = auto()
    SETTINGS = auto()

@dataclass(frozen=True, slots=True)
class Panel:
    """Immutable representation of a dockable UI component."""
    panel_id: str
    panel_type: PanelType
    is_visible: bool = True
    is_docked: bool = True
    width_ratio: float = 0.2