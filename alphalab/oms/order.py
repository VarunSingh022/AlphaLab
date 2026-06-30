"""Immutable Order domain model."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from alphalab.oms.exceptions import InvalidTransitionError
from alphalab.oms.ids import OrderId
from alphalab.oms.status import OrderStatus, OrderType, Side


@dataclass(frozen=True, slots=True)
class Order:
    """Immutable representation of a market order."""

    order_id: OrderId
    strategy_id: str
    asset_id: str
    side: Side
    order_type: OrderType
    status: OrderStatus

    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal

    limit_price: Decimal | None
    stop_price: Decimal | None
    average_fill_price: Decimal

    created_at: float
    updated_at: float

    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        """Determines if the order is currently active and open in the market."""
        return self.status in {
            OrderStatus.NEW,
            OrderStatus.PENDING,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING,
        }

    @property
    def is_closed(self) -> bool:
        """Determines if the order has reached a terminal state."""
        return not self.is_open

    @property
    def fill_ratio(self) -> Decimal:
        """Calculates the ratio of filled quantity to total requested quantity."""
        if self.quantity == Decimal("0"):
            return Decimal("0")
        return self.filled_quantity / self.quantity

    def accept(self, timestamp: float) -> "Order":
        """Transitions order to ACCEPTED state."""
        if self.status not in {OrderStatus.NEW, OrderStatus.PENDING}:
            raise InvalidTransitionError(f"Cannot accept order in status: {self.status}")
        return replace(self, status=OrderStatus.ACCEPTED, updated_at=timestamp)

    def reject(self, timestamp: float) -> "Order":
        """Transitions order to REJECTED state."""
        if self.status not in {OrderStatus.NEW, OrderStatus.PENDING}:
            raise InvalidTransitionError(f"Cannot reject order in status: {self.status}")
        return replace(self, status=OrderStatus.REJECTED, updated_at=timestamp)

    def cancel(self, timestamp: float) -> "Order":
        """Transitions order to CANCELLED state."""
        if self.is_closed:
            raise InvalidTransitionError(f"Cannot cancel a closed order. Status: {self.status}")
        return replace(self, status=OrderStatus.CANCELLED, updated_at=timestamp)

    def expire(self, timestamp: float) -> "Order":
        """Transitions order to EXPIRED state."""
        if self.is_closed:
            raise InvalidTransitionError(f"Cannot expire a closed order. Status: {self.status}")
        return replace(self, status=OrderStatus.EXPIRED, updated_at=timestamp)

    def partial_fill(self, fill_qty: Decimal, fill_price: Decimal, timestamp: float) -> "Order":
        """Applies a partial fill to the order, returning a newly updated instance."""
        if self.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise InvalidTransitionError(f"Cannot partially fill order in status: {self.status}")

        new_filled = self.filled_quantity + fill_qty
        new_rem = self.quantity - new_filled
        new_avg = (
            (self.filled_quantity * self.average_fill_price) + (fill_qty * fill_price)
        ) / new_filled

        return replace(
            self,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=new_filled,
            remaining_quantity=new_rem,
            average_fill_price=new_avg,
            updated_at=timestamp,
        )

    def fill(self, fill_qty: Decimal, fill_price: Decimal, timestamp: float) -> "Order":
        """Applies a terminal complete fill to the order."""
        if self.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise InvalidTransitionError(f"Cannot fill order in status: {self.status}")

        new_filled = self.filled_quantity + fill_qty
        new_rem = self.quantity - new_filled
        new_avg = (
            (self.filled_quantity * self.average_fill_price) + (fill_qty * fill_price)
        ) / new_filled

        return replace(
            self,
            status=OrderStatus.FILLED,
            filled_quantity=new_filled,
            remaining_quantity=new_rem,
            average_fill_price=new_avg,
            updated_at=timestamp,
        )

    def replace(
        self, new_qty: Decimal, timestamp: float, new_limit: Decimal | None = None
    ) -> "Order":
        """Replaces quantity and optionally limit price of an open order."""
        if self.is_closed:
            raise InvalidTransitionError("Cannot replace closed order.")

        limit = new_limit if new_limit is not None else self.limit_price
        new_rem = new_qty - self.filled_quantity

        return replace(
            self,
            quantity=new_qty,
            remaining_quantity=new_rem,
            limit_price=limit,
            updated_at=timestamp,
        )
