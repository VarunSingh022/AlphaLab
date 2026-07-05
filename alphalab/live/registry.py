"""Stateless registry manipulations for providers and connections."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.live.connection import ConnectionState
from alphalab.live.events import ProviderConnected, ProviderDisconnected, ProviderRegistered
from alphalab.live.exceptions import InvalidLiveStateError
from alphalab.live.provider import Provider
from alphalab.live.state import LiveState
from alphalab.live.validation import validate_provider_registration


class LiveRegistry:
    """Stateless dictionary transformations for the Live infrastructure."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def register_provider(state: LiveState, provider: Provider, timestamp: float) -> LiveState:
        """Adds a new provider and establishes its default disconnected state."""
        validate_provider_registration(state, provider)

        new_providers = dict(state.providers)
        new_providers[provider.provider_id] = provider

        new_connections = dict(state.connections)
        new_connections[provider.provider_id] = ConnectionState(provider.provider_id)

        evt = ProviderRegistered(
            LiveRegistry._create_id(), timestamp, provider.provider_id, provider.vendor
        )

        return replace(
            state,
            providers=new_providers,
            connections=new_connections,
            events=(*state.events, evt),
        )

    @staticmethod
    def connect_provider(state: LiveState, provider_id: str, timestamp: float) -> LiveState:
        """Transitions a provider's connection state to Connected."""
        if provider_id not in state.connections:
            raise InvalidLiveStateError(f"Provider {provider_id} not registered.")

        conn = state.connections[provider_id]
        if conn.connected:
            return state

        new_conn = replace(conn, connected=True, last_heartbeat=timestamp)
        new_connections = dict(state.connections)
        new_connections[provider_id] = new_conn

        evt = ProviderConnected(LiveRegistry._create_id(), timestamp, provider_id)

        return replace(state, connections=new_connections, events=(*state.events, evt))

    @staticmethod
    def disconnect_provider(
        state: LiveState, provider_id: str, reason: str, timestamp: float
    ) -> LiveState:
        """Transitions a provider's connection state to Disconnected."""
        if provider_id not in state.connections:
            raise InvalidLiveStateError(f"Provider {provider_id} not registered.")

        conn = state.connections[provider_id]
        if not conn.connected:
            return state

        new_conn = replace(conn, connected=False)
        new_connections = dict(state.connections)
        new_connections[provider_id] = new_conn

        evt = ProviderDisconnected(LiveRegistry._create_id(), timestamp, provider_id, reason)

        return replace(state, connections=new_connections, events=(*state.events, evt))
