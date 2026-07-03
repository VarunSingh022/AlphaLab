"""Validation rules ensuring structural and logical integrity of the feed."""

from alphalab.feed.exceptions import FeedValidationError, InvalidFeedStateError
from alphalab.feed.state import FeedState


def validate_connect(state: FeedState) -> None:
    """Validates that a connection can be established."""
    if state.connection.connected:
        raise InvalidFeedStateError("Feed is already connected.")


def validate_disconnect(state: FeedState) -> None:
    """Validates that a connection can be terminated."""
    if not state.connection.connected:
        raise InvalidFeedStateError("Feed is already disconnected.")


def validate_subscription(state: FeedState, symbol: str) -> None:
    """Validates subscription constraints."""
    if not state.connection.connected:
        raise InvalidFeedStateError("Cannot subscribe while disconnected.")
    if not symbol or not symbol.strip():
        raise FeedValidationError("Invalid or empty symbol.")
    if symbol in state.subscriptions and state.subscriptions[symbol].active:
        raise FeedValidationError(f"Duplicate active subscription for symbol: {symbol}")


def validate_unsubscription(state: FeedState, symbol: str) -> None:
    """Validates unsubscription constraints."""
    if not state.connection.connected:
        raise InvalidFeedStateError("Cannot unsubscribe while disconnected.")
    if symbol not in state.subscriptions or not state.subscriptions[symbol].active:
        raise FeedValidationError(f"Cannot unsubscribe; no active subscription for {symbol}.")


def validate_publish(state: FeedState) -> None:
    """Ensures data is only published when logically connected."""
    if not state.connection.connected:
        raise InvalidFeedStateError("Cannot publish data while disconnected.")
