"""Deterministic structured logging models."""

from dataclasses import dataclass
from enum import Enum, auto


class LogLevel(Enum):
    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()


@dataclass(frozen=True, slots=True)
class LogEntry:
    """Immutable structured log record."""

    timestamp: float
    level: LogLevel
    module_id: str
    message: str
    metadata: str = ""
