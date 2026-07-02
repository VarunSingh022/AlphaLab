"""Schedule definitions and enumerations."""

from enum import Enum, auto


class ScheduleType(Enum):
    """Types of schedules and triggers supported by the engine."""

    ONE_SHOT = auto()
    REPEATING = auto()
    INTERVAL = auto()
    CRON = auto()
    MANUAL = auto()
    SESSION_OPEN = auto()
    SESSION_CLOSE = auto()
    BAR_BOUNDARY = auto()
