"""Regression guard for B2: the accounting identity is exact, not approximate.

The invariant under test is:

    equity == deposits - withdrawals + realized_pnl + unrealized_pnl
              - commission_paid

Before the monetary precision policy in :mod:`alphalab.portfolio.money`, this
held only for prices that happened to land on whole cents. The cash ledger
rounded ``quantity * price + commission`` while the position independently
rounded ``(exit_price - average_cost) * quantity``; two roundings of the same
economic event disagreed by up to half a cent each and the error accumulated.
An ordinary penny-spread quote (bid 100.00 / ask 100.01, mid 100.005) put the
identity out by a cent; randomized multi-asset portfolios drifted by up to five.

Every test below therefore uses prices the old implementation could not handle:
half-cent mids, sub-cent prices, thirds, and fractional quantities.
"""

import random
from decimal import Decimal

import pytest

from alphalab.portfolio import (
    Account,
    PortfolioEngine,
    PortfolioState,
    PortfolioValuation,
)
from alphalab.portfolio.money import CURRENCY_QUANT, to_money

INITIAL = Decimal("1000000.00")

#: Prices chosen to defeat cent-rounding: half-cents, thirds, sub-cent ticks.
AWKWARD_PRICES = [
    Decimal("100.005"),  # the mid of a 1-cent spread -- the original B2 repro
    Decimal("33.333333"),
    Decimal("120.007"),
    Decimal("0.0001"),
    Decimal("199.995"),
    Decimal("7.77"),
]
AWKWARD_QUANTITIES = [
    Decimal("1"),
    Decimal("3"),
    Decimal("7"),
    Decimal("1.7"),
    Decimal("2.25"),
    Decimal("0.333333"),
]
AWKWARD_COMMISSIONS = [Decimal("0"), Decimal("0.001"), Decimal("0.125"), Decimal("1.005")]


def funded(deposit: Decimal = INITIAL) -> PortfolioState:
    account = Account(account_id="ACC-MP", base_currency="USD", name="Precision", created_at=1.0)
    return PortfolioEngine.apply_deposit(PortfolioState(account=account), deposit, "USD", 1.0)


def identity_gap(state: PortfolioState, deposits: Decimal = INITIAL) -> Decimal:
    """equity - (deposits + realized + unrealized - commissions). Must be exactly 0."""

    valuation = PortfolioValuation.snapshot(state, 0.0)
    expected = (
        deposits + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )
    return valuation.equity - expected


def assert_exact(state: PortfolioState, deposits: Decimal = INITIAL) -> None:
    gap = identity_gap(state, deposits)
    assert gap == Decimal("0"), f"accounting identity off by {gap}"


def assert_all_money_is_cent_exact(state: PortfolioState) -> None:
    """Contract rule 1: every stored monetary amount is a multiple of 0.01."""

    valuation = PortfolioValuation.snapshot(state, 0.0)
    for label, amount in (
        ("cash", valuation.cash),
        ("realized_pnl", valuation.realized_pnl),
        ("commission_paid", valuation.commission_paid),
        ("unrealized_pnl", valuation.unrealized_pnl),
        ("equity", valuation.equity),
        ("positions_value", valuation.positions_value),
    ):
        assert amount == to_money(amount), f"{label}={amount} is not cent-exact"
    for asset, position in state.positions.items():
        assert position.basis == to_money(position.basis), f"{asset} basis not cent-exact"
        assert position.market_value == to_money(position.market_value)


# ---------------------------------------------------------------------------
# The original B2 reproduction
# ---------------------------------------------------------------------------


def test_penny_spread_mid_round_trip_is_exact() -> None:
    """bid 100.00 / ask 100.01 -> mid 100.005: the case that was off by a cent."""

    state = funded()
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("7"), Decimal("100.005"), Decimal("0"), 2.0
    )
    assert_exact(state)
    assert_all_money_is_cent_exact(state)

    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-7"), Decimal("133.335"), Decimal("0"), 3.0
    )
    assert_exact(state)
    # Cash moved by exactly what realized P&L claims.
    assert state.cash.balance("USD") == INITIAL + state.realized_pnl


