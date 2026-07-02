"""Validation rules for checking Intent output correctness."""

from decimal import Decimal

from alphalab.strategy.events import Intent
from alphalab.strategy.exceptions import InvalidIntentError


def validate_intent(intent: Intent) -> None:
    """
    Validates structural correctness of an emitted Intent.
    Malformed intents are dropped; repeated drops cause Strategy failure.
    """
    if not intent.strategy_id:
        raise InvalidIntentError("Intent must specify a strategy_id.")
    if not intent.instrument:
        raise InvalidIntentError("Intent must specify an instrument.")
    if intent.strength < Decimal("0.0") or intent.strength > Decimal("1.0"):
        raise InvalidIntentError(
            f"Intent strength must be between 0.0 and 1.0, got {intent.strength}."
        )
    if intent.timestamp < 0:
        raise InvalidIntentError("Intent timestamp cannot be negative.")
