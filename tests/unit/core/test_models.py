from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from alphalab.common.serialization import dataclass_to_dict
from alphalab.core import (
    AssetId,
    AssetType,
    DomainValidationError,
    Fill,
    Side,
    Trade,
    new_asset_id,
    new_fill_id,
    new_order_id,
    new_portfolio_id,
    new_position_id,
    new_trade_id,
)
from alphalab.portfolio.account import Account
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.position import Position

NOW = datetime(2026, 1, 2, 15, 30, tzinfo=UTC)
NOW_TS = NOW.timestamp()


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
        filled_at=NOW_TS,
        commission=Decimal("1.25"),
    )
    same_fill = Fill(
        fill_id=fill_id,
        order_id=order_id,
        asset_id=asset_id,
        side=Side.SELL,
        quantity=Decimal("4"),
        price=Decimal("201.10"),
        filled_at=NOW_TS,
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
        executed_at=NOW_TS,
    )
    same_trade = Trade(
        trade_id=trade_id,
        asset_id=asset_id,
        side=Side.BUY,
        quantity=Decimal("8"),
        average_price=Decimal("99.95"),
        fill_ids=fill_ids,
        executed_at=NOW_TS,
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

    # The repository's serializable projection. ``dataclasses.asdict`` does not
    # recurse into the persistent containers a state holds -- the positions are
    # a PositionBook since v3.10, as its histories are append-only logs -- which
    # is why dataclass_to_dict exists (see alphalab.common.serialization).
    data = dataclass_to_dict(portfolio)

    assert data["positions"][position.asset_id]["asset_id"] == position.asset_id
    assert data["cash"]["balances"]["USD"] == Decimal("10000.00")
    assert dict(portfolio.positions) == {position.asset_id: position}
    with pytest.raises(FrozenInstanceError):
        portfolio.__setattr__("account", Account("other", "EUR", "Other", NOW.timestamp()))


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


def test_fill_takes_a_rebate_and_refuses_a_commission_that_is_not_a_number() -> None:
    """Signed since v3.11 (ACC-007): a negative commission is a rebate."""

    def fill(commission: str, price: str = "10") -> Fill:
        return Fill(
            fill_id=new_fill_id(),
            order_id=new_order_id(),
            asset_id=new_asset_id(),
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal(price),
            filled_at=NOW_TS,
            commission=Decimal(commission),
        )

    assert fill("-0.01").commission == Decimal("-0.01")
    assert fill("0", price="-37.63").price == Decimal("-37.63")
    with pytest.raises(DomainValidationError, match="finite"):
        fill("NaN")
    with pytest.raises(DomainValidationError, match="finite"):
        fill("0", price="Infinity")


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
            executed_at=NOW_TS,
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
