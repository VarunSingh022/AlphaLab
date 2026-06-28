"""Trade domain model."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import AssetId, FillId, OrderId, TradeId, validate_uuid_id


@dataclass(frozen=True, slots=True)
class Trade:
    """Immutable executed trade record.

    Attributes:
        trade_id: Unique trade identifier.
        asset_id: Asset executed by the trade.
        side: Executed direction.
        quantity: Positive trade quantity.
        average_price: Positive average execution price.
        fill_ids: One or more fills that compose the trade.
        executed_at: Time the trade was recorded. The timestamp must be timezone-aware.
        order_id: Optional order identifier linked to the trade.
    """

    trade_id: TradeId
    asset_id: AssetId
    side: Side
    quantity: Decimal
    average_price: Decimal
    fill_ids: tuple[FillId, ...]
    executed_at: datetime
    order_id: OrderId | None = None

    def __post_init__(self) -> None:
        validate_uuid_id(self.trade_id, "trade_id")
        validate_uuid_id(self.asset_id, "asset_id")
        if self.order_id is not None:
            validate_uuid_id(self.order_id, "order_id")
        if not isinstance(self.side, Side):
            raise DomainValidationError("side must be a Side")
        if self.quantity <= Decimal("0"):
            raise DomainValidationError("quantity must be positive")
        if self.average_price <= Decimal("0"):
            raise DomainValidationError("average_price must be positive")
        if not self.fill_ids:
            raise DomainValidationError("fill_ids must contain at least one fill identifier")
        if len(set(self.fill_ids)) != len(self.fill_ids):
            raise DomainValidationError("fill_ids must be unique")
        for fill_id in self.fill_ids:
            validate_uuid_id(fill_id, "fill_ids")
        if self.executed_at.tzinfo is None or self.executed_at.utcoffset() is None:
            raise DomainValidationError("executed_at must be timezone-aware")
