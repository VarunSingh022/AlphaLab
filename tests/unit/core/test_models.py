from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from alphalab.core import (
    AssetId,
    AssetType,
    DomainValidationError,
    Event,
    EventType,
    Fill,
    Order,
    OrderType,
    PortfolioState,
    Position,
    Side,
    Signal,
    TimeInForce,
    Trade,
    new_asset_id,
    new_event_id,
    new_fill_id,
    new_order_id,
    new_portfolio_id,
    new_position_id,
    new_signal_id,
    new_strategy_id,
    new_trade_id,
)
from alphalab.portfolio.account import Account
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState as CanonicalPortfolioState
from alphalab.portfolio.position import Position as CanonicalPosition

NOW = datetime(2026, 1, 2, 15, 30, tzinfo=UTC)


def test_event_creation_equality_and_serialization() -> None:
    event_id = new_event_id()

    event = Event(
        event_id=event_id,
        event_type=EventType.ORDER,
        occurred_at=NOW,
        source="unit-test",
    )
    same_event = Event(
        event_id=event_id,
        event_type=EventType.ORDER,
        occurred_at=NOW,
        source="unit-test",
    )

    assert event == same_event
    assert asdict(event) == {
        "event_id": event_id,
        "event_type": EventType.ORDER,
        "occurred_at": NOW,
        "source": "unit-test",
        "correlation_id": None,
    }


