"""Canonical lifecycle Order model.

This is the order representation used across AlphaLab's execution path -- OMS,
the runtime execution pipeline, and everything downstream. It carries mutable
lifecycle state (status, fills) alongside the original instruction and exposes
the deterministic state transitions that move an order through its lifecycle.

Which transitions exist is not decided here. Since v3.9 every method below asks
:data:`alphalab.core.lifecycle.ORDER_TRANSITIONS` -- the one declared table the
venue boundary also reads -- whether its move is legal, so the OMS and a broker
mirror cannot come to disagree about what an event may do to an order. What this
module adds is the arithmetic a status cannot express: a partial fill must leave
quantity working, a complete fill must complete the order exactly, and an
amendment must leave something to work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, plain
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.core.lifecycle import (
    WORKING_ORDER_STATUSES,
    ExecutionEventKind,
    next_order_status,
)
from alphalab.oms.exceptions import InvalidTransitionError
from alphalab.oms.ids import OrderId


@dataclass(frozen=True, slots=True)
class Order:
    """Immutable snapshot of a market order and its lifecycle state."""

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
        return self.status in WORKING_ORDER_STATUSES

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

    def _next(self, event: ExecutionEventKind, refusal: str) -> OrderStatus:
        """The status ``event`` moves this order to, or the refusal the method states."""

        target = next_order_status(self.status, event, self.filled_quantity)
        if target is None:
            raise InvalidTransitionError(refusal)
        return target

    def accept(self, timestamp: float) -> Order:
        """Transitions order to ACCEPTED state."""
        status = self._next(
            ExecutionEventKind.ORDER_ACCEPTED, f"Cannot accept order in status: {self.status}"
        )
        return replace(self, status=status, updated_at=timestamp)

    def reject(self, timestamp: float) -> Order:
        """Transitions order to REJECTED state.

        An order can be rejected before it is working (NEW / PENDING) and also
        after acceptance, which is what a venue rejection looks like: the OMS
        accepted the order, the execution venue then refused it. Without the
        ACCEPTED case such an order would stay open forever with no fills.
        An order that has already traded cannot be rejected.
        """
        status = self._next(
            ExecutionEventKind.ORDER_REJECTED, f"Cannot reject order in status: {self.status}"
        )
        return replace(self, status=status, updated_at=timestamp)

    def cancel(self, timestamp: float) -> Order:
        """Transitions order to CANCELLED state."""
        status = self._next(
            ExecutionEventKind.ORDER_CANCELLED,
            f"Cannot cancel a closed order. Status: {self.status}",
        )
        return replace(self, status=status, updated_at=timestamp)

    def expire(self, timestamp: float) -> Order:
        """Transitions order to EXPIRED state."""
        status = self._next(
            ExecutionEventKind.ORDER_EXPIRED,
            f"Cannot expire a closed order. Status: {self.status}",
        )
        return replace(self, status=status, updated_at=timestamp)

    def _filled_by(self, fill_qty: Decimal, fill_price: Decimal) -> tuple[Decimal, Decimal]:
        """The filled quantity and average price after a fill, refusing a non-fill."""

        if fill_qty <= Decimal("0"):
            raise InvalidTransitionError(
                f"A fill of {fill_qty} is not a fill; a fill moves a positive quantity."
            )
        # In the pinned accounting context: a volume-weighted average must not
        # depend on the caller's decimal precision or rounding (ACC-004).
        ctx = ACCOUNTING_CONTEXT
        new_filled = ctx.add(self.filled_quantity, fill_qty)
        new_avg = plain(
            ctx.divide(
                ctx.add(
                    ctx.multiply(self.filled_quantity, self.average_fill_price),
                    ctx.multiply(fill_qty, fill_price),
                ),
                new_filled,
            )
        )
        return new_filled, new_avg

    def partial_fill(self, fill_qty: Decimal, fill_price: Decimal, timestamp: float) -> Order:
        """Applies a partial fill to the order, returning a newly updated instance.

        A partial fill leaves quantity working; one that would not is a complete
        fill or an overfill, and neither is recorded under this name.
        """
        status = self._next(
            ExecutionEventKind.ORDER_PARTIALLY_FILLED,
            f"Cannot partially fill order in status: {self.status}",
        )
        new_filled, new_avg = self._filled_by(fill_qty, fill_price)
        if new_filled >= self.quantity:
            raise InvalidTransitionError(
                f"A partial fill of {fill_qty} takes order {self.order_id.value} to {new_filled} "
                f"of {self.quantity}, which leaves nothing working; record it as a fill."
            )

        return replace(
            self,
            status=status,
            filled_quantity=new_filled,
            remaining_quantity=ACCOUNTING_CONTEXT.subtract(self.quantity, new_filled),
            average_fill_price=new_avg,
            updated_at=timestamp,
        )

    def fill(self, fill_qty: Decimal, fill_price: Decimal, timestamp: float) -> Order:
        """Applies a terminal complete fill to the order.

        The fill must complete the order exactly. Short of that it is a partial
        fill; beyond it, it is an overfill -- more executed than was ordered --
        and a ``FILLED`` order with a negative remainder is a state no venue can
        report.
        """
        status = self._next(
            ExecutionEventKind.ORDER_FILLED, f"Cannot fill order in status: {self.status}"
        )
        new_filled, new_avg = self._filled_by(fill_qty, fill_price)
        if new_filled != self.quantity:
            raise InvalidTransitionError(
                f"A complete fill of {fill_qty} takes order {self.order_id.value} to {new_filled} "
                f"of {self.quantity}; a complete fill executes exactly what is working."
            )

        return replace(
            self,
            status=status,
            filled_quantity=new_filled,
            remaining_quantity=ACCOUNTING_CONTEXT.subtract(self.quantity, new_filled),
            average_fill_price=new_avg,
            updated_at=timestamp,
        )

    def replace(
        self, new_qty: Decimal, timestamp: float, new_limit: Decimal | None = None
    ) -> Order:
        """Replaces quantity and optionally limit price of an open order.

        The new quantity must exceed what has already filled. An amendment that
        leaves nothing working is a cancel, and one below the filled quantity
        would describe an order that executed more than it was ever for.
        """
        status = self._next(ExecutionEventKind.ORDER_REPLACED, "Cannot replace closed order.")
        if new_qty <= self.filled_quantity:
            raise InvalidTransitionError(
                f"Cannot replace order {self.order_id.value} to a quantity of {new_qty}: "
                f"{self.filled_quantity} has already filled, so nothing would be left "
                "working. Cancel it instead."
            )

        limit = new_limit if new_limit is not None else self.limit_price

        return replace(
            self,
            status=status,
            quantity=new_qty,
            remaining_quantity=ACCOUNTING_CONTEXT.subtract(new_qty, self.filled_quantity),
            limit_price=limit,
            updated_at=timestamp,
        )
