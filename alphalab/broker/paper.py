"""The reference broker adapter: a deterministic paper venue.

:class:`PaperBroker` is a complete implementation of
:class:`~alphalab.broker.protocol.BrokerProtocol` with no external dependency,
which makes it two things at once: a usable paper-trading venue, and the
executable statement of what the contract requires. A real vendor adapter is
correct when it behaves like this one at the boundary.

It is a *venue*, not an accounting system. The cash and positions it tracks are
the venue's own books -- what a broker would report back -- and they exist so
reconciliation has something to compare against. AlphaLab's authoritative
accounting is :class:`~alphalab.portfolio.engine.PortfolioEngine`, reached
through the execution path; see :mod:`alphalab.runtime.session`.
"""

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from alphalab.broker.account import BrokerAccount
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
from alphalab.broker.order import BrokerOrder
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import (
    ExecutionDecision,
    ReconciliationLog,
    apply_execution,
    classify_execution,
)
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.validation import validate_cancel_request, validate_order_submission
from alphalab.common.ids import new_id
from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType
from alphalab.core.enums import Side as CoreSide


class PaperBroker:
    """Pure in-memory, deterministic broker simulation."""

    @staticmethod
    def _generate_id() -> str:
        return str(new_id())

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
        new_state = replace(state, events=(*state.events, evt), last_heartbeat=timestamp)
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

        updated_order = replace(order, status=CoreOrderStatus.ACCEPTED, updated_at=timestamp)
        new_orders[order.broker_order_id] = updated_order

        temp_state = replace(state, orders=new_orders)

        # Paper Broker simulates instant perfect fills for Market Orders
        if order.order_type == CoreOrderType.MARKET:
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
        updated_order = replace(order, status=CoreOrderStatus.CANCELLED, updated_at=timestamp)

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
        updated_order = replace(order, quantity=new_quantity, price=new_price, updated_at=timestamp)

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
            CoreOrderStatus.FILLED
            if new_filled >= order.quantity
            else CoreOrderStatus.PARTIALLY_FILLED
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
        if order.side == CoreSide.BUY:
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

        pos_qty_change = fill_qty if order.side == CoreSide.BUY else -fill_qty
        new_qty = current_pos.quantity + pos_qty_change

        if new_qty != Decimal("0.00") and order.side == CoreSide.BUY:
            pos_cost = (current_pos.quantity * current_pos.average_price) + (fill_qty * fill_price)
            new_pos_avg = pos_cost / new_qty
        else:
            new_pos_avg = current_pos.average_price

        realized_pnl = current_pos.realized_pnl
        if order.side == CoreSide.SELL and current_pos.quantity > Decimal("0.00"):
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

    def apply_execution(
        self, state: BrokerState, execution: BrokerExecution, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Apply a fill the venue reported, idempotently.

        Delegates the decision to :mod:`alphalab.broker.reconciliation`, so a
        redelivered fill is a no-op and a fill against a terminal or unknown
        order is refused rather than silently applied. A refused fill produces
        no event: nothing happened.
        """

        new_state, decision, _ = apply_execution(state, execution)
        if not decision.applied:
            return state, ()

        evt = ExecutionReceived(
            self._generate_id(),
            timestamp,
            execution.execution_id,
            execution.broker_order_id,
            execution.fill_quantity,
            execution.fill_price,
        )
        return replace(new_state, events=(*new_state.events, evt)), (evt,)

    def classify_execution(
        self, state: BrokerState, execution: BrokerExecution
    ) -> ExecutionDecision:
        """Why :meth:`apply_execution` would accept or refuse a fill."""

        return classify_execution(state, execution)

    def apply_execution_logged(
        self,
        state: BrokerState,
        execution: BrokerExecution,
        log: ReconciliationLog,
        timestamp: float,
    ) -> tuple[BrokerState, ExecutionDecision, ReconciliationLog]:
        """:meth:`apply_execution`, keeping every refusal in ``log``."""

        new_state, decision, new_log = apply_execution(state, execution, log)
        if not decision.applied:
            return state, decision, new_log

        evt = ExecutionReceived(
            self._generate_id(),
            timestamp,
            execution.execution_id,
            execution.broker_order_id,
            execution.fill_quantity,
            execution.fill_price,
        )
        return replace(new_state, events=(*new_state.events, evt)), decision, new_log

    def order_status(self, state: BrokerState, broker_order_id: str) -> BrokerOrder | None:
        """The order as this venue currently holds it."""

        return state.orders.get(broker_order_id)

    def account(self, state: BrokerState) -> BrokerAccount:
        """The venue's account snapshot."""

        return state.account

    def positions(self, state: BrokerState) -> Sequence[BrokerPosition]:
        """Open positions at this venue."""

        return tuple(p for p in state.positions.values() if p.quantity != Decimal("0"))

    def status(self, state: BrokerState) -> ConnectionStatus:
        """Current connectivity."""

        return state.connection_status
