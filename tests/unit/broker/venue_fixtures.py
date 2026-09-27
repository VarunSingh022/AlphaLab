"""Builders shared by the v3.9 venue-contract tests: a mirror, its orders, fills and events."""

from __future__ import annotations

from decimal import Decimal

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.lifecycle import VenueEvent
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.core.lifecycle import ExecutionEventKind

E = ExecutionEventKind


def account(
    cash: str = "100000", currency: str = "USD", account_id: str = "ACC-1"
) -> BrokerAccount:
    return BrokerAccount(
        account_id=account_id,
        cash=Decimal(cash),
        equity=Decimal(cash),
        buying_power=Decimal(cash),
        margin=Decimal("0"),
        available_funds=Decimal(cash),
        currency=currency,
    )


def order(
    broker_order_id: str = "B-1",
    quantity: str = "100",
    filled: str = "0",
    status: OrderStatus | BrokerOrderStatus = OrderStatus.ACCEPTED,
    price: str = "150",
    updated_at: float = 10.0,
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        oms_order_id=f"OMS-{broker_order_id}",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_quantity=Decimal(filled),
        average_fill_price=Decimal(price) if Decimal(filled) else Decimal("0"),
        status=status,
        created_at=1.0,
        updated_at=updated_at,
    )


def position(symbol: str = "AAPL", quantity: str = "100", account_id: str = "") -> BrokerPosition:
    return BrokerPosition(
        symbol=symbol,
        quantity=Decimal(quantity),
        average_price=Decimal("150"),
        market_value=Decimal(quantity) * Decimal("150"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        account_id=account_id,
    )


def execution(
    execution_id: str = "X-1",
    broker_order_id: str = "B-1",
    quantity: str = "40",
    price: str = "150",
    timestamp: float = 20.0,
) -> BrokerExecution:
    return BrokerExecution(
        execution_id=execution_id,
        broker_order_id=broker_order_id,
        symbol="AAPL",
        fill_quantity=Decimal(quantity),
        fill_price=Decimal(price),
        commission=Decimal("1"),
        timestamp=timestamp,
    )


def mirror(
    *orders: BrokerOrder,
    positions: tuple[BrokerPosition, ...] = (),
    executions: tuple[BrokerExecution, ...] = (),
    connection: ConnectionStatus = ConnectionStatus.CONNECTED,
    cash: str = "100000",
) -> BrokerState:
    return BrokerState(
        broker_name="VENUE",
        connection_status=connection,
        account=account(cash),
        positions=PersistentMap({p.symbol: p for p in positions}),
        orders=PersistentMap({o.broker_order_id: o for o in orders}),
        executions=PersistentMap({x.execution_id: x for x in executions}),
    )


def event(kind: ExecutionEventKind, broker_order_id: str = "B-1", at: float = 30.0) -> VenueEvent:
    return VenueEvent(kind=kind, timestamp=at, broker_order_id=broker_order_id)


def fill(
    execution_id: str = "X-1",
    quantity: str = "40",
    complete: bool = False,
    broker_order_id: str = "B-1",
    at: float = 20.0,
) -> VenueEvent:
    return VenueEvent(
        kind=E.ORDER_FILLED if complete else E.ORDER_PARTIALLY_FILLED,
        timestamp=at,
        broker_order_id=broker_order_id,
        execution=execution(execution_id, broker_order_id, quantity, timestamp=at),
    )
