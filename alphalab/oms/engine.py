"""Pure functional Order Management System."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from alphalab.common.ids import new_id
from alphalab.oms.events import (
    OMSEvent,
    OrderAccepted,
    OrderCancelled,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
    OrderReplaced,
    OrderSubmitted,
)
from alphalab.oms.exceptions import DuplicateOrderError
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.state import OMSState
from alphalab.oms.validation import validate_order


class OMSEngine:
    """Pure immutable Order Management Engine."""

    # =====================================================
    # Internal Helpers
    # =====================================================

    @staticmethod
    def _append_event(
        state: OMSState,
        event: OMSEvent,
    ) -> OMSState:
        """Append an event."""

        return replace(
            state,
            history=state.history.append(event),
            events=state.events.append(event),
        )

    @staticmethod
    def _replace_book(
        state: OMSState,
        order: Order,
    ) -> OMSState:
        """Replace an order inside the order book."""

        if state.orders.contains(order.order_id):
            book = state.orders.replace(order)
        else:
            book = state.orders.add(order)

        return replace(
            state,
            orders=book,
        )

    @staticmethod
    def _update_sets(
        state: OMSState,
        order: Order,
    ) -> OMSState:
        """Update active/completed order sets."""

        if order.is_open:
            active = state.active_orders.add(order.order_id)
            completed = state.completed_orders.discard(order.order_id)
        else:
            active = state.active_orders.discard(order.order_id)
            completed = state.completed_orders.add(order.order_id)

        return replace(
            state,
            active_orders=active,
            completed_orders=completed,
        )

    @staticmethod
    def _store(
        state: OMSState,
        order: Order,
    ) -> OMSState:
        """Store an updated order."""

        state = OMSEngine._replace_book(
            state,
            order,
        )

        state = OMSEngine._update_sets(
            state,
            order,
        )

        return state

    @staticmethod
    def _record(
        state: OMSState,
        order: Order,
        event: OMSEvent,
    ) -> OMSState:
        """Store order and append event."""

        state = OMSEngine._store(
            state,
            order,
        )

        return OMSEngine._append_event(
            state,
            event,
        )

    @staticmethod
    def _get(
        state: OMSState,
        order_id: OrderId,
    ) -> Order:
        """Lookup an order."""

        return state.orders.find(order_id)
        # =====================================================

    # Submit
    # =====================================================

    @staticmethod
    def submit(
        state: OMSState,
        order: Order,
        current_timestamp: float,
    ) -> OMSState:
        """
        Submit a new order into the OMS.
        """

        if state.orders.contains(order.order_id):
            raise DuplicateOrderError(f"Order already exists: {order.order_id}")

        validate_order(
            order,
            current_timestamp,
        )

        state = OMSEngine._store(
            state,
            order,
        )

        event = OrderSubmitted(
            event_id=str(new_id()),
            timestamp=current_timestamp,
            order_id=order.order_id,
            order=order,
        )

        return OMSEngine._append_event(
            state,
            event,
        )

    # =====================================================
    # Accept
    # =====================================================

    @staticmethod
    def accept(
        state: OMSState,
        order_id: OrderId,
        timestamp: float,
    ) -> OMSState:
        """
        Accept an order.
        """

        order = OMSEngine._get(
            state,
            order_id,
        )

        updated = order.accept(
            timestamp,
        )

        event = OrderAccepted(
            event_id=str(new_id()),
            timestamp=timestamp,
            order_id=order_id,
        )

        return OMSEngine._record(
            state,
            updated,
            event,
        )

    # =====================================================
    # Reject
    # =====================================================

    @staticmethod
    def reject(
        state: OMSState,
        order_id: OrderId,
        reason: str,
        timestamp: float,
    ) -> OMSState:
        """
        Reject an order.
        """

        order = OMSEngine._get(
            state,
            order_id,
        )

        updated = order.reject(
            timestamp,
        )

        event = OrderRejected(
            event_id=str(new_id()),
            timestamp=timestamp,
            order_id=order_id,
            reason=reason,
        )

        return OMSEngine._record(
            state,
            updated,
            event,
        )
        # =====================================================

    # Cancel
    # =====================================================

    @staticmethod
    def cancel(
        state: OMSState,
        order_id: OrderId,
        timestamp: float,
    ) -> OMSState:
        """Cancel an active order."""

        order = OMSEngine._get(
            state,
            order_id,
        )

        updated = order.cancel(
            timestamp,
        )

        event = OrderCancelled(
            event_id=str(new_id()),
            timestamp=timestamp,
            order_id=order_id,
        )

        return OMSEngine._record(
            state,
            updated,
            event,
        )

    # =====================================================
    # Expire
    # =====================================================

    @staticmethod
    def expire(
        state: OMSState,
        order_id: OrderId,
        timestamp: float,
    ) -> OMSState:
        """Expire an order."""

        order = OMSEngine._get(
            state,
            order_id,
        )

        updated = order.expire(
            timestamp,
        )

        event = OrderExpired(
            event_id=str(new_id()),
            timestamp=timestamp,
            order_id=order_id,
        )

        return OMSEngine._record(
            state,
            updated,
            event,
        )

    # =====================================================
    # Partial Fill
    # =====================================================

    @staticmethod
    def partial_fill(
        state: OMSState,
        order_id: OrderId,
        quantity: Decimal,
        price: Decimal,
        timestamp: float,
    ) -> OMSState:
        """Apply a partial execution."""

        order = OMSEngine._get(
            state,
            order_id,
        )

        updated = order.partial_fill(
            quantity,
            price,
            timestamp,
        )

        event = OrderPartiallyFilled(
            event_id=str(new_id()),
            timestamp=timestamp,
            order_id=order_id,
            fill_quantity=quantity,
            fill_price=price,
        )

        return OMSEngine._record(
            state,
            updated,
            event,
        )
        # =====================================================

    # Fill
    # =====================================================

    @staticmethod
    def fill(
        state: OMSState,
        order_id: OrderId,
        quantity: Decimal,
        price: Decimal,
        timestamp: float,
    ) -> OMSState:
        """Fully execute an order."""

        order = OMSEngine._get(
            state,
            order_id,
        )

        updated = order.fill(
            quantity,
            price,
            timestamp,
        )

        event = OrderFilled(
            event_id=str(new_id()),
            timestamp=timestamp,
            order_id=order_id,
            fill_quantity=quantity,
            fill_price=price,
        )

        return OMSEngine._record(
            state,
            updated,
            event,
        )

    # =====================================================
    # Replace
    # =====================================================

    @staticmethod
    def replace(
        state: OMSState,
        order_id: OrderId,
        quantity: Decimal,
        timestamp: float,
        limit_price: Decimal | None = None,
    ) -> OMSState:
        """Replace an existing open order."""

        order = OMSEngine._get(
            state,
            order_id,
        )

        updated = order.replace(
            new_qty=quantity,
            timestamp=timestamp,
            new_limit=limit_price,
        )

        event = OrderReplaced(
            event_id=str(new_id()),
            timestamp=timestamp,
            order_id=order_id,
            new_quantity=quantity,
            new_limit_price=limit_price,
        )

        return OMSEngine._record(
            state,
            updated,
            event,
        )
