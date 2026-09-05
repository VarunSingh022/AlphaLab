"""Top-level Engine Facade orchestrating all Broker Connector components."""

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.manager import OrderManager
from alphalab.brokers.registry import BrokerRegistry
from alphalab.brokers.state import BrokerConnectorState


class BrokerConnectorEngine:
    """Facade for managing deterministic multi-broker orchestration state."""

    @staticmethod
    def initialize(engine_id: str) -> BrokerConnectorState:
        """Constructs an empty base state for the broker layer."""
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return BrokerConnectorState(engine_id=engine_id)

    @staticmethod
    def register_broker(
        state: BrokerConnectorState, connection: BrokerConnection, timestamp: float
    ) -> BrokerConnectorState:
        return BrokerRegistry.register_broker(state, connection, timestamp)

    @staticmethod
    def connect_broker(
        state: BrokerConnectorState, broker_id: str, timestamp: float
    ) -> BrokerConnectorState:
        return BrokerRegistry.connect_broker(state, broker_id, timestamp)

    @staticmethod
    def disconnect_broker(
        state: BrokerConnectorState, broker_id: str, reason: str, timestamp: float
    ) -> BrokerConnectorState:
        return BrokerRegistry.disconnect_broker(state, broker_id, reason, timestamp)

    @staticmethod
    def add_account(state: BrokerConnectorState, account: BrokerAccount) -> BrokerConnectorState:
        return BrokerRegistry.add_account(state, account)

    @staticmethod
    def submit_order(
        state: BrokerConnectorState, order: BrokerOrder, timestamp: float
    ) -> BrokerConnectorState:
        return OrderManager.submit_order(state, order, timestamp)

    @staticmethod
    def cancel_order(
        state: BrokerConnectorState, broker_order_id: str, timestamp: float
    ) -> BrokerConnectorState:
        return OrderManager.cancel_order(state, broker_order_id, timestamp)

    @staticmethod
    def process_execution(
        state: BrokerConnectorState, execution: BrokerExecution, timestamp: float
    ) -> BrokerConnectorState:
        return OrderManager.process_execution(state, execution, timestamp)
