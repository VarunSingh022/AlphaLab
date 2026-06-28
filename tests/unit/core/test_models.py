from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
    new_account_id,
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
    position_id = new_position_id()
    asset_id = new_asset_id()

    position = Position(
        position_id=position_id,
        asset_id=asset_id,
        asset_type=AssetType.EQUITY,
        quantity=Decimal("12"),
        average_price=Decimal("10"),
        market_price=Decimal("12"),
        updated_at=NOW,
    )
    same_position = Position(
        position_id=position_id,
        asset_id=asset_id,
        asset_type=AssetType.EQUITY,
        quantity=Decimal("12"),
        average_price=Decimal("10"),
        market_price=Decimal("12"),
        updated_at=NOW,
    )

    assert position == same_position
    assert asdict(position)["asset_type"] == "equity"


def test_portfolio_state_is_immutable_snapshot_with_serializable_positions() -> None:
    position = Position(
        position_id=new_position_id(),
        asset_id=new_asset_id(),
        asset_type=AssetType.EQUITY,
        quantity=Decimal("3"),
        average_price=Decimal("20"),
        market_price=Decimal("21"),
        updated_at=NOW,
    )
    portfolio = PortfolioState(
        portfolio_id=new_portfolio_id(),
        account_id=new_account_id(),
        base_currency="USD",
        cash=Decimal("10000"),
        equity=Decimal("10063"),
        realized_pnl=Decimal("12"),
        unrealized_pnl=Decimal("3"),
        updated_at=NOW,
        positions=(position,),
    )

    data = asdict(portfolio)

    assert data["positions"][0]["asset_id"] == position.asset_id
    with pytest.raises(FrozenInstanceError):
        portfolio.__setattr__("base_currency", "EUR")


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


def test_position_rejects_zero_quantity() -> None:
    with pytest.raises(DomainValidationError):
        Position(
            position_id=new_position_id(),
            asset_id=new_asset_id(),
            asset_type=AssetType.EQUITY,
            quantity=Decimal("0"),
            average_price=Decimal("10"),
            market_price=Decimal("10"),
            updated_at=NOW,
        )


def test_portfolio_state_rejects_duplicate_position_assets() -> None:
    asset_id: AssetId = new_asset_id()
    first_position = Position(
        position_id=new_position_id(),
        asset_id=asset_id,
        asset_type=AssetType.EQUITY,
        quantity=Decimal("1"),
        average_price=Decimal("10"),
        market_price=Decimal("10"),
        updated_at=NOW,
    )
    second_position = Position(
        position_id=new_position_id(),
        asset_id=asset_id,
        asset_type=AssetType.EQUITY,
        quantity=Decimal("2"),
        average_price=Decimal("20"),
        market_price=Decimal("20"),
        updated_at=NOW,
    )

    with pytest.raises(DomainValidationError):
        PortfolioState(
            portfolio_id=new_portfolio_id(),
            account_id=new_account_id(),
            base_currency="USD",
            cash=Decimal("100"),
            equity=Decimal("100"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            updated_at=NOW,
            positions=(first_position, second_position),
        )
