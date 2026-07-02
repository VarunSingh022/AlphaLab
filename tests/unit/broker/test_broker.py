"""Comprehensive tests validating strict broker protocol semantics and logic."""

from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from alphalab.broker import (
    BrokerAdapter,
    BrokerEngine,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerState,
    BrokerValidationError,
    ConnectionStatus,
    InvalidBrokerStateError,
    PaperBroker,
    account_snapshot,
    executions,
    open_orders,
    positions,
)


@dataclass(frozen=True)
class MockOMSOrder:
    order_id: str
    asset_id: str
    side: str
    quantity: str
    price: str


@pytest.fixture
def default_state() -> BrokerState:
    return BrokerEngine.initialize("PAPER-1", Decimal("100000.00"), "USD")


@pytest.fixture
def base_oms_order() -> MockOMSOrder:
    return MockOMSOrder("OMS-1", "AAPL", "BUY", "100", "150.00")


def test_initialization(default_state: BrokerState) -> None:
    assert default_state.connection_status == ConnectionStatus.DISCONNECTED
    assert account_snapshot(default_state).cash == Decimal("100000.00")


def test_connect_disconnect(default_state: BrokerState) -> None:
    broker = PaperBroker()
    
    s1, evts1 = broker.connect(default_state, 1000.0)
    assert s1.connection_status == ConnectionStatus.CONNECTED
    assert len(evts1) == 1
    assert type(evts1[0]).__name__ == "BrokerConnected"

    s2, evts2 = broker.disconnect(s1, "Planned", 1001.0)
    assert s2.connection_status == ConnectionStatus.DISCONNECTED
    assert len(evts2) == 1
    assert type(evts2[0]).__name__ == "BrokerDisconnected"


def test_heartbeat(default_state: BrokerState) -> None:
    broker = PaperBroker()
    _s1, _evts = broker.heartbeat(default_state, 1000.0)
    assert len(_evts) == 1
    assert type(_evts[0]).__name__ == "Heartbeat"


def test_adapter_conversion(base_oms_order: MockOMSOrder) -> None:
    broker_order = BrokerAdapter.to_broker_order(
        base_oms_order, "B-1", BrokerOrderType.LIMIT, 1000.0
    )
    assert broker_order.broker_order_id == "B-1"
    assert broker_order.oms_order_id == "OMS-1"
    assert broker_order.quantity == Decimal("100")
    assert broker_order.price == Decimal("150.00")
    assert broker_order.side == BrokerOrderSide.BUY


def test_validation_negative_qty(
    default_state: BrokerState, base_oms_order: MockOMSOrder
) -> None:
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(base_oms_order, "B-1", BrokerOrderType.LIMIT, 1000.0)
    
    bad_order = replace(order, quantity=Decimal("-10"))

    with pytest.raises(BrokerValidationError, match="positive"):
        broker.submit_order(default_state, bad_order, 1000.0)


def test_submit_limit_order(
    default_state: BrokerState, base_oms_order: MockOMSOrder
) -> None:
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(base_oms_order, "B-1", BrokerOrderType.LIMIT, 1000.0)
    
    s1, evts = broker.submit_order(default_state, order, 1001.0)
    
    assert len(open_orders(s1)) == 1
    assert s1.orders["B-1"].status == BrokerOrderStatus.ACCEPTED
    assert len(evts) == 2
    assert type(evts[0]).__name__ == "OrderSubmitted"
    assert type(evts[1]).__name__ == "OrderAccepted"
    assert len(executions(s1)) == 0


def test_submit_market_order_fills_immediately(
    default_state: BrokerState, base_oms_order: MockOMSOrder
) -> None:
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(base_oms_order, "B-1", BrokerOrderType.MARKET, 1000.0)
    
    s1, _evts = broker.submit_order(default_state, order, 1001.0)
    
    # Market order instantly fills
    assert len(open_orders(s1)) == 0
    assert s1.orders["B-1"].status == BrokerOrderStatus.FILLED
    assert s1.orders["B-1"].filled_quantity == Decimal("100")
    
    assert len(executions(s1)) == 1
    assert executions(s1)[0].fill_quantity == Decimal("100")
    
    # 100000 - (100 * 150) = 85000
    assert account_snapshot(s1).cash == Decimal("85000.00")
    
    pos = positions(s1)
    assert len(pos) == 1
    assert pos[0].symbol == "AAPL"
    assert pos[0].quantity == Decimal("100")


def test_cancel_order(
    default_state: BrokerState, base_oms_order: MockOMSOrder
) -> None:
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(base_oms_order, "B-1", BrokerOrderType.LIMIT, 1000.0)
    
    s1, _ = broker.submit_order(default_state, order, 1001.0)
    s2, evts = broker.cancel_order(s1, "B-1", 1002.0)
    
    assert len(open_orders(s2)) == 0
    assert s2.orders["B-1"].status == BrokerOrderStatus.CANCELLED
    assert len(evts) == 1
    assert type(evts[0]).__name__ == "OrderCancelled"


def test_cancel_invalid_state(
    default_state: BrokerState, base_oms_order: MockOMSOrder
) -> None:
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(base_oms_order, "B-1", BrokerOrderType.MARKET, 1000.0)
    
    # Submitting market fills it immediately
    s1, _ = broker.submit_order(default_state, order, 1001.0)
    
    with pytest.raises(InvalidBrokerStateError, match="FILLED"):
        broker.cancel_order(s1, "B-1", 1002.0)


def test_replace_order(
    default_state: BrokerState, base_oms_order: MockOMSOrder
) -> None:
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(base_oms_order, "B-1", BrokerOrderType.LIMIT, 1000.0)
    
    s1, _ = broker.submit_order(default_state, order, 1001.0)
    s2, _ = broker.replace_order(s1, "B-1", Decimal("200"), Decimal("155.00"), 1002.0)
    
    assert s2.orders["B-1"].quantity == Decimal("200")
    assert s2.orders["B-1"].price == Decimal("155.00")


def test_account_and_position_math_sell(default_state: BrokerState) -> None:
    broker = PaperBroker()
    
    buy_oms = MockOMSOrder("OMS-1", "AAPL", "BUY", "100", "150.00")
    sell_oms = MockOMSOrder("OMS-2", "AAPL", "SELL", "50", "160.00")
    
    buy_ord = BrokerAdapter.to_broker_order(buy_oms, "B-1", BrokerOrderType.MARKET, 1000.0)
    sell_ord = BrokerAdapter.to_broker_order(sell_oms, "B-2", BrokerOrderType.MARKET, 1001.0)
    
    s1, _ = broker.submit_order(default_state, buy_ord, 1002.0)
    s2, _ = broker.submit_order(s1, sell_ord, 1003.0)
    
    pos = positions(s2)[0]
    
    # 100 bought, 50 sold -> 50 left
    assert pos.quantity == Decimal("50")
    
    # Cash: 100000 - 15000 (buy) + 8000 (sell) = 93000
    assert account_snapshot(s2).cash == Decimal("93000.00")
    
    # Realized PnL: sold 50 at 160 vs avg 150 = 50 * 10 = +500
    assert pos.realized_pnl == Decimal("500.00")


def test_immutability(
    default_state: BrokerState, base_oms_order: MockOMSOrder
) -> None:
    broker = PaperBroker()
    order = BrokerAdapter.to_broker_order(base_oms_order, "B-1", BrokerOrderType.LIMIT, 1000.0)
    
    s1, _ = broker.submit_order(default_state, order, 1001.0)
    
    assert len(default_state.orders) == 0
    assert len(s1.orders) == 1
    assert default_state is not s1