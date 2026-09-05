"""Global immutable state container for the Replay Engine.

``system_events`` is an :class:`~alphalab.common.append_log.AppendOnlyLog`, not a
tuple. The replay cursor appends one ``ReplayAdvanced`` per record, and rebuilding
a tuple on every append made a replay cost O(N^2) in the records it had already
read -- the defect v2.1 removed from the risk engine and v2.2 from the OMS, left
behind on the very path v2.2 wired into execution. The log appends in O(1)
amortized and still compares equal to the tuple it replaced.
"""

from dataclasses import dataclass, field
from enum import Enum, auto

from alphalab.common.append_log import AppendOnlyLog
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
    system_events: AppendOnlyLog[ReplaySystemEvent] = field(default_factory=AppendOnlyLog)

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
