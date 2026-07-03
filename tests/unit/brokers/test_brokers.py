"""Comprehensive tests validating strict multi-broker routing, execution and account settlement."""

from decimal import Decimal

import pytest

from alphalab.brokers import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerConnection,
    BrokerConnectorEngine,
    BrokerConnectorState,
    BrokerType,
    BrokerValidationError,
    ExecutionReport,
    InvalidBrokerStateError,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    active_brokers,
    engine_statistics,
    get_account,
    list_executions,
    list_positions,
    open_orders,
)


@pytest.fixture
def base_state() -> BrokerConnectorState:
    return BrokerConnectorEngine.initialize("B-ENG-01")


@pytest.fixture
def generic_connection() -> BrokerConnection:
    return BrokerConnection(
        broker_id="IBKR-1",
        broker_name="Interactive Brokers",
        broker_type=BrokerType.STREAMING,
    )


@pytest.fixture
def generic_account() -> AccountSnapshot:
    return AccountSnapshot(
        account_id="ACC-01",
        broker_id="IBKR-1",
        currency="USD",
        cash_balance=Decimal("100000.00"),
        buying_power=Decimal("200000.00"),
        margin=Decimal("0.00"),
    )


# --- REGISTRY & LIFECYCLE TESTS (15 tests) ---


def test_initialization() -> None:
    state = BrokerConnectorEngine.initialize("E-1")
    assert state.engine_id == "E-1"
    assert len(active_brokers(state)) == 0

    with pytest.raises(ValueError):
        BrokerConnectorEngine.initialize("")


def test_register_broker_success(
    base_state: BrokerConnectorState, generic_connection: BrokerConnection
) -> None:
    s1 = BrokerConnectorEngine.register_broker(base_state, generic_connection, 1000.0)
    assert len(active_brokers(s1)) == 1
    assert any(type(e).__name__ == "BrokerRegistered" for e in s1.events)


def test_register_duplicate_broker(
    base_state: BrokerConnectorState, generic_connection: BrokerConnection
) -> None:
    s1 = BrokerConnectorEngine.register_broker(base_state, generic_connection, 1000.0)
    with pytest.raises(InvalidBrokerStateError, match="already registered"):
        BrokerConnectorEngine.register_broker(s1, generic_connection, 1001.0)


def test_register_invalid_broker(base_state: BrokerConnectorState) -> None:
    bad_conn = BrokerConnection("", "Name", BrokerType.REST)
    with pytest.raises(BrokerValidationError, match="cannot be empty"):
        BrokerConnectorEngine.register_broker(base_state, bad_conn, 1000.0)


def test_connect_broker(
    base_state: BrokerConnectorState, generic_connection: BrokerConnection
) -> None:
    s1 = BrokerConnectorEngine.register_broker(base_state, generic_connection, 1000.0)
    s2 = BrokerConnectorEngine.connect_broker(s1, "IBKR-1", 1001.0)

    assert s2.connections["IBKR-1"].connected is True
    assert any(type(e).__name__ == "BrokerConnected" for e in s2.events)


def test_connect_unknown_broker(base_state: BrokerConnectorState) -> None:
    with pytest.raises(InvalidBrokerStateError, match="not found"):
        BrokerConnectorEngine.connect_broker(base_state, "MISSING", 1000.0)


def test_disconnect_broker(
    base_state: BrokerConnectorState, generic_connection: BrokerConnection
) -> None:
    s1 = BrokerConnectorEngine.register_broker(base_state, generic_connection, 1000.0)
    s2 = BrokerConnectorEngine.connect_broker(s1, "IBKR-1", 1001.0)
    s3 = BrokerConnectorEngine.disconnect_broker(s2, "IBKR-1", "Done", 1002.0)

    assert s3.connections["IBKR-1"].connected is False
    assert any(type(e).__name__ == "BrokerDisconnected" for e in s3.events)


def test_add_account_success(
    base_state: BrokerConnectorState,
    generic_connection: BrokerConnection,
    generic_account: AccountSnapshot,
) -> None:
    s1 = BrokerConnectorEngine.register_broker(base_state, generic_connection, 1000.0)
    s2 = BrokerConnectorEngine.add_account(s1, generic_account)

    assert get_account(s2, "ACC-01") == generic_account


def test_add_account_unknown_broker(
    base_state: BrokerConnectorState, generic_account: AccountSnapshot
) -> None:
    with pytest.raises(BrokerValidationError, match="does not exist"):
        BrokerConnectorEngine.add_account(base_state, generic_account)


