"""Top-level Engine Facade orchestrating all Live Market Data components."""

from alphalab.live.feed import LiveFeed
from alphalab.live.manager import SubscriptionManager
from alphalab.live.message import QuoteTick, TradeTick
from alphalab.live.provider import Provider
from alphalab.live.registry import LiveRegistry
from alphalab.live.state import LiveState
from alphalab.live.subscription import Subscription


class LiveEngine:
    """Facade for managing deterministic live state orchestration."""

    @staticmethod
    def initialize(engine_id: str) -> LiveState:
        """Constructs an empty base state for the live layer."""
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return LiveState(engine_id=engine_id)

    @staticmethod
    def register_provider(state: LiveState, provider: Provider, timestamp: float) -> LiveState:
        return LiveRegistry.register_provider(state, provider, timestamp)

    @staticmethod
    def connect_provider(state: LiveState, provider_id: str, timestamp: float) -> LiveState:
        return LiveRegistry.connect_provider(state, provider_id, timestamp)

    @staticmethod
    def disconnect_provider(
        state: LiveState, provider_id: str, reason: str, timestamp: float
    ) -> LiveState:
        return LiveRegistry.disconnect_provider(state, provider_id, reason, timestamp)

    @staticmethod
    def subscribe(state: LiveState, subscription: Subscription, timestamp: float) -> LiveState:
        return SubscriptionManager.subscribe(state, subscription, timestamp)

    @staticmethod
    def unsubscribe(state: LiveState, provider_id: str, symbol: str, timestamp: float) -> LiveState:
        return SubscriptionManager.unsubscribe(state, provider_id, symbol, timestamp)

    @staticmethod
    def process_trade(state: LiveState, trade: TradeTick) -> LiveState:
        return LiveFeed.process_trade(state, trade)

    @staticmethod
    def process_quote(state: LiveState, quote: QuoteTick) -> LiveState:
        return LiveFeed.process_quote(state, quote)
