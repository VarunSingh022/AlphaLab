"""Orchestration of connection, authentication, and routing states."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.common.ids import new_id
from alphalab.integrations.auth import AuthState, AuthStatus
from alphalab.integrations.connection import ConnectionState, ConnectionStatus
from alphalab.integrations.events import (
    AuthenticationFailed,
    AuthenticationSucceeded,
    BrokerConnected,
    BrokerDisconnected,
    IntegrationEvent,
    OrderAccepted,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PortfolioSynchronized,
)
from alphalab.integrations.exceptions import ConnectionManagerError
from alphalab.integrations.protocol import IntegrationProviderProtocol
from alphalab.integrations.state import IntegrationState
from alphalab.integrations.validation import validate_connection_attempt


class IntegrationManager:
    """Facade for managing immutable remote state interactions."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def authenticate(
        state: IntegrationState,
        broker_id: str,
        provider: IntegrationProviderProtocol,
        creds: dict[str, str],
        ts: float,
    ) -> IntegrationState:
        if broker_id not in state.configs:
            raise ConnectionManagerError("Broker not registered.")

        success = provider.authenticate(creds)
        new_auths = dict(state.auth_states)

        evt: IntegrationEvent
        if success:
            new_auths[broker_id] = AuthState(broker_id, AuthStatus.AUTHENTICATED, ts + 3600.0)
            evt = AuthenticationSucceeded(IntegrationManager._create_id(), ts, broker_id)
        else:
            new_auths[broker_id] = AuthState(broker_id, AuthStatus.FAILED)
            evt = AuthenticationFailed(
                IntegrationManager._create_id(), ts, broker_id, "Invalid credentials"
            )

        return replace(state, auth_states=new_auths, events=(*state.events, evt))

    @staticmethod
    def connect(
        state: IntegrationState, broker_id: str, provider: IntegrationProviderProtocol, ts: float
    ) -> IntegrationState:
        validate_connection_attempt(state, broker_id)

        auth = state.auth_states.get(broker_id)
        if not auth or auth.status != AuthStatus.AUTHENTICATED:
            raise ConnectionManagerError("Must authenticate before connecting.")

        success = provider.connect()
        new_conns = dict(state.connections)

        if success:
            new_conns[broker_id] = ConnectionState(broker_id, ConnectionStatus.CONNECTED, ts)
            evt = BrokerConnected(IntegrationManager._create_id(), ts, broker_id)
            return replace(state, connections=new_conns, events=(*state.events, evt))

        return state

    @staticmethod
    def disconnect(
        state: IntegrationState, broker_id: str, provider: IntegrationProviderProtocol, ts: float
    ) -> IntegrationState:
        new_conns = dict(state.connections)
        new_conns[broker_id] = ConnectionState(broker_id, ConnectionStatus.DISCONNECTED, ts)
        provider.disconnect()
        evt = BrokerDisconnected(IntegrationManager._create_id(), ts, broker_id, "Manual")
        return replace(state, connections=new_conns, events=(*state.events, evt))

    @staticmethod
    def submit_order(
        state: IntegrationState,
        broker_id: str,
        provider: IntegrationProviderProtocol,
        order: dict[str, Any],
        ts: float,
    ) -> IntegrationState:
        conn = state.connections.get(broker_id)
        if not conn or conn.status != ConnectionStatus.CONNECTED:
            raise ConnectionManagerError("Broker disconnected.")

        evt_sub = OrderSubmitted(IntegrationManager._create_id(), ts, broker_id, order["order_id"])
        evt_result: IntegrationEvent
        try:
            resp = provider.submit_order(order)
            if resp.get("status") == "ACCEPTED":
                evt_result = OrderAccepted(
                    IntegrationManager._create_id(),
                    ts,
                    broker_id,
                    order["order_id"],
                    resp.get("remote_id", ""),
                )
                new_mets = replace(
                    state.metrics, orders_submitted=state.metrics.orders_submitted + 1
                )

            elif resp.get("status") == "FILLED":
                # Paper brokers might fill instantly

                evt_result = OrderFilled(
                    IntegrationManager._create_id(),
                    ts,
                    broker_id,
                    order["order_id"],
                    Decimal(str(resp.get("filled_qty", "0"))),
                    Decimal(str(resp.get("price", "0"))),
                )
                new_mets = replace(
                    state.metrics,
                    orders_submitted=state.metrics.orders_submitted + 1,
                    executions_processed=state.metrics.executions_processed + 1,
                )
            else:
                evt_result = OrderRejected(
                    IntegrationManager._create_id(),
                    ts,
                    broker_id,
                    order["order_id"],
                    resp.get("reason", "Unknown"),
                )
                new_mets = replace(state.metrics, orders_rejected=state.metrics.orders_rejected + 1)
        except Exception as e:
            evt_result = OrderRejected(
                IntegrationManager._create_id(), ts, broker_id, order["order_id"], str(e)
            )
            new_mets = replace(
                state.metrics,
                orders_rejected=state.metrics.orders_rejected + 1,
                api_errors=state.metrics.api_errors + 1,
            )

        return replace(state, metrics=new_mets, events=(*state.events, evt_sub, evt_result))

    @staticmethod
    def sync_portfolio(
        state: IntegrationState, broker_id: str, provider: IntegrationProviderProtocol, ts: float
    ) -> IntegrationState:
        resp = provider.sync_portfolio()
        drift = resp.get("drift_detected", False)
        evt = PortfolioSynchronized(IntegrationManager._create_id(), ts, broker_id, drift)
        return replace(state, events=(*state.events, evt))
