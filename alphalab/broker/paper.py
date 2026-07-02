"""Deterministic Paper Broker implementation."""

import uuid
from dataclasses import replace
from decimal import Decimal

from alphalab.broker.events import (
    BrokerConnected,
    BrokerDisconnected,
    BrokerEvent,
    ExecutionReceived,
    Heartbeat,
    OrderAccepted,
    OrderCancelled,
    OrderSubmitted,
)
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderSide, BrokerOrderStatus, BrokerOrderType
from alphalab.broker.position import BrokerPosition
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.validation import validate_cancel_request, validate_order_submission


class PaperBroker:
    """Pure in-memory, deterministic broker simulation."""

    @staticmethod
    def _generate_id() -> str:
        return str(uuid.uuid4())

    def connect(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        if state.connection_status == ConnectionStatus.CONNECTED:
            return state, ()
            
        evt = BrokerConnected(self._generate_id(), timestamp, state.broker_name)
        new_state = replace(
            state, connection_status=ConnectionStatus.CONNECTED, events=(*state.events, evt)
        )
        return new_state, (evt,)

    def disconnect(
        self, state: BrokerState, reason: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        evt = BrokerDisconnected(self._generate_id(), timestamp, state.broker_name, reason)
        new_state = replace(
            state, connection_status=ConnectionStatus.DISCONNECTED, events=(*state.events, evt)
        )
        return new_state, (evt,)

    def heartbeat(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        evt = Heartbeat(self._generate_id(), timestamp, state.broker_name)
        new_state = replace(state, events=(*state.events, evt))
        return new_state, (evt,)

    def submit_order(
        self, state: BrokerState, order: BrokerOrder, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_order_submission(state, order)

        sub_evt = OrderSubmitted(
            self._generate_id(), timestamp, order.broker_order_id, order.oms_order_id
        )
        acc_evt = OrderAccepted(self._generate_id(), timestamp, order.broker_order_id)
        
        events: list[BrokerEvent] = [sub_evt, acc_evt]
        new_orders = dict(state.orders)
        
        updated_order = replace(order, status=BrokerOrderStatus.ACCEPTED, updated_at=timestamp)
        new_orders[order.broker_order_id] = updated_order
        
        temp_state = replace(state, orders=new_orders)

        # Paper Broker simulates instant perfect fills for Market Orders
        if order.order_type == BrokerOrderType.MARKET:
            return self._simulate_fill(
                temp_state, updated_order, order.quantity, order.price, timestamp, tuple(events)
            )

        # Limit orders rest in the book
        return replace(temp_state, events=(*state.events, *events)), tuple(events)

    def cancel_order(
        self, state: BrokerState, broker_order_id: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_cancel_request(state, broker_order_id)
        
        order = state.orders[broker_order_id]
        updated_order = replace(order, status=BrokerOrderStatus.CANCELLED, updated_at=timestamp)
        
        new_orders = dict(state.orders)
        new_orders[broker_order_id] = updated_order
        
        evt = OrderCancelled(self._generate_id(), timestamp, broker_order_id)
        new_state = replace(state, orders=new_orders, events=(*state.events, evt))
        
        return new_state, (evt,)

    def replace_order(
        self, 
        state: BrokerState, 
        broker_order_id: str, 
        new_quantity: Decimal, 
        new_price: Decimal, 
        timestamp: float,
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_cancel_request(state, broker_order_id)  # Same validation logic applies
        
        order = state.orders[broker_order_id]
        updated_order = replace(
            order, quantity=new_quantity, price=new_price, updated_at=timestamp
        )
        
        new_orders = dict(state.orders)
        new_orders[broker_order_id] = updated_order
        
        # Simulated standard replacing behavior
        new_state = replace(state, orders=new_orders)
        return new_state, ()

    def _simulate_fill(
        self, 
        state: BrokerState, 
        order: BrokerOrder, 
        fill_qty: Decimal, 
        fill_price: Decimal, 
        timestamp: float, 
        existing_events: tuple[BrokerEvent, ...],
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Internal pure function simulating fill accounting and execution generation."""
        exec_id = f"EXEC-{self._generate_id()}"
        commission = Decimal("0.00")  # Simplification for paper broker
        
        execution = BrokerExecution(
            execution_id=exec_id,
            broker_order_id=order.broker_order_id,
            symbol=order.symbol,
            fill_quantity=fill_qty,
            fill_price=fill_price,
            commission=commission,
            timestamp=timestamp,
        )

        exec_evt = ExecutionReceived(
            self._generate_id(), timestamp, exec_id, order.broker_order_id, fill_qty, fill_price
        )

        # 1. Update Order
        new_filled = order.filled_quantity + fill_qty
        new_status = (
            BrokerOrderStatus.FILLED 
            if new_filled >= order.quantity 
            else BrokerOrderStatus.PARTIALLY_FILLED
        )
        
        total_cost = (order.filled_quantity * order.average_fill_price) + (fill_qty * fill_price)
        new_avg_price = total_cost / new_filled if new_filled > Decimal("0") else Decimal("0.00")
        
        updated_order = replace(
            order,
            filled_quantity=new_filled,
            average_fill_price=new_avg_price.quantize(Decimal("0.0001")),
            status=new_status,
            updated_at=timestamp,
        )

        new_orders = dict(state.orders)
        new_orders[order.broker_order_id] = updated_order

        new_executions = dict(state.executions)
        new_executions[exec_id] = execution

        # 2. Update Account
        cost_impact = (fill_qty * fill_price) + commission
        if order.side == BrokerOrderSide.BUY:
            new_cash = state.account.cash - cost_impact
        else:
            new_cash = state.account.cash + cost_impact

        updated_account = replace(state.account, cash=new_cash)

        # 3. Update Position
        new_positions = dict(state.positions)
        current_pos = new_positions.get(
            order.symbol,
            BrokerPosition(
                order.symbol,
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
            ),
        )

        pos_qty_change = fill_qty if order.side == BrokerOrderSide.BUY else -fill_qty
        new_qty = current_pos.quantity + pos_qty_change

        if new_qty != Decimal("0.00") and order.side == BrokerOrderSide.BUY:
            pos_cost = (current_pos.quantity * current_pos.average_price) + (fill_qty * fill_price)
            new_pos_avg = pos_cost / new_qty
        else:
            new_pos_avg = current_pos.average_price

        realized_pnl = current_pos.realized_pnl
        if order.side == BrokerOrderSide.SELL and current_pos.quantity > Decimal("0.00"):
            realized_pnl += fill_qty * (fill_price - current_pos.average_price)

        updated_position = replace(
            current_pos,
            quantity=new_qty,
            average_price=new_pos_avg.quantize(Decimal("0.0001")),
            realized_pnl=realized_pnl.quantize(Decimal("0.0001")),
        )
        new_positions[order.symbol] = updated_position

        # Re-assemble
        events_out = (*existing_events, exec_evt)
        new_state = replace(
            state,
            orders=new_orders,
            executions=new_executions,
            account=updated_account,
            positions=new_positions,
            events=(*state.events, *events_out),
        )

        return new_state, events_out