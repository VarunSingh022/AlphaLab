"""Immutable clock abstractions for deterministic time handling."""

import time
from dataclasses import dataclass
from typing import Protocol


class ClockProtocol(Protocol):
    """Interface for obtaining the current time deterministically."""

    def now(self) -> float: ...


@dataclass(frozen=True, slots=True)
class ClockState:
    """Immutable representation of a clock's current moment in time."""

    current_time: float


@dataclass(frozen=True, slots=True)
class VirtualClock:
    """A deterministic clock driven entirely by external state progression."""

    _current_time: float

    def now(self) -> float:
        return self._current_time


@dataclass(frozen=True, slots=True)
class BacktestClock:
    """A clock specific to backtesting, functionally identical to VirtualClock."""

    _current_time: float

    def now(self) -> float:
        return self._current_time


class SystemClock:
    """A live monotonic system clock. Explicitly excluded from deterministic replays."""

    def now(self) -> float:
        return time.time()
