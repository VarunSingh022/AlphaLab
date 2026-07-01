"""Validation rules ensuring structural integrity of risk evaluations."""

from decimal import Decimal

from alphalab.risk.exceptions import RiskValidationError
from alphalab.risk.models import OrderRequest


def validate_order_request(request: OrderRequest) -> None:
    """Validates structural integrity of an incoming order request."""
    if request.quantity <= Decimal("0"):
        raise RiskValidationError(f"Order quantity must be positive, got {request.quantity}")

    if request.price <= Decimal("0"):
        raise RiskValidationError(f"Order price must be positive, got {request.price}")

    if not request.asset_id:
        raise RiskValidationError("Asset ID cannot be empty.")
