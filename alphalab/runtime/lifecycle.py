"""Lifecycle state definitions for the Runtime."""

from enum import Enum, auto


class RuntimeStatus(Enum):
    """Explicit, pure state machine stages for the Live Trading Runtime."""

    CREATED = auto()
    INITIALIZED = auto()
    STARTING = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()
    FAILED = auto()
