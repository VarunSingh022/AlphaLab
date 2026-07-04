"""Stateless registry manipulations for broker configurations."""

from dataclasses import replace

from alphalab.integrations.auth import AuthState, AuthStatus
from alphalab.integrations.broker import BrokerHealth
from alphalab.integrations.config import BrokerConfig
from alphalab.integrations.connection import ConnectionState, ConnectionStatus
from alphalab.integrations.state import IntegrationState
from alphalab.integrations.validation import validate_registration


class BrokerRegistry:
    """Stateless dictionary transformations for integration configs."""

    @staticmethod
    def register(state: IntegrationState, config: BrokerConfig) -> IntegrationState:
        validate_registration(state, config)

        new_configs = dict(state.configs)
        new_configs[config.broker_id] = config

        new_auths = dict(state.auth_states)
        new_auths[config.broker_id] = AuthState(config.broker_id, AuthStatus.UNAUTHENTICATED)

        new_conns = dict(state.connections)
        new_conns[config.broker_id] = ConnectionState(
            config.broker_id, ConnectionStatus.DISCONNECTED
        )

        new_health = dict(state.health)
        new_health[config.broker_id] = BrokerHealth(
            config.broker_id, 0.0, 0, False, config.rate_limit_per_second
        )

        return replace(
            state,
            configs=new_configs,
            auth_states=new_auths,
            connections=new_conns,
            health=new_health,
        )
