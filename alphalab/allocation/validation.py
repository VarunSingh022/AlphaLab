"""Validation rules ensuring structural integrity of allocations."""

from decimal import Decimal

from alphalab.allocation.exceptions import AllocationValidationError
from alphalab.strategy.events import Intent, IntentKind


def validate_intent(intent: Intent) -> None:
    """Ensures intent structural integrity before processing."""
    if not intent.strategy_id:
        raise AllocationValidationError("Intent must have a valid strategy_id.")
    if not intent.instrument:
        raise AllocationValidationError("Intent must have a valid instrument.")
    if not intent.target.is_finite():
        raise AllocationValidationError(f"Intent target must be finite, got {intent.target}.")
    if not intent.strength.is_finite() or not Decimal("0") <= intent.strength <= Decimal("1"):
        raise AllocationValidationError("Intent strength must be between 0.0 and 1.0.")
    if intent.timestamp < 0.0:
        raise AllocationValidationError("Intent timestamp cannot be negative.")
    if intent.kind is not IntentKind.DELTA:
        raise AllocationValidationError(
            f"Allocation sizes order deltas; an intent of kind {intent.kind!r} is not one."
        )


def validate_net_quantity(quantity: Decimal, enforce_long_only: bool = False) -> None:
    """Ensures a netted delta is a number, and optionally that it does not sell.

    ``enforce_long_only`` refuses **any** negative delta, because a delta alone
    cannot tell a sale that closes a long from one that opens a short. Allocation
    no longer uses it for that: it checks the projected position instead -- see
    :func:`validate_long_only`.
    """
    if quantity.is_nan():
        raise AllocationValidationError("Net quantity cannot be NaN.")
    if enforce_long_only and quantity < Decimal("0.00"):
        raise AllocationValidationError("Negative allocation rejected: long-only enforced.")


def validate_long_only(asset_id: str, delta: Decimal, committed: Decimal) -> None:
    """Refuse a delta that would take ``asset_id``'s position below zero.

    ``committed`` is the signed position the account holds or has working in the
    asset -- filled plus working orders -- so a sale that closes a long passes
    and one that would open or deepen a short does not. Until v3.10 long-only
    was checked on the delta alone and refused every sale (ledger ALC-001).
    """
    projected = committed + delta
    if projected < 0:
        raise AllocationValidationError(
            f"Long-only: a delta of {delta} in {asset_id} against a committed position of "
            f"{committed} would leave {projected}, a short."
        )
