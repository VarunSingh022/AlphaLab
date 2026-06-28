"""Event priority definitions."""

from enum import IntEnum, unique


@unique
class EventPriority(IntEnum):
    """Deterministic event processing priority.

    Lower values are processed before higher values.
    """

    SYSTEM = 0
    MARKET = 10
    SIGNAL = 20
    PORTFOLIO = 30
    ORDER = 40
    EXECUTION = 50
    FILL = 60
    RISK = 70
    ANALYTICS = 80
    LOGGING = 90
