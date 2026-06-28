"""Order domain model."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from alphalab.core.enums import OrderType, Side, TimeInForce
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import AssetId, OrderId, SignalId, StrategyId, validate_uuid_id


@dataclass(frozen=True, slots=True)
class Order:
    """Immutable order instruction.

    Attributes:
        order_id: Unique order identifier.
        asset_id: Asset the order targets.
        side: Order direction.
        order_type: Execution instruction type.
        quantity: Positive order quantity.
        created_at: Time the order was created. The timestamp must be timezone-aware.
        time_in_force: Order lifetime policy.
        limit_price: Limit price for limit and stop-limit orders.
        stop_price: Stop trigger price for stop and stop-limit orders.
        strategy_id: Optional strategy identifier associated with the order.
        signal_id: Optional signal identifier associated with the order.
    """

    order_id: OrderId
    asset_id: AssetId
    side: Side
    order_type: OrderType
    quantity: Decimal
    created_at: datetime
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    strategy_id: StrategyId | None = None
    signal_id: SignalId | None = None

    def __post_init__(self) -> None:
        validate_uuid_id(self.order_id, "order_id")
        validate_uuid_id(self.asset_id, "asset_id")
        if self.strategy_id is not None:
            validate_uuid_id(self.strategy_id, "strategy_id")
        if self.signal_id is not None:
            validate_uuid_id(self.signal_id, "signal_id")
        if not isinstance(self.side, Side):
            raise DomainValidationError("side must be a Side")
        if not isinstance(self.order_type, OrderType):
            raise DomainValidationError("order_type must be an OrderType")
        if not isinstance(self.time_in_force, TimeInForce):
            raise DomainValidationError("time_in_force must be a TimeInForce")
        if self.quantity <= Decimal("0"):
            raise DomainValidationError("quantity must be positive")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise DomainValidationError("created_at must be timezone-aware")
        self._validate_prices()

    def _validate_prices(self) -> None:
        if self.limit_price is not None and self.limit_price <= Decimal("0"):
            raise DomainValidationError("limit_price must be positive")
        if self.stop_price is not None and self.stop_price <= Decimal("0"):
            raise DomainValidationError("stop_price must be positive")

        if self.order_type is OrderType.MARKET:
            if self.limit_price is not None or self.stop_price is not None:
                raise DomainValidationError("market orders cannot include limit or stop prices")
        elif self.order_type is OrderType.LIMIT:
            if self.limit_price is None or self.stop_price is not None:
                raise DomainValidationError("limit orders require only limit_price")
        elif self.order_type is OrderType.STOP:
            if self.stop_price is None or self.limit_price is not None:
                raise DomainValidationError("stop orders require only stop_price")
        elif self.limit_price is None or self.stop_price is None:
            raise DomainValidationError("stop-limit orders require limit_price and stop_price")
