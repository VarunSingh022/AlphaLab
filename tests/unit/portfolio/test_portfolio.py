from decimal import Decimal

import pytest

from alphalab.portfolio import (
    Account,
    CashLedger,
    InsufficientFundsError,
    MarginEngine,
    NAVCalculator,
    PnLEngine,
    PortfolioEngine,
    PortfolioState,
    Position,
)


@pytest.fixture
def base_account() -> Account:
    return Account(
        account_id="ACC-001",
        base_currency="USD",
        name="Test Fund",
        created_at=100.0,
    )


@pytest.fixture
def empty_state(base_account: Account) -> PortfolioState:
    return PortfolioState(account=base_account)


def test_cash_deposit_withdraw() -> None:
    ledger = CashLedger()

    l2 = ledger.deposit(Decimal("1000.50"), "USD")
    assert l2.balance("USD") == Decimal("1000.50")

    l3 = l2.withdraw(Decimal("500.00"), "USD")
    assert l3.balance("USD") == Decimal("500.50")

    with pytest.raises(InsufficientFundsError):
        l3.withdraw(Decimal("1000.00"), "USD")


def test_position_long_fill() -> None:
    pos = Position(
        "AAPL",
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        "USD",
        100.0,
    )

    p2, pnl = pos.apply_fill(
        Decimal("10"),
        Decimal("150.00"),
        101.0,
    )

    assert p2.quantity == Decimal("10")
    assert p2.average_cost == Decimal("150.00")
    assert pnl == Decimal("0.00")


def test_position_realized_pnl() -> None:
    pos = Position(
        "AAPL",
        Decimal("10"),
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("0"),
        "USD",
        100.0,
    )

    p2, pnl = pos.apply_fill(
        Decimal("-5"),
        Decimal("150.00"),
        101.0,
    )

    assert p2.quantity == Decimal("5")
    assert p2.realized_pnl == Decimal("250.00")
    assert pnl == Decimal("250.00")


def test_engine_apply_deposit(
    empty_state: PortfolioState,
) -> None:
    s2 = PortfolioEngine.apply_deposit(
        empty_state,
        Decimal("10000"),
        "USD",
        100.0,
    )

    assert s2.cash.balance("USD") == Decimal("10000")
    assert len(s2.ledger.history()) == 1


def test_engine_apply_fill(
    empty_state: PortfolioState,
) -> None:
    s1 = PortfolioEngine.apply_deposit(
        empty_state,
        Decimal("10000"),
        "USD",
        100.0,
    )

    s2 = PortfolioEngine.apply_fill(
        s1,
        "AAPL",
        Decimal("10"),
        Decimal("150"),
        Decimal("1.50"),
        101.0,
    )

    assert "AAPL" in s2.positions
    assert s2.positions["AAPL"].quantity == Decimal("10")
    assert s2.cash.balance("USD") == Decimal("8498.50")


def test_pnl_and_nav(
    empty_state: PortfolioState,
) -> None:
    s1 = PortfolioEngine.apply_deposit(
        empty_state,
        Decimal("10000"),
        "USD",
        100.0,
    )

    s2 = PortfolioEngine.apply_fill(
        s1,
        "AAPL",
        Decimal("10"),
        Decimal("100"),
        Decimal("0"),
        101.0,
    )

    s3 = PortfolioEngine.update_market_prices(
        s2,
        {"AAPL": Decimal("150")},
        102.0,
    )

    unrealized = PnLEngine.unrealized_pnl(s3.positions)
    assert unrealized == Decimal("500.00")

    nav = NAVCalculator.calculate(
        s3.cash,
        s3.positions,
    )

    assert nav == Decimal("10500.00")


def test_margin(
    empty_state: PortfolioState,
) -> None:
    s1 = PortfolioEngine.apply_deposit(
        empty_state,
        Decimal("10000"),
        "USD",
        100.0,
    )

    s2 = PortfolioEngine.apply_fill(
        s1,
        "AAPL",
        Decimal("10"),
        Decimal("100"),
        Decimal("0"),
        101.0,
    )

    initial_margin = MarginEngine.initial_margin(
        s2.positions,
        Decimal("0.50"),
    )

    assert initial_margin == Decimal("500.00")

    buying_power = MarginEngine.buying_power(
        s2.cash,
        s2.positions,
        margin_rate=Decimal("0.50"),
    )

    assert buying_power == Decimal("19000.00")
