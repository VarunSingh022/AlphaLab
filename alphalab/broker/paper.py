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

What a paper fill costs (v3.11)
-------------------------------
Until v3.10 every paper fill carried a commission of zero ("a simplification
for paper broker"), so a paper run was systematically cheaper than the live run
it rehearsed and the expected/paper/live comparison was biased in one direction
(ledger BRK-008). A paper venue now takes the
:class:`~alphalab.execution.costs.ExecutionCostModel` -- the one cost authority
the simulator already uses -- with no default: the spread, slippage and impact
it states move the fill price, and the commission, fees and tax it states are
charged to cash, exactly as a simulated fill's are.
:data:`~alphalab.execution.costs.FREE` is the explicit statement that a paper
venue charges nothing.

Its books are a venue's books (v3.11)
-------------------------------------
v3.11 also corrected the venue's own ledger: a sell's commission was *added* to
cash, a short position carried no average price and realized nothing when it
was bought back, and a sell that crossed a long position through zero realized
profit on the whole fill rather than on the part that closed. Positions are now
kept at average cost on either side of zero -- a reduction realizes
``closed quantity * (fill - average)`` in the position's direction, and a fill
that crosses zero opens the remainder at the fill price.

A resting order rests
---------------------
A market order fills when submitted. A limit or stop order is accepted and
rests: this venue sees no market data, so nothing fills it until a caller
reports an execution through :meth:`PaperBroker.apply_execution`. Resting orders
that fill against simulated market data are the simulator's
(:mod:`alphalab.execution.simulator`), not this venue's.
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
from alphalab.broker.exceptions import BrokerValidationError
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
from alphalab.broker.validation import (
    validate_cancel_request,
    validate_order_submission,
    validate_replace_request,
)
from alphalab.common.ids import new_id
from alphalab.core.enums import OrderStatus as CoreOrderStatus
from alphalab.core.enums import OrderType as CoreOrderType
from alphalab.core.enums import Side as CoreSide
from alphalab.execution.costs import CostContext, ExecutionCostModel

_ZERO = Decimal("0")
_BOOK_QUANTUM = Decimal("0.0001")


def position_after_fill(
    position: BrokerPosition, side: CoreSide, quantity: Decimal, price: Decimal
) -> BrokerPosition:
    """A venue position after one fill, at average cost on either side of zero.

    Opening or adding moves the average to the quantity-weighted mean of the
    held and filled prices. Reducing realizes ``closed * (price - average)`` in
    the position's direction and leaves the average where it was. A fill that
    crosses zero closes the whole position -- realizing on that part only -- and
    opens the remainder at ``price``. Averages and realized P&L are kept to four
    decimal places, the venue's reporting precision.
    """

    signed = quantity if side is CoreSide.BUY else -quantity
    held = position.quantity
    realized = position.realized_pnl
    after = held + signed

    if held == _ZERO or (held > _ZERO) == (signed > _ZERO):
        average = (abs(held) * position.average_price + quantity * price) / abs(after)
    else:
        closed = min(abs(held), quantity)
        direction = Decimal(1) if held > _ZERO else Decimal(-1)
        realized += closed * (price - position.average_price) * direction
        if after == _ZERO:
            average = _ZERO
        elif (after > _ZERO) == (held > _ZERO):
            average = position.average_price
        else:
            average = price

    return replace(
        position,
        quantity=after,
        average_price=average.quantize(_BOOK_QUANTUM),
        realized_pnl=realized.quantize(_BOOK_QUANTUM),
    )


class PaperBroker:
    """Pure in-memory, deterministic broker simulation.

    Args:
        cost_model: What a fill here costs; see the module docstring. Required:
            ``FREE`` says a paper venue charges nothing, out loud.

    Raises:
        BrokerValidationError: If ``cost_model`` is not an
            :class:`~alphalab.execution.costs.ExecutionCostModel`.
    """

    def __init__(self, cost_model: ExecutionCostModel) -> None:
        if not isinstance(cost_model, ExecutionCostModel):
            raise BrokerValidationError(
                f"A paper venue needs the ExecutionCostModel its fills are charged under, got "
                f"{cost_model!r}. Pass alphalab.execution.costs.FREE to charge nothing."
            )
        self._cost_model = cost_model

    @property
    def cost_model(self) -> ExecutionCostModel:
        """What a fill at this venue costs."""

        return self._cost_model

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
            state, connection_status=ConnectionStatus.CONNECTED, events=state.events.append(evt)
        )
        return new_state, (evt,)

    def disconnect(
        self, state: BrokerState, reason: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        evt = BrokerDisconnected(self._generate_id(), timestamp, state.broker_name, reason)
        new_state = replace(
            state, connection_status=ConnectionStatus.DISCONNECTED, events=state.events.append(evt)
        )
        return new_state, (evt,)

    def heartbeat(
        self, state: BrokerState, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        evt = Heartbeat(self._generate_id(), timestamp, state.broker_name)
        new_state = replace(state, events=state.events.append(evt), last_heartbeat=timestamp)
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

        updated_order = replace(order, status=CoreOrderStatus.ACCEPTED, updated_at=timestamp)
        temp_state = replace(state, orders=state.orders.set(order.broker_order_id, updated_order))

        # A market order fills in full on submission, at its reference price
        # moved by the cost model's concessions.
        if order.order_type == CoreOrderType.MARKET:
            return self._simulate_fill(
                temp_state, updated_order, order.quantity, order.price, timestamp, tuple(events)
            )

        # Every other order rests; see the module docstring.
        return replace(temp_state, events=state.events.extend(events)), tuple(events)

    def cancel_order(
        self, state: BrokerState, broker_order_id: str, timestamp: float
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_cancel_request(state, broker_order_id)

        order = state.orders[broker_order_id]
        updated_order = replace(order, status=CoreOrderStatus.CANCELLED, updated_at=timestamp)

        evt = OrderCancelled(self._generate_id(), timestamp, broker_order_id)
        new_state = replace(
            state,
            orders=state.orders.set(broker_order_id, updated_order),
            events=state.events.append(evt),
        )

        return new_state, (evt,)

    def replace_order(
        self,
        state: BrokerState,
        broker_order_id: str,
        new_quantity: Decimal,
        new_price: Decimal,
        timestamp: float,
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        validate_replace_request(state, broker_order_id, new_quantity, new_price)

        order = state.orders[broker_order_id]
        updated_order = replace(order, quantity=new_quantity, price=new_price, updated_at=timestamp)

        # Simulated standard replacing behavior
        new_state = replace(state, orders=state.orders.set(broker_order_id, updated_order))
        return new_state, ()

    def _simulate_fill(
        self,
        state: BrokerState,
        order: BrokerOrder,
        fill_qty: Decimal,
        reference_price: Decimal,
        timestamp: float,
        existing_events: tuple[BrokerEvent, ...],
    ) -> tuple[BrokerState, tuple[BrokerEvent, ...]]:
        """Fill ``fill_qty`` of ``order`` under the venue's cost model, and book it."""

        context = CostContext(
            asset_id=order.symbol,
            side=order.side,
            quantity=fill_qty,
            reference_price=reference_price,
            currency=state.account.currency,
            venue=state.broker_name,
            timestamp=timestamp,
        )
        costs = self._cost_model.quote(context)
        fill_price = self._cost_model.fill_price(context, costs)
        commission = costs.cash_charged

        exec_id = f"EXEC-{self._generate_id()}"
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

        # 1. The order
        new_filled = order.filled_quantity + fill_qty
        new_status = (
            CoreOrderStatus.FILLED
            if new_filled >= order.quantity
            else CoreOrderStatus.PARTIALLY_FILLED
        )
        total_notional = (order.filled_quantity * order.average_fill_price) + (
            fill_qty * fill_price
        )
        new_avg_price = total_notional / new_filled if new_filled > _ZERO else _ZERO
        updated_order = replace(
            order,
            filled_quantity=new_filled,
            average_fill_price=new_avg_price.quantize(_BOOK_QUANTUM),
            status=new_status,
            updated_at=timestamp,
        )

        # 2. Cash: the notional moves one way, the charges always leave.
        notional = fill_qty * fill_price
        if order.side == CoreSide.BUY:
            new_cash = state.account.cash - notional - commission
        else:
            new_cash = state.account.cash + notional - commission
        updated_account = replace(state.account, cash=new_cash)

        # 3. The position, at average cost on either side of zero.
        current = state.positions.get(
            order.symbol,
            BrokerPosition(order.symbol, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO),
        )
        updated_position = position_after_fill(current, order.side, fill_qty, fill_price)

        events_out = (*existing_events, exec_evt)
        new_state = replace(
            state,
            orders=state.orders.set(order.broker_order_id, updated_order),
            executions=state.executions.set(exec_id, execution),
            account=updated_account,
            positions=state.positions.set(order.symbol, updated_position),
            events=state.events.extend(events_out),
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
        return replace(new_state, events=new_state.events.append(evt)), (evt,)

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
        return replace(new_state, events=new_state.events.append(evt)), decision, new_log

    def order_status(self, state: BrokerState, broker_order_id: str) -> BrokerOrder | None:
        """The order as this venue currently holds it."""

        return state.orders.get(broker_order_id)

    def account(self, state: BrokerState) -> BrokerAccount:
        """The venue's account snapshot."""

        return state.account

    def positions(self, state: BrokerState) -> Sequence[BrokerPosition]:
        """Open positions at this venue."""

        return tuple(p for p in state.positions.values() if p.quantity != _ZERO)

    def status(self, state: BrokerState) -> ConnectionStatus:
        """Current connectivity."""

        return state.connection_status
