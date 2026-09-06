"""A valuation is one figure in one currency, or it is a refusal.

Until v2.8 ``PortfolioValuation.snapshot`` produced a number from a book that
might hold several currencies, and the two halves of the calculation disagreed
about what to do with them. ``cash`` was read for the base currency alone, so
every other balance vanished. ``long_value`` and ``short_value`` summed every
position regardless of what it traded in, so foreign positions were added
straight into the total.

Measured at v2.7.0 on a book of 1000 USD and 500 EUR cash against 1100 USD and
1100 EUR of positions::

    snapshot.currency = "USD"
    snapshot.cash = 1000.00  # the 500 EUR is gone
    positions_value = 2200.00  # 1100 USD + 1100 EUR, added together
    equity = 3200.00  # a figure in no currency at all

No error, no warning. There is no honest single number without a rate, so the
valuation now refuses instead of inventing one.

**This is the absence of FX, not a rule that foreign-currency instruments are
invalid.** ``CashLedger`` is keyed by currency and ``Position`` declares its
own, so a wholly-EUR book values perfectly well in EUR -- that case is asserted
below alongside the refusals, because the distinction is the whole point.
"""

from decimal import Decimal

import pytest

from alphalab.portfolio.account import Account
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.exceptions import MixedCurrencyValuationError
from alphalab.portfolio.position import Position
from alphalab.portfolio.valuation import PortfolioValuation, assert_single_currency

ZERO = Decimal("0.00")


def _position(asset_id: str, currency: str, quantity: str = "10") -> Position:
    return Position(
        asset_id=asset_id,
        quantity=Decimal(quantity),
        average_cost=Decimal("100"),
        market_price=Decimal("110"),
        realized_pnl=ZERO,
        currency=currency,
        last_updated=1.0,
    )


def _state(
    *,
    balances: dict[str, Decimal] | None = None,
    positions: dict[str, Position] | None = None,
    base: str = "USD",
) -> PortfolioState:
    return PortfolioState(
        account=Account("acct", base, "Valuation Account", 1.0),
        cash=CashLedger(balances=dict(balances or {})),
        positions=dict(positions or {}),
    )


# --------------------------------------------------------------------------- #
# Valid: one currency, however little of it there is
# --------------------------------------------------------------------------- #


def test_cash_only_in_the_base_currency_values() -> None:
    state = _state(balances={"USD": Decimal("1000.00")})

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD")

    assert (snapshot.cash, snapshot.positions_value, snapshot.equity) == (
        Decimal("1000.00"),
        ZERO,
        Decimal("1000.00"),
    )


def test_positions_only_in_the_base_currency_value() -> None:
    state = _state(positions={"a": _position("a", "USD")})

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD")

    assert snapshot.positions_value == Decimal("1100.00")
    assert snapshot.equity == Decimal("1100.00")


def test_a_book_with_no_positions_values() -> None:
    state = _state(balances={"USD": Decimal("500.00")}, positions={})

    assert PortfolioValuation.snapshot(state, 1.0, "USD").equity == Decimal("500.00")


def test_a_book_with_no_cash_at_all_values() -> None:
    """An empty ledger is not a second currency."""

    state = _state(balances={}, positions={"a": _position("a", "USD")})

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD")

    assert (snapshot.cash, snapshot.equity) == (ZERO, Decimal("1100.00"))


def test_an_entirely_foreign_book_values_in_its_own_currency() -> None:
    """The distinction this release exists to keep: EUR is not invalid.

    A wholly-EUR book has one currency and one honest total. Refusing it would
    turn "AlphaLab has no FX" into "foreign instruments cannot be held", which
    is a different and false claim.
    """

    state = _state(
        balances={"EUR": Decimal("1000.00")},
        positions={"a": _position("a", "EUR")},
        base="EUR",
    )

    snapshot = PortfolioValuation.snapshot(state, 1.0, "EUR")

    assert snapshot.currency == "EUR"
    assert snapshot.equity == Decimal("2100.00")


def test_a_spent_currency_leaves_a_zero_balance_that_is_not_a_second_currency() -> None:
    """``withdraw`` subtracts in place and leaves the key behind."""

    ledger = CashLedger(balances={"USD": Decimal("1000.00")}).deposit(Decimal("50.00"), "EUR")
    ledger = ledger.withdraw(Decimal("50.00"), "EUR")
    state = PortfolioState(
        account=Account("acct", "USD", "Spent", 1.0),
        cash=ledger,
        positions={"a": _position("a", "USD")},
    )

    assert "EUR" in state.cash.balances
    assert state.cash.balance("EUR") == ZERO
    assert PortfolioValuation.snapshot(state, 1.0, "USD").equity == Decimal("2100.00")


def test_the_base_currency_defaults_to_the_account_and_still_validates() -> None:
    state = _state(balances={"USD": Decimal("10.00")}, positions={"a": _position("a", "USD")})

    assert PortfolioValuation.snapshot(state, 1.0).currency == "USD"


# --------------------------------------------------------------------------- #
# Refused: two currencies, one number requested
# --------------------------------------------------------------------------- #


def test_base_currency_cash_with_a_foreign_position_is_refused() -> None:
    state = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "EUR")})

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(state, 1.0, "USD")


