"""Immutable definitions for data timeframes."""

from enum import Enum, auto


class Timeframe(Enum):
    TICK = auto()
    SECOND = auto()
    MINUTE = auto()
    HOURLY = auto()
    DAILY = auto()
