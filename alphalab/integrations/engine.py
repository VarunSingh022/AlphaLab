"""Top-level Engine Facade orchestrating Broker Integrations."""

from typing import Any

from alphalab.integrations.config import BrokerConfig
from alphalab.integrations.manager import IntegrationManager
from alphalab.integrations.protocol import IntegrationProviderProtocol
from alphalab.integrations.registry import BrokerRegistry
from alphalab.integrations.state import IntegrationState


class IntegrationEngine:
    """Facade for managing deterministic cross-broker integration workflows."""

    @staticmethod
    def initialize(engine_id: str) -> IntegrationState:
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return IntegrationState(engine_id=engine_id)

    @staticmethod
    def register(state: IntegrationState, config: BrokerConfig) -> IntegrationState:
        return BrokerRegistry.register(state, config)

    @staticmethod
    def authenticate(
        state: IntegrationState,
        broker_id: str,
        provider: IntegrationProviderProtocol,
        creds: dict[str, str],
        ts: float,
    ) -> IntegrationState:
        return IntegrationManager.authenticate(state, broker_id, provider, creds, ts)

    @staticmethod
    def connect(
        state: IntegrationState, broker_id: str, provider: IntegrationProviderProtocol, ts: float
    ) -> IntegrationState:
        return IntegrationManager.connect(state, broker_id, provider, ts)

    @staticmethod
    def disconnect(
        state: IntegrationState, broker_id: str, provider: IntegrationProviderProtocol, ts: float
    ) -> IntegrationState:
        return IntegrationManager.disconnect(state, broker_id, provider, ts)

    @staticmethod
    def submit_order(
        state: IntegrationState,
        broker_id: str,
        provider: IntegrationProviderProtocol,
        order: dict[str, Any],
        ts: float,
    ) -> IntegrationState:
        return IntegrationManager.submit_order(state, broker_id, provider, order, ts)

    @staticmethod
    def sync_portfolio(
        state: IntegrationState, broker_id: str, provider: IntegrationProviderProtocol, ts: float
    ) -> IntegrationState:
        return IntegrationManager.sync_portfolio(state, broker_id, provider, ts)
