"""Fill domain model."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import AssetId, FillId, OrderId, validate_uuid_id


@dataclass(frozen=True, slots=True)
class Fill:
    """Immutable execution fill for an order.

    Attributes:
        fill_id: Unique fill identifier.
        order_id: Order satisfied by this fill.
        asset_id: Asset executed by this fill.
        side: Executed direction.
        quantity: Positive filled quantity.
        price: Positive execution price.
        filled_at: Time the fill occurred. The timestamp must be timezone-aware.
        commission: Non-negative execution commission.
    """

    fill_id: FillId
    order_id: OrderId
    asset_id: AssetId
    side: Side
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    commission: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        validate_uuid_id(self.fill_id, "fill_id")
        validate_uuid_id(self.order_id, "order_id")
        validate_uuid_id(self.asset_id, "asset_id")
        if not isinstance(self.side, Side):
            raise DomainValidationError("side must be a Side")
        if self.quantity <= Decimal("0"):
            raise DomainValidationError("quantity must be positive")
        if self.price <= Decimal("0"):
            raise DomainValidationError("price must be positive")
        if self.commission < Decimal("0"):
            raise DomainValidationError("commission must be non-negative")
        if self.filled_at.tzinfo is None or self.filled_at.utcoffset() is None:
            raise DomainValidationError("filled_at must be timezone-aware")
