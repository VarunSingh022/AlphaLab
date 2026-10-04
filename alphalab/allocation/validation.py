"""Validation rules ensuring structural integrity of allocations.

Whether an intent is structurally one is not allocation's rule: it is
:func:`alphalab.strategy.validate_intent`, the one check the strategy runtime
also runs. Until v3.13 this module kept a second, disagreeing copy under the
same name (ledger API-001).
"""

from decimal import Decimal

from alphalab.allocation.exceptions import AllocationValidationError


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
