"""Stateless registry manipulations for market data providers."""

import uuid
from dataclasses import replace

from alphalab.marketdata.config import ProviderConfig
from alphalab.marketdata.connection import ConnectionState, ConnectionStatus
from alphalab.marketdata.events import ProviderRegistered
from alphalab.marketdata.state import MarketDataState, ProviderMetrics
from alphalab.marketdata.validation import validate_provider_registration


class ProviderRegistry:
    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def register(
        state: MarketDataState, config: ProviderConfig, timestamp: float
    ) -> MarketDataState:
        validate_provider_registration(state, config)

        new_providers = dict(state.providers)
        new_providers[config.provider_id] = config

        new_conns = dict(state.connections)
        new_conns[config.provider_id] = ConnectionState(
            config.provider_id, ConnectionStatus.DISCONNECTED
        )

        new_metrics = dict(state.metrics)
        new_metrics[config.provider_id] = ProviderMetrics()

        evt = ProviderRegistered(ProviderRegistry._create_id(), timestamp, config.provider_id)

        return replace(
            state,
            providers=new_providers,
            connections=new_conns,
            metrics=new_metrics,
            events=(*state.events, evt),
        )
