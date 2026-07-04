"""Comprehensive tests validating strict multi-broker integrations."""

from typing import Any

import pytest

from alphalab.integrations import (
    AuthStatus,
    BrokerConfig,
    ConnectionManagerError,
    ConnectionStatus,
    IntegrationAdapter,
    IntegrationEngine,
    IntegrationState,
    IntegrationValidationError,
    PortfolioSynchronized,
    authentication_status,
    broker_summary,
    connection_status,
    metrics_report,
)
from alphalab.integrations.alpaca import AlpacaAdapter
from alphalab.integrations.alpaca.config import AlpacaConfig
from alphalab.integrations.interactivebrokers import InteractiveBrokersAdapter
from alphalab.integrations.interactivebrokers.config import InteractiveBrokersConfig
from alphalab.integrations.paper import PaperAdapter
from alphalab.integrations.paper.config import PaperConfig
from alphalab.integrations.zerodha import ZerodhaAdapter
from alphalab.integrations.zerodha.config import ZerodhaConfig


@pytest.fixture
def base_state() -> IntegrationState:
    return IntegrationEngine.initialize("INT-ENG-01")


@pytest.fixture
def paper_config() -> BrokerConfig:
    return BrokerConfig("PAPER-1", "PaperBroker", "paper", "http://localhost")


@pytest.fixture
def paper_provider() -> PaperAdapter:
    return PaperAdapter(PaperConfig("k", "s"))


# --- REGISTRATION TESTS ---

def test_engine_initialization() -> None:
    state = IntegrationEngine.initialize("E1")
    assert state.engine_id == "E1"
    assert len(broker_summary(state)) == 0

    with pytest.raises(ValueError):
        IntegrationEngine.initialize("")


def test_register_broker(base_state: IntegrationState, paper_config: BrokerConfig) -> None:
    s1 = IntegrationEngine.register(base_state, paper_config)
    assert len(broker_summary(s1)) == 1
    auth = authentication_status(s1, "PAPER-1")
    assert auth is not None
    assert auth.status == AuthStatus.UNAUTHENTICATED

def test_register_duplicate(base_state: IntegrationState, paper_config: BrokerConfig) -> None:
    s1 = IntegrationEngine.register(base_state, paper_config)
    with pytest.raises(IntegrationValidationError, match="already registered"):
        IntegrationEngine.register(s1, paper_config)


def test_register_empty_id(base_state: IntegrationState) -> None:
    cfg = BrokerConfig("", "PaperBroker", "paper", "http://localhost")
    with pytest.raises(IntegrationValidationError, match="cannot be empty"):
        IntegrationEngine.register(base_state, cfg)


# --- AUTHENTICATION & CONNECTION TESTS ---

def test_authentication_success(
    base_state: IntegrationState, paper_config: BrokerConfig, paper_provider: PaperAdapter
) -> None:
    s1 = IntegrationEngine.register(base_state, paper_config)
    s2 = IntegrationEngine.authenticate(s1, "PAPER-1", paper_provider, {"api_key": "k"}, 1000.0)

    auth = authentication_status(s1, "PAPER-1")
    assert auth is not None
    assert auth.status == AuthStatus.UNAUTHENTICATED
    assert any(type(e).__name__ == "AuthenticationSucceeded" for e in s2.events)


def test_authentication_failure(base_state: IntegrationState) -> None:
    cfg = BrokerConfig("ZD-1", "Zerodha", "live", "api")
    s1 = IntegrationEngine.register(base_state, cfg)

    zd_provider = ZerodhaAdapter(ZerodhaConfig("k", "s"))
    s2 = IntegrationEngine.authenticate(s1, "ZD-1", zd_provider, {"api_key": "INVALID"}, 1000.0)

    auth = authentication_status(s2, "ZD-1")
    assert auth is not None
    assert auth.status == AuthStatus.FAILED
    assert any(type(e).__name__ == "AuthenticationFailed" for e in s2.events)


