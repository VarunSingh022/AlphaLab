"""Strict structural and state validation rules."""

from alphalab.common.validators import (
    require_mapping_key,
    require_missing_mapping_key,
    require_non_empty_string,
)
from alphalab.live.exceptions import InvalidLiveStateError, LiveValidationError
from alphalab.live.provider import Provider
from alphalab.live.state import LiveState
from alphalab.live.subscription import Subscription


def validate_provider_registration(state: LiveState, provider: Provider) -> None:
    """Ensures a provider is unique and correctly formatted."""
    require_non_empty_string(
        provider.provider_id,
        "provider_id",
        message="Provider ID cannot be empty.",
        exception_type=LiveValidationError,
    )
    require_missing_mapping_key(
        state.providers,
        provider.provider_id,
        f"Provider '{provider.provider_id}' is already registered.",
        exception_type=LiveValidationError,
    )
    if not provider.asset_classes:
        raise LiveValidationError("Provider must support at least one asset class.")


def validate_subscription(state: LiveState, subscription: Subscription) -> None:
    """Ensures a subscription targets a valid provider and is unique."""
    require_mapping_key(
        state.providers,
        subscription.provider_id,
        f"Provider '{subscription.provider_id}' not found.",
        exception_type=InvalidLiveStateError,
    )

    sub_key = f"{subscription.provider_id}:{subscription.symbol}"
    if sub_key in state.subscriptions and state.subscriptions[sub_key].active:
        raise LiveValidationError(f"Duplicate active subscription for '{sub_key}'.")


def validate_tick_routing(state: LiveState, provider_id: str, symbol: str) -> None:
    """Ensures incoming data maps to an active provider and subscription."""
    conn = state.connections.get(provider_id)
    if not conn or not conn.connected:
        raise InvalidLiveStateError(f"Provider '{provider_id}' is not connected.")

    sub_key = f"{provider_id}:{symbol}"
    sub = state.subscriptions.get(sub_key)
    if not sub or not sub.active:
        raise InvalidLiveStateError(f"No active subscription for '{sub_key}'.")
