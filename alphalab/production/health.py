"""Deterministic health evaluation criteria."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemHealth:
    """Immutable aggregation of cluster health evaluations."""
    score: float
    cpu_usage_logical: float
    memory_usage_logical: float
    queue_backlog: int
    event_throughput: float
    broker_connected: bool
    market_connected: bool
    is_healthy: bool

def compute_health_score(
    cpu: float, mem: float, backlog: int, broker_up: bool, market_up: bool
) -> float:
    """Deterministically evaluates a health grade between 0.0 and 100.0."""
    score = 100.0
    if not broker_up:
        score -= 40.0
    if not market_up:
        score -= 30.0
        
    score -= min(30.0, cpu * 0.3)
    score -= min(30.0, mem * 0.3)
    score -= min(20.0, backlog * 0.1)
    
    return max(0.0, round(score, 2))