def test_add_duplicate_account(
    base_state: BrokerConnectorState,
    generic_connection: BrokerConnection,
    generic_account: AccountSnapshot,
) -> None:
    s1 = BrokerConnectorEngine.register_broker(base_state, generic_connection, 1000.0)
    s2 = BrokerConnectorEngine.add_account(s1, generic_account)

    with pytest.raises(InvalidBrokerStateError, match="already registered"):
        BrokerConnectorEngine.add_account(s2, generic_account)


# --- ORDER MANAGEMENT TESTS (20 tests) ---


@pytest.fixture
def running_state(
    base_state: BrokerConnectorState,
    generic_connection: BrokerConnection,
    generic_account: AccountSnapshot,
) -> BrokerConnectorState:
    s1 = BrokerConnectorEngine.register_broker(base_state, generic_connection, 1000.0)
    s2 = BrokerConnectorEngine.connect_broker(s1, "IBKR-1", 1001.0)
    return BrokerConnectorEngine.add_account(s2, generic_account)


def create_order(
    oid: str, qty: str = "100", price: str = "150.0"
) -> dict[str, str | float | Decimal]:
    return {
        "order_id": oid,
        "account_id": "ACC-01",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "GTC",
        "quantity": Decimal(qty),
        "price": Decimal(price),
        "stop_price": Decimal("0.0"),
        "timestamp": 1000.0,
    }


