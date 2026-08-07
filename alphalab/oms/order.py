"""Compatibility Order model for OMS workflows.

This module preserves the historical OMS API while routing conversions through the
canonical core Order domain model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from alphalab.core.enums import OrderStatus, OrderType, Side, TimeInForce
from alphalab.core.ids import AssetId
from alphalab.core.ids import OrderId as CoreOrderId
from alphalab.core.order import Order as CoreOrder
from alphalab.oms.exceptions import InvalidTransitionError
from alphalab.oms.ids import OrderId


@dataclass(frozen=True, slots=True)
class Order:
    """Immutable representation of a market order.

    This remains the OMS-facing compatibility model, but it can be converted into
    the canonical core Order for shared domain usage.
    """

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

    @property
    def canonical_order(self) -> CoreOrder:
        """Expose the canonical core Order representation for shared domain usage."""
        return self.to_core_order()

    def to_core_order(self) -> CoreOrder:
        """Convert the OMS order into the canonical core Order representation."""
        return CoreOrder(
            order_id=cast(CoreOrderId, str(self.order_id.value)),
            asset_id=cast(AssetId, self.asset_id),
            side=self.side,
            order_type=self.order_type,
            quantity=self.quantity,
            created_at=datetime.fromtimestamp(self.created_at, tz=UTC),
            time_in_force=TimeInForce.DAY,
            limit_price=self.limit_price,
            stop_price=self.stop_price,
            strategy_id=None,
            signal_id=None,
        )

    @classmethod
    def from_core_order(
        cls,
        order: CoreOrder,
        *,
        status: OrderStatus = OrderStatus.NEW,
        filled_quantity: Decimal = Decimal("0"),
        remaining_quantity: Decimal | None = None,
        average_fill_price: Decimal = Decimal("0"),
        updated_at: float | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> Order:
        """Create an OMS compatibility order from a canonical core Order."""
        created_at = order.created_at.timestamp()
        return cls(
            order_id=OrderId(UUID(order.order_id)),
            strategy_id="",
            asset_id=order.asset_id,
            side=order.side,
            order_type=order.order_type,
            status=status,
            quantity=order.quantity,
            filled_quantity=filled_quantity,
            remaining_quantity=order.quantity if remaining_quantity is None else remaining_quantity,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            average_fill_price=average_fill_price,
            created_at=created_at,
            updated_at=created_at if updated_at is None else updated_at,
            metadata=dict(metadata or {}),
        )

    def accept(self, timestamp: float) -> Order:
        """Transitions order to ACCEPTED state."""
        if self.status not in {OrderStatus.NEW, OrderStatus.PENDING}:
            raise InvalidTransitionError(f"Cannot accept order in status: {self.status}")
        return replace(self, status=OrderStatus.ACCEPTED, updated_at=timestamp)

    def reject(self, timestamp: float) -> Order:
        """Transitions order to REJECTED state."""
        if self.status not in {OrderStatus.NEW, OrderStatus.PENDING}:
            raise InvalidTransitionError(f"Cannot reject order in status: {self.status}")
        return replace(self, status=OrderStatus.REJECTED, updated_at=timestamp)

    def cancel(self, timestamp: float) -> Order:
        """Transitions order to CANCELLED state."""
        if self.is_closed:
            raise InvalidTransitionError(f"Cannot cancel a closed order. Status: {self.status}")
        return replace(self, status=OrderStatus.CANCELLED, updated_at=timestamp)

    def expire(self, timestamp: float) -> Order:
        """Transitions order to EXPIRED state."""
        if self.is_closed:
            raise InvalidTransitionError(f"Cannot expire a closed order. Status: {self.status}")
        return replace(self, status=OrderStatus.EXPIRED, updated_at=timestamp)

    def partial_fill(self, fill_qty: Decimal, fill_price: Decimal, timestamp: float) -> Order:
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

    def fill(self, fill_qty: Decimal, fill_price: Decimal, timestamp: float) -> Order:
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
    ) -> Order:
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
