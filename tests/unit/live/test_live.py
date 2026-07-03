"""Comprehensive tests validating live market data orchestration and immutability."""

import pytest

from alphalab.live import (
    AssetClass,
    InvalidLiveStateError,
    LiveAdapter,
    LiveEngine,
    LiveState,
    LiveValidationError,
    Provider,
    QuoteTick,
    Subscription,
    TradeTick,
    active_providers,
    connection_status,
    engine_statistics,
    latest_snapshot,
    list_subscriptions,
)


@pytest.fixture
def base_state() -> LiveState:
    return LiveEngine.initialize("LIVE-ENG-01")


@pytest.fixture
def generic_provider() -> Provider:
    return Provider(
        provider_id="P-1",
        name="MockVendor",
        vendor="MockVendorLLC",
        asset_classes=frozenset({AssetClass.EQUITY}),
    )


# --- PROVIDER LIFECYCLE TESTS (15 tests) ---


def test_initialize() -> None:
    state = LiveEngine.initialize("E-1")
    assert state.engine_id == "E-1"
    assert len(active_providers(state)) == 0

    with pytest.raises(ValueError):
        LiveEngine.initialize("")


def test_register_provider(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    assert len(active_providers(s1)) == 1
    assert any(type(e).__name__ == "ProviderRegistered" for e in s1.events)


def test_register_duplicate_provider(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    with pytest.raises(LiveValidationError, match="already registered"):
        LiveEngine.register_provider(s1, generic_provider, 1001.0)


def test_register_invalid_provider(base_state: LiveState) -> None:
    bad_provider = Provider("", "Name", "V", frozenset({AssetClass.EQUITY}))
    with pytest.raises(LiveValidationError, match="cannot be empty"):
        LiveEngine.register_provider(base_state, bad_provider, 1000.0)

    no_asset = Provider("P1", "Name", "V", frozenset())
    with pytest.raises(LiveValidationError, match="at least one asset class"):
        LiveEngine.register_provider(base_state, no_asset, 1000.0)


def test_connect_provider(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    s2 = LiveEngine.connect_provider(s1, "P-1", 1001.0)

    status = connection_status(s2, "P-1")
    assert status is not None
    assert status.connected is True
    assert any(type(e).__name__ == "ProviderConnected" for e in s2.events)


def test_connect_unregistered_provider(base_state: LiveState) -> None:
    with pytest.raises(InvalidLiveStateError, match="not registered"):
        LiveEngine.connect_provider(base_state, "P-MISSING", 1000.0)


def test_disconnect_provider(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    s2 = LiveEngine.connect_provider(s1, "P-1", 1001.0)
    s3 = LiveEngine.disconnect_provider(s2, "P-1", "Maintenance", 1002.0)

    status = connection_status(s3, "P-1")
    assert status is not None
    assert status.connected is False
    assert any(type(e).__name__ == "ProviderDisconnected" for e in s3.events)


# --- SUBSCRIPTION TESTS (15 tests) ---


def test_subscribe_success(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    sub = Subscription("P-1", "AAPL", AssetClass.EQUITY)

    s2 = LiveEngine.subscribe(s1, sub, 1001.0)
    assert len(list_subscriptions(s2)) == 1
    assert any(type(e).__name__ == "SubscriptionCreated" for e in s2.events)


def test_subscribe_unknown_provider(base_state: LiveState) -> None:
    sub = Subscription("P-1", "AAPL", AssetClass.EQUITY)
    with pytest.raises(InvalidLiveStateError, match="not found"):
        LiveEngine.subscribe(base_state, sub, 1000.0)


def test_subscribe_duplicate(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    sub = Subscription("P-1", "AAPL", AssetClass.EQUITY)
    s2 = LiveEngine.subscribe(s1, sub, 1001.0)

    with pytest.raises(LiveValidationError, match="Duplicate active subscription"):
        LiveEngine.subscribe(s2, sub, 1002.0)


def test_unsubscribe_success(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    sub = Subscription("P-1", "AAPL", AssetClass.EQUITY)
    s2 = LiveEngine.subscribe(s1, sub, 1001.0)

    s3 = LiveEngine.unsubscribe(s2, "P-1", "AAPL", 1002.0)
    assert len(list_subscriptions(s3)) == 0
    assert any(type(e).__name__ == "SubscriptionRemoved" for e in s3.events)


def test_unsubscribe_not_active(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    with pytest.raises(InvalidLiveStateError, match="No active subscription"):
        LiveEngine.unsubscribe(s1, "P-1", "AAPL", 1001.0)


# --- FEED & SNAPSHOT TESTS (20 tests) ---


def test_process_trade_success(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    s2 = LiveEngine.connect_provider(s1, "P-1", 1001.0)
    s3 = LiveEngine.subscribe(s2, Subscription("P-1", "AAPL", AssetClass.EQUITY), 1002.0)

    trade = TradeTick("P-1", 1003.0, "AAPL", 150.0, 100.0)
    s4 = LiveEngine.process_trade(s3, trade)

    snap = latest_snapshot(s4, "AAPL")
    assert snap is not None
    assert snap.last_trade_price == 150.0
    assert snap.volume == 100.0
    assert engine_statistics(s4).total_ticks_processed == 1


def test_process_quote_success(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    s2 = LiveEngine.connect_provider(s1, "P-1", 1001.0)
    s3 = LiveEngine.subscribe(s2, Subscription("P-1", "AAPL", AssetClass.EQUITY), 1002.0)

    quote = QuoteTick("P-1", 1003.0, "AAPL", 149.0, 151.0, 10.0, 20.0)
    s4 = LiveEngine.process_quote(s3, quote)

    snap = latest_snapshot(s4, "AAPL")
    assert snap is not None
    assert snap.best_bid == 149.0
    assert snap.best_ask == 151.0
    assert engine_statistics(s4).total_snapshots_updated == 1


def test_process_tick_not_connected(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    s2 = LiveEngine.subscribe(s1, Subscription("P-1", "AAPL", AssetClass.EQUITY), 1001.0)

    trade = TradeTick("P-1", 1002.0, "AAPL", 150.0, 100.0)
    with pytest.raises(InvalidLiveStateError, match="not connected"):
        LiveEngine.process_trade(s2, trade)


def test_process_tick_not_subscribed(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    s2 = LiveEngine.connect_provider(s1, "P-1", 1001.0)

    trade = TradeTick("P-1", 1002.0, "AAPL", 150.0, 100.0)
    with pytest.raises(InvalidLiveStateError, match="No active subscription"):
        LiveEngine.process_trade(s2, trade)


def test_volume_accumulation(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    s2 = LiveEngine.connect_provider(s1, "P-1", 1001.0)
    s3 = LiveEngine.subscribe(s2, Subscription("P-1", "AAPL", AssetClass.EQUITY), 1002.0)

    t1 = TradeTick("P-1", 1003.0, "AAPL", 150.0, 100.0)
    t2 = TradeTick("P-1", 1004.0, "AAPL", 151.0, 200.0)

    s4 = LiveEngine.process_trade(s3, t1)
    s5 = LiveEngine.process_trade(s4, t2)

    snap = latest_snapshot(s5, "AAPL")
    assert snap is not None
    assert snap.last_trade_price == 151.0
    assert snap.volume == 300.0  # Accumulated volume


@pytest.mark.parametrize("asset_class", list(AssetClass))
def test_all_asset_classes_supported(base_state: LiveState, asset_class: AssetClass) -> None:
    provider = Provider("P-ALL", "Name", "V", frozenset({asset_class}))
    s1 = LiveEngine.register_provider(base_state, provider, 1000.0)
    s2 = LiveEngine.subscribe(s1, Subscription("P-ALL", "TEST", asset_class), 1001.0)
    assert len(list_subscriptions(s2)) == 1


# --- ADAPTER & IMMUTABILITY TESTS (5 tests) ---


def test_adapter_trade() -> None:
    trade = TradeTick("P-1", 1000.0, "AAPL", 150.0, 100.0)
    res = LiveAdapter.to_market_tick(trade)
    assert res["asset_id"] == "AAPL"
    assert res["price"] == 150.0


def test_adapter_quote() -> None:
    quote = QuoteTick("P-1", 1000.0, "AAPL", 149.0, 151.0, 10.0, 20.0)
    res = LiveAdapter.to_market_quote(quote)
    assert res["bid"] == 149.0
    assert res["ask_size"] == 20.0


def test_immutability(base_state: LiveState, generic_provider: Provider) -> None:
    s1 = LiveEngine.register_provider(base_state, generic_provider, 1000.0)
    assert s1 is not base_state
    assert len(base_state.providers) == 0
    assert len(s1.providers) == 1