def test_signal_creation_equality_and_serialization() -> None:
    signal_id = new_signal_id()
    strategy_id = new_strategy_id()
    asset_id = new_asset_id()

    signal = Signal(
        signal_id=signal_id,
        strategy_id=strategy_id,
        asset_id=asset_id,
        side=Side.BUY,
        confidence=Decimal("0.75"),
        generated_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    same_signal = Signal(
        signal_id=signal_id,
        strategy_id=strategy_id,
        asset_id=asset_id,
        side=Side.BUY,
        confidence=Decimal("0.75"),
        generated_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert signal == same_signal
    assert asdict(signal)["side"] == "buy"
    assert asdict(signal)["confidence"] == Decimal("0.75")


def test_order_creation_equality_and_serialization() -> None:
    order_id = new_order_id()
    asset_id = new_asset_id()

    order = Order(
        order_id=order_id,
        asset_id=asset_id,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        created_at=NOW,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("125.50"),
    )
    same_order = Order(
        order_id=order_id,
        asset_id=asset_id,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        created_at=NOW,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("125.50"),
    )

    data = asdict(order)

    assert order == same_order
    assert data["order_id"] == order_id
    assert data["order_type"] == "limit"
    assert data["limit_price"] == Decimal("125.50")


def test_fill_creation_equality_and_serialization() -> None:
    fill_id = new_fill_id()
    order_id = new_order_id()
    asset_id = new_asset_id()

    fill = Fill(
        fill_id=fill_id,
        order_id=order_id,
        asset_id=asset_id,
        side=Side.SELL,
        quantity=Decimal("4"),
        price=Decimal("201.10"),
        filled_at=NOW,
        commission=Decimal("1.25"),
    )
    same_fill = Fill(
        fill_id=fill_id,
        order_id=order_id,
        asset_id=asset_id,
        side=Side.SELL,
        quantity=Decimal("4"),
        price=Decimal("201.10"),
        filled_at=NOW,
        commission=Decimal("1.25"),
    )

    assert fill == same_fill
    assert asdict(fill)["commission"] == Decimal("1.25")
    assert asdict(fill)["side"] == "sell"


def test_trade_creation_equality_and_serialization() -> None:
    trade_id = new_trade_id()
    asset_id = new_asset_id()
    fill_ids = (new_fill_id(),)

    trade = Trade(
        trade_id=trade_id,
        asset_id=asset_id,
        side=Side.BUY,
        quantity=Decimal("8"),
        average_price=Decimal("99.95"),
        fill_ids=fill_ids,
        executed_at=NOW,
    )
    same_trade = Trade(
        trade_id=trade_id,
        asset_id=asset_id,
        side=Side.BUY,
        quantity=Decimal("8"),
        average_price=Decimal("99.95"),
        fill_ids=fill_ids,
        executed_at=NOW,
    )

    assert trade == same_trade
    assert asdict(trade)["fill_ids"] == fill_ids


def test_position_creation_equality_and_serialization() -> None:
    asset_id = new_asset_id()

    position = Position(
        asset_id=asset_id,
        quantity=Decimal("12"),
        average_cost=Decimal("10"),
        market_price=Decimal("12"),
        realized_pnl=Decimal("1.25"),
        currency="USD",
        last_updated=NOW.timestamp(),
    )
    same_position = Position(
        asset_id=asset_id,
        quantity=Decimal("12"),
        average_cost=Decimal("10"),
        market_price=Decimal("12"),
        realized_pnl=Decimal("1.25"),
        currency="USD",
        last_updated=NOW.timestamp(),
    )

    assert position == same_position
    assert asdict(position)["asset_id"] == asset_id
    assert position.market_value == Decimal("144.00")
    assert position.unrealized_pnl == Decimal("24.00")


def test_portfolio_state_is_immutable_snapshot_with_serializable_positions() -> None:
    position = Position(
        asset_id=new_asset_id(),
        quantity=Decimal("3"),
        average_cost=Decimal("20"),
        market_price=Decimal("21"),
        realized_pnl=Decimal("12"),
        currency="USD",
        last_updated=NOW.timestamp(),
    )
    portfolio = PortfolioState(
        account=Account("core-account", "USD", "Core Account", NOW.timestamp()),
        cash=CashLedger(balances={"USD": Decimal("10000.00")}),
        positions={position.asset_id: position},
    )

    data = asdict(portfolio)

    assert data["positions"][position.asset_id]["asset_id"] == position.asset_id
    assert data["cash"]["balances"]["USD"] == Decimal("10000.00")
    with pytest.raises(FrozenInstanceError):
        portfolio.__setattr__("account", Account("other", "EUR", "Other", NOW.timestamp()))


def test_core_portfolio_state_is_canonical_portfolio_state() -> None:
    assert PortfolioState is CanonicalPortfolioState


def test_core_position_is_canonical_position() -> None:
    assert Position is CanonicalPosition


def test_core_id_helpers_create_uuid_backed_ids() -> None:
    asset_id: AssetId = new_asset_id()
    portfolio_id = new_portfolio_id()
    position_id = new_position_id()

    assert str(UUID(asset_id)) == asset_id
    assert str(UUID(portfolio_id)) == portfolio_id
    assert str(UUID(position_id)) == position_id


def test_asset_type_enum_behavior() -> None:
    assert AssetType.EQUITY.value == "equity"
    assert AssetType.CASH.value == "cash"
    assert AssetType("equity") is AssetType.EQUITY


def test_event_rejects_naive_timestamp() -> None:
    with pytest.raises(DomainValidationError):
        Event(
            event_id=new_event_id(),
            event_type=EventType.SIGNAL,
            occurred_at=datetime(2026, 1, 2, 15, 30),
            source="unit-test",
        )


def test_signal_rejects_confidence_outside_valid_range() -> None:
    with pytest.raises(DomainValidationError):
        Signal(
            signal_id=new_signal_id(),
            strategy_id=new_strategy_id(),
            asset_id=new_asset_id(),
            side=Side.BUY,
            confidence=Decimal("1.01"),
            generated_at=NOW,
        )


def test_order_rejects_invalid_price_shape_for_order_type() -> None:
    with pytest.raises(DomainValidationError):
        Order(
            order_id=new_order_id(),
            asset_id=new_asset_id(),
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            created_at=NOW,
            limit_price=Decimal("100"),
        )


def test_order_rejects_non_positive_quantity() -> None:
    with pytest.raises(DomainValidationError):
        Order(
            order_id=new_order_id(),
            asset_id=new_asset_id(),
            side=Side.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("0"),
            created_at=NOW,
        )


def test_fill_rejects_negative_commission() -> None:
    with pytest.raises(DomainValidationError):
        Fill(
            fill_id=new_fill_id(),
            order_id=new_order_id(),
            asset_id=new_asset_id(),
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("10"),
            filled_at=NOW,
            commission=Decimal("-0.01"),
        )


def test_trade_rejects_duplicate_fill_ids() -> None:
    fill_id = new_fill_id()

    with pytest.raises(DomainValidationError):
        Trade(
            trade_id=new_trade_id(),
            asset_id=new_asset_id(),
            side=Side.BUY,
            quantity=Decimal("1"),
            average_price=Decimal("10"),
            fill_ids=(fill_id, fill_id),
            executed_at=NOW,
        )


def test_position_supports_flat_quantity_for_portfolio_accounting() -> None:
    position = Position(
        asset_id=new_asset_id(),
        quantity=Decimal("0"),
        average_cost=Decimal("0"),
        market_price=Decimal("10"),
        realized_pnl=Decimal("0"),
        currency="USD",
        last_updated=NOW.timestamp(),
    )

    assert position.market_value == Decimal("0.00")
    assert position.unrealized_pnl == Decimal("0.00")


def test_portfolio_state_positions_are_keyed_by_asset_id() -> None:
    asset_id = new_asset_id()
    first_position = Position(
        asset_id=asset_id,
        quantity=Decimal("1"),
        average_cost=Decimal("10"),
        market_price=Decimal("10"),
        realized_pnl=Decimal("0"),
        currency="USD",
        last_updated=NOW.timestamp(),
    )

    portfolio = PortfolioState(
        account=Account("core-account", "USD", "Core Account", NOW.timestamp()),
        positions={asset_id: first_position},
    )

    assert portfolio.positions[asset_id] is first_position
