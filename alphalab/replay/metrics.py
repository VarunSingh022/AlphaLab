"""Immutable metrics tracking for Replay performance and progress."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ReplayMetrics:
    """Tracks the progress and throughput of an active replay session."""

    events_processed: int
    total_events: int
    elapsed_replay_time: float
    elapsed_real_time: float

    @property
    def remaining_events(self) -> int:
        """Calculates the number of events left in the sequence."""
        return self.total_events - self.events_processed

    @property
    def completion_ratio(self) -> Decimal:
        """Calculates the percentage of the replay session completed."""
        if self.total_events == 0:
            return Decimal("1.0000")
        ratio = self.events_processed / self.total_events
        return Decimal(str(ratio)).quantize(Decimal("0.0001"))

    @property
    def throughput(self) -> float:
        """Calculates events processed per second of real wall-clock time."""
        if self.elapsed_real_time <= 0.0:
            return 0.0
        return self.events_processed / self.elapsed_real_time
