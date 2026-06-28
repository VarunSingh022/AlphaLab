"""Position domain model."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from alphalab.core.enums import AssetType
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import AssetId, PositionId, validate_uuid_id


@dataclass(frozen=True, slots=True)
class Position:
    """Immutable position snapshot for a single asset.

    Attributes:
        position_id: Unique position identifier.
        asset_id: Asset represented by the position.
        asset_type: Asset category.
        quantity: Signed non-zero position quantity.
        average_price: Non-negative average carrying price.
        market_price: Non-negative mark price.
        updated_at: Time the position snapshot was updated. The timestamp must be timezone-aware.
    """

    position_id: PositionId
    asset_id: AssetId
    asset_type: AssetType
    quantity: Decimal
    average_price: Decimal
    market_price: Decimal
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_uuid_id(self.position_id, "position_id")
        validate_uuid_id(self.asset_id, "asset_id")
        if not isinstance(self.asset_type, AssetType):
            raise DomainValidationError("asset_type must be an AssetType")
        if self.quantity == Decimal("0"):
            raise DomainValidationError("quantity must be non-zero")
        if self.average_price < Decimal("0"):
            raise DomainValidationError("average_price must be non-negative")
        if self.market_price < Decimal("0"):
            raise DomainValidationError("market_price must be non-negative")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise DomainValidationError("updated_at must be timezone-aware")