def test_long_reversal_at_a_sub_cent_price_is_exact() -> None:
    """The reversal split must add up to the single cash movement, exactly."""

    state = funded()
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("1.00"), 2.0
    )
    before = state.cash.balance("USD")
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-25"), Decimal("120.007"), Decimal("1.00"), 3.0
    )

    assert_exact(state)
    assert state.positions["AAPL"].quantity == Decimal("-15.000000")
    # The 25 sold credited one cash movement; the closed 10 and the new short 15
    # must partition exactly that amount.
    credited = state.cash.balance("USD") - before + Decimal("1.00")
    assert credited == to_money(Decimal("25") * Decimal("120.007"))


# ---------------------------------------------------------------------------
# Parametrized sweep over every supported position transition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("price", AWKWARD_PRICES)
@pytest.mark.parametrize("quantity", AWKWARD_QUANTITIES)
def test_opening_a_long_is_exact(price: Decimal, quantity: Decimal) -> None:
    state = PortfolioEngine.apply_fill(funded(), "A", quantity, price, Decimal("0.125"), 2.0)
    assert_exact(state)
    assert_all_money_is_cent_exact(state)


@pytest.mark.parametrize("price", AWKWARD_PRICES)
@pytest.mark.parametrize("quantity", AWKWARD_QUANTITIES)
def test_opening_a_short_is_exact(price: Decimal, quantity: Decimal) -> None:
    state = PortfolioEngine.apply_fill(funded(), "A", -quantity, price, Decimal("0.125"), 2.0)
    assert_exact(state)
    assert_all_money_is_cent_exact(state)


@pytest.mark.parametrize("commission", AWKWARD_COMMISSIONS)
def test_commissions_at_sub_cent_precision_stay_exact(commission: Decimal) -> None:
    state = funded()
    for i in range(5):
        state = PortfolioEngine.apply_fill(
            state, "A", Decimal("3"), Decimal("33.333333"), commission, 2.0 + i
        )
        assert_exact(state)
    assert state.commission_paid == to_money(commission) * 5
    assert_all_money_is_cent_exact(state)


@pytest.mark.parametrize("exit_price", AWKWARD_PRICES)
def test_partial_close_is_exact(exit_price: Decimal) -> None:
    state = funded()
    state = PortfolioEngine.apply_fill(
        state, "A", Decimal("9"), Decimal("77.775"), Decimal("0.1"), 2.0
    )
    for i, size in enumerate((Decimal("-3"), Decimal("-3"), Decimal("-3"))):
        state = PortfolioEngine.apply_fill(state, "A", size, exit_price, Decimal("0.1"), 3.0 + i)
        assert_exact(state)
    assert "A" not in state.positions
    assert_all_money_is_cent_exact(state)


@pytest.mark.parametrize("price", AWKWARD_PRICES)
def test_multiple_fills_then_full_close_is_exact(price: Decimal) -> None:
    state = funded()
    for i in range(6):
        state = PortfolioEngine.apply_fill(
            state, "A", Decimal("1.7"), price + Decimal(i) / Decimal("3"), Decimal("0.01"), 2.0 + i
        )
        assert_exact(state)
    held = state.positions["A"].quantity
    state = PortfolioEngine.apply_fill(state, "A", -held, Decimal("199.995"), Decimal("0.01"), 20.0)
    assert_exact(state)
    assert state.positions == {}


def test_short_reversal_at_sub_cent_prices_is_exact() -> None:
    state = funded()
    state = PortfolioEngine.apply_fill(
        state, "A", Decimal("-7"), Decimal("55.555"), Decimal("0.25"), 2.0
    )
    assert_exact(state)
    state = PortfolioEngine.apply_fill(
        state, "A", Decimal("18"), Decimal("51.115"), Decimal("0.25"), 3.0
    )
    assert_exact(state)
    assert state.positions["A"].quantity == Decimal("11.000000")


