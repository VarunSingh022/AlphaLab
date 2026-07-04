"""Pure queries exposing transparent Integration State access."""

from collections.abc import Sequence

from alphalab.integrations.auth import AuthState
from alphalab.integrations.broker import BrokerHealth
from alphalab.integrations.config import BrokerConfig
from alphalab.integrations.connection import ConnectionState
from alphalab.integrations.state import IntegrationMetrics, IntegrationState


def broker_summary(state: IntegrationState) -> Sequence[BrokerConfig]:
    return tuple(state.configs.values())

def connection_status(state: IntegrationState, broker_id: str) -> ConnectionState | None:
    return state.connections.get(broker_id)

def broker_health(state: IntegrationState, broker_id: str) -> BrokerHealth | None:
    return state.health.get(broker_id)

def authentication_status(state: IntegrationState, broker_id: str) -> AuthState | None:
    return state.auth_states.get(broker_id)

def metrics_report(state: IntegrationState) -> IntegrationMetrics:
    return state.metrics