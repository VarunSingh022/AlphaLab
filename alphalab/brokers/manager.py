"""Orchestration of order routing and execution settlement logic."""

from dataclasses import replace
from decimal import Decimal

from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.brokers.events import (
    BrokerEvent,
    ExecutionReceived,
    OrderCancelled,
    OrderFilled,
    OrderSubmitted,
)
from alphalab.brokers.exceptions import BrokerValidationError
from alphalab.brokers.order import OrderStatus
from alphalab.brokers.state import BrokerConnectorState
from alphalab.brokers.validation import (
    validate_execution,
    validate_order_cancellation,
    validate_order_submission,
)
from alphalab.common.ids import new_id
from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import Side as CoreSide


class OrderManager:
    """Facade for managing immutable order state transitions and executions."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def submit_order(
        state: BrokerConnectorState, order: BrokerOrder, timestamp: float
    ) -> BrokerConnectorState:
        """Validates and registers an outbound order."""
        validate_order_submission(state, order)

        # Keep connector-local SUBMITTED as a local staging state
        submitted_order = replace(order, status=OrderStatus.SUBMITTED, updated_at=timestamp)

        new_orders = dict(state.orders)
        new_orders[order.broker_order_id] = submitted_order

        evt = OrderSubmitted(
            OrderManager._create_id(),
            timestamp,
            order.broker_order_id,
            order.account_id,
            order.symbol,
        )

        new_stats = replace(
            state.statistics,
            total_orders_submitted=state.statistics.total_orders_submitted + 1,
        )

        return replace(state, orders=new_orders, statistics=new_stats, events=(*state.events, evt))

    @staticmethod
    def cancel_order(
        state: BrokerConnectorState, broker_order_id: str, timestamp: float
    ) -> BrokerConnectorState:
        """Marks an active order as cancelled."""
        order = validate_order_cancellation(state, broker_order_id)

        cancelled_order = replace(order, status=CoreOrderStatus.CANCELLED, updated_at=timestamp)

        new_orders = dict(state.orders)
        new_orders[broker_order_id] = cancelled_order

        evt = OrderCancelled(
            OrderManager._create_id(), timestamp, broker_order_id, order.account_id
        )

        return replace(state, orders=new_orders, events=(*state.events, evt))

    @staticmethod
    def process_execution(
        state: BrokerConnectorState, execution: BrokerExecution, timestamp: float
    ) -> BrokerConnectorState:
        """Deterministically settles a fill report against orders, accounts, and positions."""
        order = validate_execution(state, execution.execution_id, execution.broker_order_id)

        if execution.fill_quantity <= Decimal("0"):
            raise BrokerValidationError("Execution fill quantity must be positive.")

        # 1. Update Order
        new_filled_qty = order.filled_quantity + execution.fill_quantity
        if new_filled_qty > order.quantity:
            raise BrokerValidationError("Execution causes order to overfill.")

        total_cost = (order.filled_quantity * order.average_fill_price) + (
            execution.fill_quantity * execution.fill_price
        )
        new_avg_price = total_cost / new_filled_qty

        new_status = (
            CoreOrderStatus.FILLED
            if new_filled_qty == order.quantity
            else CoreOrderStatus.PARTIALLY_FILLED
        )

        updated_order = replace(
            order,
            filled_quantity=new_filled_qty,
            average_fill_price=new_avg_price,
            status=new_status,
            updated_at=timestamp,
        )

        # 2. Update Account Cash
        account = state.accounts[order.account_id]
        exec_value = execution.fill_quantity * execution.fill_price

        if order.side == CoreSide.BUY:
            new_cash = account.cash - exec_value - execution.commission
        else:
            new_cash = account.cash + exec_value - execution.commission

        updated_account = replace(account, cash=new_cash)

        # 3. Update Position
        pos_key = f"{order.account_id}:{order.symbol}"
        pos = state.positions.get(
            pos_key,
            BrokerPosition(
                symbol=order.symbol,
                quantity=Decimal("0"),
                average_price=Decimal("0"),
                market_value=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
                account_id=order.account_id,
            ),
        )

        if order.side == CoreSide.BUY:
            pos_new_qty = pos.quantity + execution.fill_quantity
            if pos_new_qty != Decimal("0"):
                pos_cost = (pos.quantity * pos.average_price) + exec_value
                pos_new_avg = pos_cost / pos_new_qty
            else:
                pos_new_avg = Decimal("0")
            realized = pos.realized_pnl
        else:
            pos_new_qty = pos.quantity - execution.fill_quantity
            pos_new_avg = pos.average_price
            realized = pos.realized_pnl + (
                execution.fill_quantity * (execution.fill_price - pos.average_price)
            )

        updated_position = replace(
            pos,
            quantity=pos_new_qty,
            average_price=pos_new_avg,
            realized_pnl=realized,
        )

        # 4. Assemble State
        new_orders = dict(state.orders)
        new_orders[order.broker_order_id] = updated_order

        new_accounts = dict(state.accounts)
        new_accounts[account.account_id] = updated_account

        new_positions = dict(state.positions)
        new_positions[pos_key] = updated_position

        new_executions = dict(state.executions)
        new_executions[execution.execution_id] = execution

        exec_evt = ExecutionReceived(
            OrderManager._create_id(),
            timestamp,
            execution.execution_id,
            order.broker_order_id,
            execution.fill_price,
            execution.fill_quantity,
        )

        events: list[BrokerEvent] = [exec_evt]
        if new_status == CoreOrderStatus.FILLED:
            fill_evt = OrderFilled(
                OrderManager._create_id(),
                timestamp,
                order.broker_order_id,
                order.account_id,
                new_filled_qty,
            )
            events.append(fill_evt)

        new_stats = replace(
            state.statistics,
            total_executions_received=state.statistics.total_executions_received + 1,
        )

        return replace(
            state,
            orders=new_orders,
            accounts=new_accounts,
            positions=new_positions,
            executions=new_executions,
            statistics=new_stats,
            events=(*state.events, *events),
        )
