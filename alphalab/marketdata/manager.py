"""Orchestration of connections and subscriptions."""

import uuid
from dataclasses import replace

from alphalab.marketdata.connection import ConnectionState, ConnectionStatus
from alphalab.marketdata.events import (
    ProviderConnected,
    ProviderDisconnected,
    SubscriptionCreated,
    SubscriptionRemoved,
)
from alphalab.marketdata.exceptions import InvalidMarketStateError
from alphalab.marketdata.protocol import MarketDataProtocol
from alphalab.marketdata.state import MarketDataState
from alphalab.marketdata.subscription import Subscription, SubscriptionStatus
from alphalab.marketdata.timeframe import Timeframe


class ConnectionManager:
    @staticmethod
    def _create_id() -> str: return str(uuid.uuid4())

    @staticmethod
    def connect(
        state: MarketDataState, provider_id: str, provider: MarketDataProtocol, ts: float
    ) -> MarketDataState:
        if provider_id not in state.providers:
            raise InvalidMarketStateError("Provider not registered.")
            
        success = provider.connect()
        if not success:
            return state
            
        new_conns = dict(state.connections)
        new_conns[provider_id] = ConnectionState(
            provider_id, ConnectionStatus.CONNECTED, 0.0, ts, 0
        )
        
        evt = ProviderConnected(ConnectionManager._create_id(), ts, provider_id)
        return replace(state, connections=new_conns, events=(*state.events, evt))

    @staticmethod
    def disconnect(
        state: MarketDataState, provider_id: str, provider: MarketDataProtocol, ts: float
    ) -> MarketDataState:
        provider.disconnect()
        new_conns = dict(state.connections)
        new_conns[provider_id] = ConnectionState(
            provider_id, ConnectionStatus.DISCONNECTED, 0.0, ts, 0
        )
        
        evt = ProviderDisconnected(ConnectionManager._create_id(), ts, provider_id, "Manual")
        return replace(state, connections=new_conns, events=(*state.events, evt))

    @staticmethod
    def subscribe(
        state: MarketDataState, provider_id: str, provider: MarketDataProtocol, 
        symbol: str, tf: Timeframe, ts: float
    ) -> MarketDataState:
        sub_id = f"{provider_id}:{symbol}:{tf.name}"
        success = provider.subscribe(symbol, tf)
        if not success:
            return state
            
        new_subs = dict(state.subscriptions)
        new_subs[sub_id] = Subscription(
            sub_id, provider_id, symbol, tf, SubscriptionStatus.ACTIVE
        )
        
        evt = SubscriptionCreated(ConnectionManager._create_id(), ts, sub_id, provider_id, symbol)
        return replace(state, subscriptions=new_subs, events=(*state.events, evt))

    @staticmethod
    def unsubscribe(
        state: MarketDataState, provider_id: str, provider: MarketDataProtocol, 
        symbol: str, tf: Timeframe, ts: float
    ) -> MarketDataState:
        sub_id = f"{provider_id}:{symbol}:{tf.name}"
        provider.unsubscribe(symbol, tf)
        
        new_subs = dict(state.subscriptions)
        if sub_id in new_subs:
            new_subs[sub_id] = replace(new_subs[sub_id], status=SubscriptionStatus.REMOVED)
            
        evt = SubscriptionRemoved(ConnectionManager._create_id(), ts, sub_id)
        return replace(state, subscriptions=new_subs, events=(*state.events, evt))