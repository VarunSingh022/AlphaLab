"""Macro-specific enumerations."""

from enum import Enum, auto


class Frequency(Enum):
    """How often an economic indicator is reported."""

    DAILY = auto()
    WEEKLY = auto()
    MONTHLY = auto()
    QUARTERLY = auto()
    ANNUAL = auto()


class PolicyAction(Enum):
    """The direction of a central bank policy rate decision."""

    HIKE = auto()
    CUT = auto()
    HOLD = auto()
