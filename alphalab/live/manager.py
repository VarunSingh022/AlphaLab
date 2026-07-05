"""Orchestration of subscription states and lifecycle."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.live.events import SubscriptionCreated, SubscriptionRemoved
from alphalab.live.exceptions import InvalidLiveStateError
from alphalab.live.state import LiveState
from alphalab.live.subscription import Subscription
from alphalab.live.validation import validate_subscription


class SubscriptionManager:
    """Facade for managing immutable subscriptions mapping to symbols."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def subscribe(state: LiveState, subscription: Subscription, timestamp: float) -> LiveState:
        """Registers a new active subscription."""
        validate_subscription(state, subscription)

        sub_key = f"{subscription.provider_id}:{subscription.symbol}"
        new_subs = dict(state.subscriptions)
        new_subs[sub_key] = subscription

        evt = SubscriptionCreated(
            SubscriptionManager._create_id(),
            timestamp,
            subscription.provider_id,
            subscription.symbol,
            subscription.asset_class.name,
        )

        return replace(state, subscriptions=new_subs, events=(*state.events, evt))

    @staticmethod
    def unsubscribe(state: LiveState, provider_id: str, symbol: str, timestamp: float) -> LiveState:
        """Marks a subscription as inactive."""
        sub_key = f"{provider_id}:{symbol}"
        if sub_key not in state.subscriptions or not state.subscriptions[sub_key].active:
            raise InvalidLiveStateError(f"No active subscription to remove for {sub_key}.")

        target_sub = state.subscriptions[sub_key]
        inactive_sub = replace(target_sub, active=False)

        new_subs = dict(state.subscriptions)
        new_subs[sub_key] = inactive_sub

        evt = SubscriptionRemoved(SubscriptionManager._create_id(), timestamp, provider_id, symbol)

        return replace(state, subscriptions=new_subs, events=(*state.events, evt))
