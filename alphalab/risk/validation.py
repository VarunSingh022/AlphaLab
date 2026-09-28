"""Validation rules ensuring structural integrity of risk evaluations."""

from decimal import Decimal

from alphalab.core.order_request import OrderRequest
from alphalab.risk.exceptions import RiskValidationError


def validate_order_request(request: OrderRequest) -> None:
    """Validates structural integrity of an incoming order request."""
    if request.quantity <= Decimal("0"):
        raise RiskValidationError(f"Order quantity must be positive, got {request.quantity}")

    # Whether a non-positive price is one the instrument takes is its declared
    # economics' question, which the pipeline's price gate asks before any
    # request exists (ACC-007); here it need only be a price.
    if not request.price.is_finite():
        raise RiskValidationError(f"Order price must be a finite number, got {request.price}")

    if not request.asset_id:
        raise RiskValidationError("Asset ID cannot be empty.")