# ---------------------------------------------------------------------------
# Mark-to-market at fractional-cent marks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mark", AWKWARD_PRICES)
def test_marking_at_a_fractional_cent_price_keeps_the_identity_exact(mark: Decimal) -> None:
    state = funded()
    state = PortfolioEngine.apply_fill(
        state, "A", Decimal("7"), Decimal("100.005"), Decimal("0"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "B", Decimal("-3"), Decimal("33.333333"), Decimal("0"), 2.0
    )

    marked = PortfolioEngine.update_market_prices(state, {"A": mark, "B": mark}, 3.0)

    assert_exact(marked)
    assert_all_money_is_cent_exact(marked)
    # Marking moves unrealized P&L only.
    assert marked.realized_pnl == state.realized_pnl
    assert marked.commission_paid == state.commission_paid
    assert marked.cash.balance("USD") == state.cash.balance("USD")


def test_withdrawals_are_included_in_the_identity() -> None:
    state = funded()
    state = PortfolioEngine.apply_fill(
        state, "A", Decimal("7"), Decimal("100.005"), Decimal("0.5"), 2.0
    )
    state = PortfolioEngine.apply_withdrawal(state, Decimal("1234.56"), "USD", 3.0)

    assert_exact(state, deposits=INITIAL - Decimal("1234.56"))


# ---------------------------------------------------------------------------
# Randomized multi-asset portfolios
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(25))
def test_randomized_multi_asset_portfolios_are_exact(seed: int) -> None:
    """The randomized sweep that previously drifted by up to $0.05."""

    rng = random.Random(seed)
    state = funded()

    for step in range(12):
        asset = f"A{rng.randrange(4)}"
        quantity = rng.choice(AWKWARD_QUANTITIES) * rng.choice([Decimal("1"), Decimal("-1")])
        price = rng.choice(AWKWARD_PRICES)
        commission = rng.choice(AWKWARD_COMMISSIONS)
        state = PortfolioEngine.apply_fill(state, asset, quantity, price, commission, 2.0 + step)
        assert_exact(state)

    marks = {f"A{i}": rng.choice(AWKWARD_PRICES) for i in range(4)}
    state = PortfolioEngine.update_market_prices(state, marks, 50.0)

    assert_exact(state)
    assert_all_money_is_cent_exact(state)


def test_identity_holds_after_fully_unwinding_a_random_portfolio() -> None:
    """Unwind every position: equity must be cash, and cash must be deposits + realized - fees."""

    rng = random.Random(99)
    state = funded()
    for step in range(15):
        asset = f"A{rng.randrange(3)}"
        quantity = rng.choice(AWKWARD_QUANTITIES) * rng.choice([Decimal("1"), Decimal("-1")])
        state = PortfolioEngine.apply_fill(
            state, asset, quantity, rng.choice(AWKWARD_PRICES), Decimal("0.125"), 2.0 + step
        )

    for asset in list(state.positions):
        held = state.positions[asset].quantity
        state = PortfolioEngine.apply_fill(
            state, asset, -held, rng.choice(AWKWARD_PRICES), Decimal("0.125"), 100.0
        )

    assert state.positions == {}
    assert_exact(state)
    valuation = PortfolioValuation.snapshot(state, 0.0)
    assert valuation.equity == valuation.cash
    assert valuation.cash == INITIAL + state.realized_pnl - state.commission_paid


# ---------------------------------------------------------------------------
# The precision contract itself
# ---------------------------------------------------------------------------


def test_cost_basis_is_authoritative_and_survives_partial_closes() -> None:
    state = funded()
    state = PortfolioEngine.apply_fill(
        state, "A", Decimal("10"), Decimal("100.005"), Decimal("0"), 2.0
    )
    opened = state.positions["A"]
    assert opened.basis == Decimal("1000.05")  # exactly what cash paid
    assert state.cash.balance("USD") == INITIAL - Decimal("1000.05")

    state = PortfolioEngine.apply_fill(
        state, "A", Decimal("-4"), Decimal("120.00"), Decimal("0"), 3.0
    )
    reduced = state.positions["A"]
    # Relieved 4/10 of the basis; the remainder is the exact complement.
    assert reduced.basis == Decimal("1000.05") - Decimal("400.02")
    assert state.realized_pnl == Decimal("480.00") - Decimal("400.02")
    assert_exact(state)


def test_a_position_constructed_without_a_basis_derives_one() -> None:
    """External constructions (fixtures, adapters) still behave correctly."""

    from alphalab.portfolio import Position

    position = Position("A", Decimal("12"), Decimal("10"), Decimal("12"), Decimal("0"), "USD", 1.0)

    assert position.cost_basis is None
    assert position.basis == Decimal("120.00")
    assert position.unrealized_pnl == Decimal("24.00")


def test_currency_quant_is_the_single_declared_precision() -> None:
    from alphalab.portfolio import cash as cash_module
    from alphalab.portfolio import position as position_module

    assert CURRENCY_QUANT == Decimal("0.01")  # noqa: SIM300
    assert cash_module.CURRENCY_QUANT is CURRENCY_QUANT
    assert position_module.CURRENCY_QUANT is CURRENCY_QUANT
