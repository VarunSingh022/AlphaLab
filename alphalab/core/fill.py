"""Fill domain model."""

from dataclasses import dataclass
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
        price: Execution price. Positive for every instrument but one whose
            declared economics allow negative prices (ledger ACC-007), which
            is the instrument's question and is answered where its economics
            are known -- the execution pipeline's price gate and the
            portfolio. Here it need only be a number.
        filled_at: Unix timestamp (seconds) the fill occurred.
        commission: Signed execution commission: a negative one is a rebate,
            which a venue pays a liquidity provider (v3.11, ACC-007).
    """

    fill_id: FillId
    order_id: OrderId
    asset_id: AssetId
    side: Side
    quantity: Decimal
    price: Decimal
    filled_at: float
    commission: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        validate_uuid_id(self.fill_id, "fill_id")
        validate_uuid_id(self.order_id, "order_id")
        validate_uuid_id(self.asset_id, "asset_id")
        if not isinstance(self.side, Side):
            raise DomainValidationError("side must be a Side")
        if self.quantity <= Decimal("0"):
            raise DomainValidationError("quantity must be positive")
        if not self.price.is_finite():
            raise DomainValidationError("price must be a finite number")
        if not self.commission.is_finite():
            raise DomainValidationError("commission must be a finite number")
