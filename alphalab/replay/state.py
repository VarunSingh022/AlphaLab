"""Global immutable state container for the Replay Engine."""

from dataclasses import dataclass, field
from enum import Enum, auto

from alphalab.replay.events import ReplaySystemEvent
from alphalab.replay.loader import HistoricalEventProtocol
from alphalab.replay.metrics import ReplayMetrics
from alphalab.replay.session import ReplaySession


class ReplayStatus(Enum):
    """Explicit pure lifecycle states of the Replay Engine."""

    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    COMPLETED = auto()


@dataclass(frozen=True, slots=True)
class ReplayState:
    """Deterministic snapshot of an active historical replay sequence."""

    session: ReplaySession
    status: ReplayStatus
    events: tuple[HistoricalEventProtocol, ...]
    current_index: int
    current_timestamp: float
    real_start_time: float
    real_current_time: float
    system_events: tuple[ReplaySystemEvent, ...] = field(default_factory=tuple)

    @property
    def metrics(self) -> ReplayMetrics:
        """Dynamically computes replay metrics on read."""
        elapsed_real = max(0.0, self.real_current_time - self.real_start_time)
        return ReplayMetrics(
            events_processed=self.current_index,
            total_events=len(self.events),
            elapsed_replay_time=self.current_timestamp - self.session.start_time,
            elapsed_real_time=elapsed_real,
        )
