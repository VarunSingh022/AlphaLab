"""Immutable tracking metrics for the Runtime."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeMetrics:
    """Immutable representation of runtime performance and health."""
    events_processed: int = 0
    total_dispatch_latency: float = 0.0
    error_count: int = 0
    heartbeat_count: int = 0
    uptime_seconds: float = 0.0

    @property
    def events_per_second(self) -> float:
        """Calculates processed events per second of uptime."""
        if self.uptime_seconds <= 0.0:
            return 0.0
        return self.events_processed / self.uptime_seconds

    @property
    def average_dispatch_latency(self) -> float:
        """Calculates the mean processing time per event."""
        if self.events_processed == 0:
            return 0.0
        return self.total_dispatch_latency / self.events_processed