def test_connect_without_auth(
    base_state: IntegrationState, paper_config: BrokerConfig, paper_provider: PaperAdapter
) -> None:
    s1 = IntegrationEngine.register(base_state, paper_config)
    with pytest.raises(ConnectionManagerError, match="Must authenticate"):
        IntegrationEngine.connect(s1, "PAPER-1", paper_provider, 1000.0)


def test_connect_success(
    base_state: IntegrationState, paper_config: BrokerConfig, paper_provider: PaperAdapter
) -> None:
    s1 = IntegrationEngine.register(base_state, paper_config)
    s2 = IntegrationEngine.authenticate(s1, "PAPER-1", paper_provider, {"k": "v"}, 1000.0)
    s3 = IntegrationEngine.connect(s2, "PAPER-1", paper_provider, 1001.0)

    conn = connection_status(s3, "PAPER-1")
    assert conn is not None
    assert conn.status == ConnectionStatus.CONNECTED
    assert any(type(e).__name__ == "BrokerConnected" for e in s3.events)


def test_disconnect(
    base_state: IntegrationState, paper_config: BrokerConfig, paper_provider: PaperAdapter
) -> None:
    s1 = IntegrationEngine.register(base_state, paper_config)
    s2 = IntegrationEngine.authenticate(s1, "PAPER-1", paper_provider, {"k": "v"}, 1000.0)
    s3 = IntegrationEngine.connect(s2, "PAPER-1", paper_provider, 1001.0)
    s4 = IntegrationEngine.disconnect(s3, "PAPER-1", paper_provider, 1002.0)

    conn = connection_status(s4, "PAPER-1")
    assert conn is not None
    assert conn.status == ConnectionStatus.DISCONNECTED
    assert any(type(e).__name__ == "BrokerDisconnected" for e in s4.events)


# --- ORDER ROUTING & EXECUTION TRANSLATION TESTS ---

@pytest.fixture
def connected_state(
    base_state: IntegrationState, paper_config: BrokerConfig, paper_provider: PaperAdapter
) -> IntegrationState:
    s1 = IntegrationEngine.register(base_state, paper_config)
    s2 = IntegrationEngine.authenticate(s1, "PAPER-1", paper_provider, {"k": "v"}, 1000.0)
    return IntegrationEngine.connect(s2, "PAPER-1", paper_provider, 1001.0)


def test_submit_order_market_fill(
        connected_state: IntegrationState, 
        paper_provider: PaperAdapter
    ) -> None:
    order = {"order_id": "O-1", "symbol": "AAPL", "side": "BUY", "type": "MARKET", "quantity": 10}
    s1 = IntegrationEngine.submit_order(connected_state, "PAPER-1", paper_provider, order, 1002.0)

    assert any(type(e).__name__ == "OrderFilled" for e in s1.events)
    assert metrics_report(s1).orders_submitted == 1
    assert metrics_report(s1).executions_processed == 1


def test_submit_order_limit_accept(
        connected_state: IntegrationState, 
        paper_provider: PaperAdapter
    ) -> None:
    order = {
        "order_id": "O-1", 
        "symbol": "AAPL", 
        "side": "BUY", 
        "type": "LIMIT", 
        "quantity": 10, 
        "price": 150.0
    }
    s1 = IntegrationEngine.submit_order(connected_state, "PAPER-1", paper_provider, order, 1002.0)

    assert any(type(e).__name__ == "OrderAccepted" for e in s1.events)
    assert metrics_report(s1).orders_submitted == 1
    assert metrics_report(s1).executions_processed == 0


def test_submit_order_rejected() -> None:
    cfg = BrokerConfig("ALP-1", "Alpaca", "live", "api")
    s1 = IntegrationEngine.register(IntegrationEngine.initialize("E"), cfg)
    provider = AlpacaAdapter(AlpacaConfig("k", "s"))
    s2 = IntegrationEngine.authenticate(s1, "ALP-1", provider, {}, 1.0)
    s3 = IntegrationEngine.connect(s2, "ALP-1", provider, 2.0)

    order = {"order_id": "O-1", "symbol": "AAPL", "side": "BUY", "type": "MARKET", "quantity": -5}
    s4 = IntegrationEngine.submit_order(s3, "ALP-1", provider, order, 3.0)

    assert any(type(e).__name__ == "OrderRejected" for e in s4.events)
    assert metrics_report(s4).orders_rejected == 1


