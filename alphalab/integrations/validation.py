"""Strict validation rules for broker state and transitions."""

from alphalab.integrations.config import BrokerConfig
from alphalab.integrations.exceptions import ConnectionManagerError, IntegrationValidationError
from alphalab.integrations.state import IntegrationState


def validate_registration(state: IntegrationState, config: BrokerConfig) -> None:
    if not config.broker_id.strip():
        raise IntegrationValidationError("Broker ID cannot be empty.")
    if config.broker_id in state.configs:
        raise IntegrationValidationError(f"Broker {config.broker_id} is already registered.")


def validate_connection_attempt(state: IntegrationState, broker_id: str) -> None:
    if broker_id not in state.configs:
        raise IntegrationValidationError(f"Broker {broker_id} not registered.")
    conn = state.connections.get(broker_id)
    if conn and conn.status.name == "CONNECTED":
        raise ConnectionManagerError(f"Broker {broker_id} is already connected.")
