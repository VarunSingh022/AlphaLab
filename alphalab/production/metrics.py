"""Immutable metrics collection tracking runtime efficiency."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeMetrics:
    """Operational counters for the production cluster."""

    processed_events: int = 0
    orders_routed: int = 0
    trades_executed: int = 0
    avg_latency_ms: float = 0.0
    heartbeats_received: int = 0
    heartbeats_missed: int = 0
    broker_uptime_seconds: float = 0.0
    total_recoveries: int = 0
    total_checkpoints: int = 0