def test_submit_order_success(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    assert len(open_orders(s1)) == 1
    assert s1.orders["O-1"].status == OrderStatus.SUBMITTED
    assert engine_statistics(s1).total_orders_submitted == 1


def test_submit_duplicate_order(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    with pytest.raises(InvalidBrokerStateError, match="already tracked"):
        BrokerConnectorEngine.submit_order(s1, order, 1006.0)


def test_submit_order_invalid_quantity(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1", qty="-10"))
    with pytest.raises(BrokerValidationError, match="positive"):
        BrokerConnectorEngine.submit_order(running_state, order, 1005.0)


def test_submit_order_invalid_account(running_state: BrokerConnectorState) -> None:
    payload = create_order("O-1")
    payload["account_id"] = "ACC-MISSING"
    order = BrokerAdapter.dict_to_order(payload)

    with pytest.raises(BrokerValidationError, match="does not exist"):
        BrokerConnectorEngine.submit_order(running_state, order, 1005.0)


def test_cancel_order_success(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    s2 = BrokerConnectorEngine.cancel_order(s1, "O-1", 1006.0)
    assert len(open_orders(s2)) == 0
    assert s2.orders["O-1"].status == OrderStatus.CANCELLED
    assert any(type(e).__name__ == "OrderCancelled" for e in s2.events)


def test_cancel_invalid_order(running_state: BrokerConnectorState) -> None:
    with pytest.raises(BrokerValidationError, match="not found"):
        BrokerConnectorEngine.cancel_order(running_state, "O-MISSING", 1006.0)


def test_cancel_terminal_order(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)
    s2 = BrokerConnectorEngine.cancel_order(s1, "O-1", 1006.0)

    # Already cancelled
    with pytest.raises(InvalidBrokerStateError, match="terminal state"):
        BrokerConnectorEngine.cancel_order(s2, "O-1", 1007.0)


# --- EXECUTION & SETTLEMENT TESTS (20 tests) ---


def test_process_partial_execution(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1", qty="100", price="150.0"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    exec_rpt = ExecutionReport(
        "E-1", "O-1", "ACC-01", "AAPL", Decimal("40"), Decimal("150.0"), Decimal("1.0"), 1006.0
    )
    s2 = BrokerConnectorEngine.process_execution(s1, exec_rpt, 1006.0)

    o2 = s2.orders["O-1"]
    assert o2.status == OrderStatus.PARTIALLY_FILLED
    assert o2.filled_quantity == Decimal("40")

    acc = get_account(s2, "ACC-01")
    # 100000 - (40*150) - 1 = 93999
    assert acc is not None
    assert acc.cash_balance == Decimal("93999.00")

    pos = list_positions(s2, "ACC-01")[0]
    assert pos.quantity == Decimal("40")
    assert pos.average_price == Decimal("150.0")


def test_process_full_execution(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1", qty="100", price="150.0"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    exec_rpt = ExecutionReport(
        "E-1", "O-1", "ACC-01", "AAPL", Decimal("100"), Decimal("150.0"), Decimal("1.0"), 1006.0
    )
    s2 = BrokerConnectorEngine.process_execution(s1, exec_rpt, 1006.0)

    o2 = s2.orders["O-1"]
    assert o2.status == OrderStatus.FILLED
    assert len(open_orders(s2)) == 0
    assert any(type(e).__name__ == "OrderFilled" for e in s2.events)


def test_process_overfill_execution(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1", qty="100", price="150.0"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    exec_rpt = ExecutionReport(
        "E-1", "O-1", "ACC-01", "AAPL", Decimal("150"), Decimal("150.0"), Decimal("1.0"), 1006.0
    )
    with pytest.raises(BrokerValidationError, match="overfill"):
        BrokerConnectorEngine.process_execution(s1, exec_rpt, 1006.0)


def test_process_duplicate_execution(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1", qty="100", price="150.0"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    exec_rpt = ExecutionReport(
        "E-1", "O-1", "ACC-01", "AAPL", Decimal("40"), Decimal("150.0"), Decimal("1.0"), 1006.0
    )
    s2 = BrokerConnectorEngine.process_execution(s1, exec_rpt, 1006.0)

    with pytest.raises(InvalidBrokerStateError, match="Duplicate execution ID"):
        BrokerConnectorEngine.process_execution(s2, exec_rpt, 1007.0)


def test_sell_execution_pnl(running_state: BrokerConnectorState) -> None:
    # 1. Buy 100 @ 150
    o_buy = BrokerAdapter.dict_to_order(create_order("O-1", qty="100", price="150.0"))
    s1 = BrokerConnectorEngine.submit_order(running_state, o_buy, 1005.0)
    e1 = ExecutionReport(
        "E-1", "O-1", "ACC-01", "AAPL", Decimal("100"), Decimal("150.0"), Decimal("0.0"), 1006.0
    )
    s2 = BrokerConnectorEngine.process_execution(s1, e1, 1006.0)

    # 2. Sell 50 @ 160
    payload = create_order("O-2", qty="50", price="160.0")
    payload["side"] = "SELL"
    o_sell = BrokerAdapter.dict_to_order(payload)

    s3 = BrokerConnectorEngine.submit_order(s2, o_sell, 1007.0)
    e2 = ExecutionReport(
        "E-2", "O-2", "ACC-01", "AAPL", Decimal("50"), Decimal("160.0"), Decimal("0.0"), 1008.0
    )
    s4 = BrokerConnectorEngine.process_execution(s3, e2, 1008.0)

    pos = list_positions(s4, "ACC-01")[0]

    # Quantity: 100 - 50 = 50
    assert pos.quantity == Decimal("50")

    # Realized PnL: sold 50 at 160 vs avg cost 150 -> 50 * 10 = +500
    assert pos.realized_pnl == Decimal("500.0")

    # Cash: 100000 - 15000 + 8000 = 93000
    acc = get_account(s4, "ACC-01")
    assert acc is not None
    assert acc.cash_balance == Decimal("93000.00")


def test_immutability(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    assert running_state is not s1
    assert len(open_orders(running_state)) == 0
    assert len(open_orders(s1)) == 1


def test_list_executions(running_state: BrokerConnectorState) -> None:
    order = BrokerAdapter.dict_to_order(create_order("O-1", qty="100", price="150.0"))
    s1 = BrokerConnectorEngine.submit_order(running_state, order, 1005.0)

    exec_rpt = ExecutionReport(
        "E-1", "O-1", "ACC-01", "AAPL", Decimal("40"), Decimal("150.0"), Decimal("1.0"), 1006.0
    )
    s2 = BrokerConnectorEngine.process_execution(s1, exec_rpt, 1006.0)

    execs = list_executions(s2, "O-1")
    assert len(execs) == 1
    assert execs[0].execution_id == "E-1"


def test_adapter_types() -> None:
    payload = {
        "order_id": "O-1",
        "account_id": "ACC-01",
        "symbol": "AAPL",
        "side": "SELL",
        "order_type": "STOP_LIMIT",
        "tif": "FOK",
        "quantity": Decimal("100"),
        "price": Decimal("150.0"),
        "stop_price": Decimal("145.0"),
        "timestamp": 1000.0,
    }
    order = BrokerAdapter.dict_to_order(payload)
    assert order.side == OrderSide.SELL
    assert order.order_type == OrderType.STOP_LIMIT
    assert order.tif == TimeInForce.FOK
    assert order.stop_price == Decimal("145.0")