def test_disconnected_order_submission(
    connected_state: IntegrationState, paper_provider: PaperAdapter
) -> None:
    s1 = IntegrationEngine.disconnect(connected_state, "PAPER-1", paper_provider, 1002.0)
    order = {"order_id": "O-1", "symbol": "AAPL", "side": "BUY", "type": "MARKET", "quantity": 10}

    with pytest.raises(ConnectionManagerError, match="Broker disconnected"):
        IntegrationEngine.submit_order(s1, "PAPER-1", paper_provider, order, 1003.0)


# --- PORTFOLIO SYNC TESTS ---

def test_sync_portfolio_no_drift(
    connected_state: IntegrationState, paper_provider: PaperAdapter
) -> None:
    s1 = IntegrationEngine.sync_portfolio(connected_state, "PAPER-1", paper_provider, 1002.0)

    sync_events = [e for e in s1.events if type(e).__name__ == "PortfolioSynchronized"]
    assert len(sync_events) == 1
    evt = sync_events[0]
    assert isinstance(evt, PortfolioSynchronized)
    assert not evt.drift_detected


def test_sync_portfolio_with_drift() -> None:
    cfg = BrokerConfig("IB-1", "IBKR", "live", "api")
    s1 = IntegrationEngine.register(IntegrationEngine.initialize("E"), cfg)
    provider = InteractiveBrokersAdapter(InteractiveBrokersConfig("k", "s"))
    s2 = IntegrationEngine.authenticate(s1, "IB-1", provider, {}, 1.0)
    s3 = IntegrationEngine.connect(s2, "IB-1", provider, 2.0)

    s4 = IntegrationEngine.sync_portfolio(s3, "IB-1", provider, 3.0)
    sync_events = [e for e in s4.events if type(e).__name__ == "PortfolioSynchronized"]
    evt = sync_events[0]
    assert isinstance(evt, PortfolioSynchronized)
    assert evt.drift_detected


# --- ADAPTER TESTS ---

def test_adapter_translation() -> None:
    alpha_order = {
        "order_id": "O-123",
        "symbol": "TSLA",
        "side": "buy",
        "order_type": "limit",
        "quantity": "50.0",
        "price": "200.5",
    }
    payload = IntegrationAdapter.to_broker_payload(alpha_order)

    assert payload["side"] == "BUY"
    assert payload["type"] == "LIMIT"
    assert payload["quantity"] == 50.0
    assert payload["price"] == 200.5


# --- METAMORPHIC SCALING TESTS ---

@pytest.mark.parametrize(
    "broker_id, adapter_cls, config_cls",
    [
        ("PAPER-X", PaperAdapter, PaperConfig),
        ("ZD-X", ZerodhaAdapter, ZerodhaConfig),
        ("ALP-X", AlpacaAdapter, AlpacaConfig),
        ("IB-X", InteractiveBrokersAdapter, InteractiveBrokersConfig),
    ],
)
def test_all_providers_protocol_compliance(
    broker_id: str, adapter_cls: Any, config_cls: Any
) -> None:
    cfg = BrokerConfig(broker_id, broker_id, "test", "url")
    s1 = IntegrationEngine.register(IntegrationEngine.initialize("E"), cfg)

    provider = adapter_cls(config_cls(api_key="VALID", api_secret="SECRET"))
    s2 = IntegrationEngine.authenticate(s1, broker_id, provider, {"api_key": "VALID"}, 1.0)
    auth = authentication_status(s2, broker_id)
    assert auth is not None
    assert auth.status in {
        AuthStatus.AUTHENTICATED,
        AuthStatus.FAILED,
    }