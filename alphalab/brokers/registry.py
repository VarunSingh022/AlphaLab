"""Stateless registry manipulations for broker endpoints and accounts."""

from dataclasses import replace

from alphalab.broker.account import BrokerAccount
from alphalab.brokers.connection import BrokerConnection
from alphalab.brokers.events import BrokerConnected, BrokerDisconnected, BrokerRegistered
from alphalab.brokers.exceptions import InvalidBrokerStateError
from alphalab.brokers.state import BrokerConnectorState
from alphalab.brokers.validation import validate_account, validate_broker_registration
from alphalab.common.ids import new_id
from alphalab.common.registry import with_mapping_item


class BrokerRegistry:
    """Stateless transforms for adding and linking brokers and accounts."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def register_broker(
        state: BrokerConnectorState, connection: BrokerConnection, timestamp: float
    ) -> BrokerConnectorState:
        """Validates and registers a new broker configuration."""
        validate_broker_registration(state, connection)

        new_connections = with_mapping_item(state.connections, connection.broker_id, connection)

        evt = BrokerRegistered(
            BrokerRegistry._create_id(),
            timestamp,
            connection.broker_id,
            connection.broker_type.name,
        )

        return replace(state, connections=new_connections, events=(*state.events, evt))

    @staticmethod
    def connect_broker(
        state: BrokerConnectorState, broker_id: str, timestamp: float
    ) -> BrokerConnectorState:
        """Marks a registered broker as connected."""
        if broker_id not in state.connections:
            raise InvalidBrokerStateError(f"Broker '{broker_id}' not found.")

        conn = state.connections[broker_id]
        if conn.connected:
            return state

        new_conn = replace(conn, connected=True, last_heartbeat=timestamp)
        new_connections = with_mapping_item(state.connections, broker_id, new_conn)

        evt = BrokerConnected(BrokerRegistry._create_id(), timestamp, broker_id)

        return replace(state, connections=new_connections, events=(*state.events, evt))

    @staticmethod
    def disconnect_broker(
        state: BrokerConnectorState, broker_id: str, reason: str, timestamp: float
    ) -> BrokerConnectorState:
        """Marks a connected broker as disconnected."""
        if broker_id not in state.connections:
            raise InvalidBrokerStateError(f"Broker '{broker_id}' not found.")

        conn = state.connections[broker_id]
        if not conn.connected:
            return state

        new_conn = replace(conn, connected=False)
        new_connections = with_mapping_item(state.connections, broker_id, new_conn)

        evt = BrokerDisconnected(BrokerRegistry._create_id(), timestamp, broker_id, reason)

        return replace(state, connections=new_connections, events=(*state.events, evt))

    @staticmethod
    def add_account(state: BrokerConnectorState, account: BrokerAccount) -> BrokerConnectorState:
        """Links a financial account to a registered broker."""
        validate_account(state, account.account_id, account.broker_id)

        new_accounts = with_mapping_item(state.accounts, account.account_id, account)

        return replace(state, accounts=new_accounts)