def test_foreign_cash_with_a_base_currency_position_is_refused() -> None:
    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"a": _position("a", "USD")},
    )

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(state, 1.0, "USD")


def test_positions_in_two_currencies_are_refused() -> None:
    state = _state(
        balances={"USD": Decimal("1000.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(state, 1.0, "USD")


def test_a_currency_the_book_does_not_hold_is_refused() -> None:
    """A zero cash reading plus a USD position book is not an EUR valuation."""

    state = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "USD")})

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(state, 1.0, "EUR")


def test_a_flat_foreign_position_is_still_a_second_currency() -> None:
    """The test is on the declared currency, never on whether it has value.

    A rule that ignored flat positions would answer differently depending on the
    order fills arrived in.
    """

    state = _state(
        balances={"USD": Decimal("1000.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR", quantity="0")},
    )

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(state, 1.0, "USD")


def test_the_v2_7_book_that_reported_a_figure_in_no_currency_is_refused() -> None:
    """The exact book from this module's docstring."""

    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"us": _position("us", "USD"), "eu": _position("eu", "EUR")},
    )

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(state, 1.0, "USD")


# --------------------------------------------------------------------------- #
# What the refusal says, and what it does not do
# --------------------------------------------------------------------------- #


def test_the_refusal_names_the_base_currency_and_both_sets_of_offenders() -> None:
    state = _state(
        balances={"USD": Decimal("1000.00"), "JPY": Decimal("900.00")},
        positions={"a": _position("a", "GBP")},
    )

    with pytest.raises(MixedCurrencyValuationError) as caught:
        PortfolioValuation.snapshot(state, 1.0, "USD")

    message = str(caught.value)
    assert "'USD'" in message
    assert "GBP" in message
    assert "JPY" in message


def test_the_refusal_says_it_is_a_missing_rate_not_an_invalid_instrument() -> None:
    """The distinction is the release's central claim; the message carries it."""

    state = _state(balances={"USD": Decimal("1.00")}, positions={"a": _position("a", "EUR")})

    with pytest.raises(MixedCurrencyValuationError) as caught:
        PortfolioValuation.snapshot(state, 1.0, "USD")

    message = str(caught.value).lower()
    assert "fx rate" in message
    assert "not a rule that" in message


def test_nothing_is_converted_at_any_rate() -> None:
    """No FX is introduced: a mixed book raises rather than being converted."""

    mixed = _state(
        balances={"USD": Decimal("1000.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )
    usd_only = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "USD")})

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(mixed, 1.0, "USD")

    # The USD leg alone is unchanged: nothing about the EUR position influenced it.
    assert PortfolioValuation.snapshot(usd_only, 1.0, "USD").equity == Decimal("2100.00")


def test_the_check_runs_before_any_figure_is_computed() -> None:
    """A refused valuation returns no partial result, only the exception."""

    state = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "EUR")})
    result = None

    with pytest.raises(MixedCurrencyValuationError):
        result = PortfolioValuation.snapshot(state, 1.0, "USD")

    assert result is None


# --------------------------------------------------------------------------- #
# The predicate is reusable, and the siblings are deliberately untouched
# --------------------------------------------------------------------------- #


def test_the_predicate_is_callable_on_its_own() -> None:
    single = _state(balances={"USD": Decimal("1.00")}, positions={"a": _position("a", "USD")})
    mixed = _state(balances={"USD": Decimal("1.00")}, positions={"a": _position("a", "EUR")})

    assert_single_currency(single, "USD")  # a single-currency book simply returns
    with pytest.raises(MixedCurrencyValuationError):
        assert_single_currency(mixed, "USD")


def test_the_currency_blind_siblings_are_unchanged_in_this_release() -> None:
    """Extending the rule to these belongs with the release that supplies a rate.

    ``NAVCalculator.calculate`` runs on every market event through the risk
    resync, so guarding it is a hot-path decision with its own callers to weigh.
    Pinning the current behaviour here makes a later change deliberate.
    """

    from alphalab.portfolio.nav import NAVCalculator

    mixed = _state(
        balances={"USD": Decimal("1000.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )

    assert PortfolioValuation.portfolio_value(mixed.cash, mixed.positions, "USD") == Decimal(
        "3200.00"
    )
    assert NAVCalculator.calculate(mixed.cash, mixed.positions, "USD") == Decimal("3200.00")
    assert PortfolioValuation.long_value(mixed.positions) == Decimal("2200.00")


def test_a_single_currency_valuation_is_numerically_what_it_always_was() -> None:
    """Every figure, not just equity, matches the pre-guard calculation."""

    state = _state(
        balances={"USD": Decimal("1000.00")},
        positions={"long": _position("long", "USD"), "short": _position("short", "USD", "-4")},
    )

    snapshot = PortfolioValuation.snapshot(state, 7.0, "USD")

    assert snapshot.timestamp == 7.0
    assert snapshot.currency == "USD"
    assert snapshot.cash == Decimal("1000.00")
    assert snapshot.long_value == Decimal("1100.00")
    assert snapshot.short_value == Decimal("-440.00")
    assert snapshot.positions_value == Decimal("660.00")
    assert snapshot.unrealized_pnl == Decimal("60.00")
    assert snapshot.realized_pnl == ZERO
    assert snapshot.commission_paid == ZERO
    assert snapshot.equity == Decimal("1660.00")
