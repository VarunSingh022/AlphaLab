"""Immutable definitions for chronological dimensions."""

from enum import Enum, auto


class TimeFrequency(Enum):
    TICK = auto()
    SECOND = auto()
    MINUTE = auto()
    HOURLY = auto()
    DAILY = auto()
    WEEKLY = auto()
    MONTHLY = auto()
    CUSTOM = auto()
