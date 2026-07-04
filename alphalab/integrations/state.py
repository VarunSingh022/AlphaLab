"""Global immutable state container for the Integration Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.integrations.auth import AuthState
from alphalab.integrations.broker import BrokerHealth
from alphalab.integrations.config import BrokerConfig
from alphalab.integrations.connection import ConnectionState
from alphalab.integrations.events import IntegrationEvent


@dataclass(frozen=True, slots=True)
class IntegrationMetrics:
    """Immutable tracking metrics for remote interactions."""

    orders_submitted: int = 0
    orders_rejected: int = 0
    executions_processed: int = 0
    avg_latency_ms: float = 0.0
    total_reconnects: int = 0
    api_errors: int = 0


@dataclass(frozen=True, slots=True)
class IntegrationState:
    """Deterministic snapshot of all active broker integrations."""

    engine_id: str
    configs: Mapping[str, BrokerConfig] = field(default_factory=dict)
    auth_states: Mapping[str, AuthState] = field(default_factory=dict)
    connections: Mapping[str, ConnectionState] = field(default_factory=dict)
    health: Mapping[str, BrokerHealth] = field(default_factory=dict)
    metrics: IntegrationMetrics = field(default_factory=IntegrationMetrics)
    events: tuple[IntegrationEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
