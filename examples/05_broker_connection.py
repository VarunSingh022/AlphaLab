"""
AlphaLab Examples
=================

Example 05 : Broker Connectors

Difficulty : Intermediate

Estimated Time : 5 minutes

Prerequisites
-------------

✓ Example 04

Topics
------

• Two boundaries: one venue, or a registry of many
• Registering and connecting broker endpoints
• Routing canonical orders and settling canonical fills
• Immutable connector state

What changed in v2.17
---------------------

This example used ``alphalab.integrations``, which was deprecated in v2.6 and
removed in v2.17 (ADR-0034). It now uses the boundary the execution path
actually routes through.

There are two, and knowing which is which is the point of this example:

``alphalab.broker``
    *One* venue. ``BrokerProtocol`` is the canonical adapter contract --
    ``RestVenueBroker`` and ``PaperBroker`` implement it, and
    ``alphalab.runtime.broker_routing`` and ``LiveSession`` speak it. Every
    method takes a ``BrokerState``, which is one broker's books.

``alphalab.brokers``
    *Many* venues and many accounts. ``BrokerConnectorProtocol`` is the routing
    contract over a ``BrokerConnectorState``, which is why its queries take an
    ``account_id`` that the single-venue boundary has no need of. Its domain
    values -- ``BrokerOrder``, ``BrokerExecution``, ``BrokerAccount``,
    ``BrokerPosition`` -- *are* the ``alphalab.broker`` types; this package adds
    only which broker and which account each belongs to.

Run

    python examples/05_broker_connection.py
"""

from decimal import Decimal

from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder
from alphalab.brokers import (
    BrokerConnection,
    BrokerConnectorEngine,
    BrokerType,
    active_brokers,
    engine_statistics,
    get_account,
    list_executions,
    open_orders,
)
from alphalab.core.enums import OrderStatus, OrderType, Side, TimeInForce

ACCOUNT_ID = "ACC-PAPER-1"
BROKER_ID = "PAPER-BROKER"


def main() -> None:
    """Register a broker, route an order, and settle the fill that comes back."""

    # ------------------------------------------------------------
    # Step 1 : Initialize the connector state
    # ------------------------------------------------------------

    state = BrokerConnectorEngine.initialize(engine_id="CONNECTOR-001")

    # ------------------------------------------------------------
    # Step 2 : Register a broker endpoint
    # ------------------------------------------------------------

    connection = BrokerConnection(
        broker_id=BROKER_ID,
        broker_name="Demo Paper Venue",
        broker_type=BrokerType.PAPER,
    )

    state = BrokerConnectorEngine.register_broker(state, connection, timestamp=1_720_000_000.0)
    state = BrokerConnectorEngine.connect_broker(state, BROKER_ID, timestamp=1_720_000_001.0)

    # ------------------------------------------------------------
    # Step 3 : Record the account this broker holds
    # ------------------------------------------------------------
    #
    # Every amount is a Decimal and every account names its own currency.
    # AlphaLab never infers one: see ADR-0019 and ADR-0028.

    state = BrokerConnectorEngine.add_account(
        state,
        BrokerAccount(
            account_id=ACCOUNT_ID,
            cash=Decimal("1000000.00"),
            equity=Decimal("1000000.00"),
            buying_power=Decimal("2000000.00"),
            margin=Decimal("0.00"),
            available_funds=Decimal("1000000.00"),
            currency="USD",
            broker_id=BROKER_ID,
        ),
    )

    # ------------------------------------------------------------
    # Step 4 : Route a canonical order
    # ------------------------------------------------------------

    order = BrokerOrder(
        broker_order_id="BRK-ORDER-001",
        oms_order_id="OMS-ORDER-001",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("100"),
        price=Decimal("190.25"),
        filled_quantity=Decimal("0"),
        average_fill_price=Decimal("0"),
        status=OrderStatus.PENDING,
        created_at=1_720_000_002.0,
        updated_at=1_720_000_002.0,
        account_id=ACCOUNT_ID,
        tif=TimeInForce.DAY,
    )

    state = BrokerConnectorEngine.submit_order(state, order, timestamp=1_720_000_002.0)

    # ------------------------------------------------------------
    # Step 5 : Settle the fill the venue reported
    # ------------------------------------------------------------
    #
    # A fill is applied by execution_id, and applying the same one twice is
    # refused -- a venue may redeliver after a reconnect.

    state = BrokerConnectorEngine.process_execution(
        state,
        BrokerExecution(
            execution_id="EXEC-001",
            broker_order_id="BRK-ORDER-001",
            account_id=ACCOUNT_ID,
            symbol="AAPL",
            fill_quantity=Decimal("100"),
            fill_price=Decimal("190.25"),
            commission=Decimal("1.00"),
            timestamp=1_720_000_003.0,
        ),
        timestamp=1_720_000_003.0,
    )

    # ------------------------------------------------------------
    # Step 6 : Inspect the state
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 05")
    print("Broker Connectors")
    print("=" * 60)
    print()

    brokers = active_brokers(state)
    print(f"Registered Brokers : {len(brokers)}")
    for registered in brokers:
        status = "connected" if registered.connected else "disconnected"
        print(f"  {registered.broker_id} ({registered.broker_name}) : {status}")

    account = get_account(state, ACCOUNT_ID)
    assert account is not None
    print()
    print(f"Account            : {account.account_id}")
    print(f"Cash               : {account.cash} {account.currency}")
    print(f"Buying Power       : {account.buying_power} {account.currency}")

    executions = list_executions(state, "BRK-ORDER-001")
    print()
    print(f"Executions Applied : {len(executions)}")
    for execution in executions:
        print(
            f"  {execution.execution_id}: {execution.fill_quantity} "
            f"{execution.symbol} @ {execution.fill_price}"
        )

    statistics = engine_statistics(state)
    print()
    print("Statistics")
    print(f"Orders Submitted   : {statistics.total_orders_submitted}")
    print(f"Executions Received: {statistics.total_executions_received}")
    print(f"Errors             : {statistics.total_errors}")

    print()
    print(f"Open Orders        : {len(open_orders(state))}")
    print(f"Connector Events   : {len(state.events)}")


if __name__ == "__main__":
    main()
