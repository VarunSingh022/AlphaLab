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


def test_engine_round_trip_winning_close_cash_and_pnl(
    empty_state: PortfolioState,
) -> None:
    """A buy/sell round-trip returning to flat: cash must equal the actual cash
    flows and, equivalently, initial + realized P&L - total commission. Realized
    P&L must not be added to cash a second time (regression for D1)."""
    initial = Decimal("100000")
    n, p1, p2, comm = Decimal("10"), Decimal("150.00"), Decimal("165.00"), Decimal("1.00")

    s = PortfolioEngine.apply_deposit(empty_state, initial, "USD", 100.0)
    s = PortfolioEngine.apply_fill(s, "AAPL", n, p1, comm, 101.0)  # BUY 10 @ 150
    s = PortfolioEngine.apply_fill(s, "AAPL", -n, p2, comm, 102.0)  # SELL 10 @ 165

    realized = (p2 - p1) * n  # 150.00
    cash_flow_expected = initial - n * p1 - comm + n * p2 - comm  # 100148.00
    identity_expected = initial + realized - (comm + comm)  # 100148.00

    assert cash_flow_expected == identity_expected
    assert s.cash.balance("USD") == cash_flow_expected
    assert s.positions == {}


def test_engine_round_trip_losing_close_cash(
    empty_state: PortfolioState,
) -> None:
    """A losing round-trip: cash must be down by the loss plus commissions,
    never inflated (regression for D1)."""
    initial = Decimal("100000")
    n, p1, p2, comm = Decimal("8"), Decimal("200.00"), Decimal("175.00"), Decimal("0.75")

    s = PortfolioEngine.apply_deposit(empty_state, initial, "USD", 100.0)
    s = PortfolioEngine.apply_fill(s, "MSFT", n, p1, comm, 101.0)  # BUY 8 @ 200
    s = PortfolioEngine.apply_fill(s, "MSFT", -n, p2, comm, 102.0)  # SELL 8 @ 175

    realized = (p2 - p1) * n  # -200.00
    cash_flow_expected = initial - n * p1 - comm + n * p2 - comm  # 99798.50
    identity_expected = initial + realized - (comm + comm)  # 99798.50

    assert cash_flow_expected == identity_expected
    assert s.cash.balance("USD") == cash_flow_expected
    assert s.positions == {}


def test_engine_partial_reduction_cash(
    empty_state: PortfolioState,
) -> None:
    """Selling M < N leaves a residual long; cash reflects only the executed
    legs and commissions, and the realized P&L on the closed portion is not
    double-counted into cash (regression for D1)."""
    initial = Decimal("100000")
    n, m, p1, p2, comm = (
        Decimal("10"),
        Decimal("4"),
        Decimal("100.00"),
        Decimal("130.00"),
        Decimal("0.50"),
    )

    s = PortfolioEngine.apply_deposit(empty_state, initial, "USD", 100.0)
    s = PortfolioEngine.apply_fill(s, "NVDA", n, p1, comm, 101.0)  # BUY 10 @ 100
    s = PortfolioEngine.apply_fill(s, "NVDA", -m, p2, comm, 102.0)  # SELL 4 @ 130

    realized = (p2 - p1) * m  # 120.00
    cash_flow_expected = initial - n * p1 - comm + m * p2 - comm  # 98999.00
    identity_expected = initial + realized - (comm + comm) - (n - m) * p1  # 98999.00

    assert cash_flow_expected == identity_expected
    assert s.cash.balance("USD") == cash_flow_expected
    assert s.positions["NVDA"].quantity == (n - m)
    assert s.positions["NVDA"].realized_pnl == realized
    assert s.positions["NVDA"].average_cost == p1  # cost basis of the remaining lot unchanged


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
