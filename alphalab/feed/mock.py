"""Deterministic Mock Feed implementation satisfying FeedProtocol."""

import uuid
from dataclasses import replace
from typing import Any

from alphalab.feed.adapter import FeedAdapter
from alphalab.feed.events import (
    FeedConnected,
    FeedDisconnected,
    FeedEvent,
    FeedSubscribed,
    FeedUnsubscribed,
    HeartbeatReceived,
    MarketDataReceived,
)
from alphalab.feed.normalization import RawPayload
from alphalab.feed.state import FeedState
from alphalab.feed.subscription import Subscription
from alphalab.feed.validation import (
    validate_connect,
    validate_disconnect,
    validate_publish,
    validate_subscription,
    validate_unsubscription,
)


class MockFeed:
    """Pure functional, in-memory dummy provider for testing Market workflows."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    def connect(
        self, state: FeedState, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]:
        validate_connect(state)

        event = FeedConnected(self._create_id(), timestamp, state.provider_id)
        
        new_conn = replace(
            state.connection,
            connected=True,
            last_heartbeat=timestamp,
            latency_ms=0.0,
        )
        
        new_state = replace(
            state,
            connection=new_conn,
            events=(*state.events, event),
        )
        return new_state, (event,)

    def disconnect(
        self, state: FeedState, reason: str, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]:
        validate_disconnect(state)

        event = FeedDisconnected(self._create_id(), timestamp, state.provider_id, reason)
        
        new_conn = replace(
            state.connection,
            connected=False,
        )
        
        new_state = replace(
            state,
            connection=new_conn,
            events=(*state.events, event),
        )
        return new_state, (event,)

    def subscribe(
        self, state: FeedState, symbol: str, feed_type: str, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]:
        validate_subscription(state, symbol)

        event = FeedSubscribed(
            self._create_id(), timestamp, state.provider_id, symbol, feed_type
        )
        
        sub = Subscription(symbol, feed_type, True, timestamp)
        new_subs = dict(state.subscriptions)
        new_subs[symbol] = sub
        
        new_state = replace(
            state,
            subscriptions=new_subs,
            events=(*state.events, event),
        )
        return new_state, (event,)

    def unsubscribe(
        self, state: FeedState, symbol: str, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]:
        validate_unsubscription(state, symbol)

        event = FeedUnsubscribed(
            self._create_id(), timestamp, state.provider_id, symbol
        )
        
        sub = state.subscriptions[symbol]
        new_subs = dict(state.subscriptions)
        new_subs[symbol] = replace(sub, active=False, timestamp=timestamp)
        
        new_state = replace(
            state,
            subscriptions=new_subs,
            events=(*state.events, event),
        )
        return new_state, (event,)

    def heartbeat(
        self, state: FeedState, latency_ms: float, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]:
        if not state.connection.connected:
            return state, ()
            
        event = HeartbeatReceived(
            self._create_id(), timestamp, state.provider_id, latency_ms
        )
        
        new_conn = replace(
            state.connection, last_heartbeat=timestamp, latency_ms=latency_ms
        )
        
        new_state = replace(
            state,
            connection=new_conn,
            events=(*state.events, event),
        )
        return new_state, (event,)

    def publish(
        self, state: FeedState, payload: Any, timestamp: float
    ) -> tuple[FeedState, tuple[FeedEvent, ...]]:
        """Consumes a raw payload, adapts it to AlphaLab formats, and emits."""
        validate_publish(state)
        
        # In a real feed, filtering by subscription happens here
        if isinstance(payload, RawPayload):
            sym = str(payload.data.get("symbol", ""))
            if sym not in state.subscriptions or not state.subscriptions[sym].active:
                return state, ()

        # Normalize via Adapter
        normalized_data = FeedAdapter.process_payload(
            payload, state.connection.provider_name
        )

        event = MarketDataReceived(
            self._create_id(), timestamp, state.provider_id, normalized_data
        )

        new_stats = replace(
            state.statistics,
            messages_received=state.statistics.messages_received + 1,
        )

        new_state = replace(
            state,
            statistics=new_stats,
            events=(*state.events, event),
        )
        return new_state, (event,)

    def status(self, state: FeedState) -> bool:
        return state.connection.connected