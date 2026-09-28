"""Two values that compare equal have one identity (ledger DET-005).

v3.10 stopped rounding an amount a second time: ``CurrencyAmounts`` and the
cash ledger store what the producer of an amount gave them, and a price or a
quantity is booked as reported (``alphalab.portfolio.money`` rules 2 and 3).
v3.9 had rounded every amount to a cent and every price and quantity to a fixed
number of places, and that rounding had also, silently, made each amount's
*text* canonical. Identities that hashed the text therefore changed meaning:
while v3.10 was being built, a multi-strategy book holding ``250000`` of
unassigned capital and one holding ``250000.00`` compared equal and had two
different ``book_id`` values. v3.9 gave them one, so this is a regression the
release caught in itself rather than a v3.9 defect, and the test passes against
v3.9.0.

The identities render every amount with ``canonical_text`` now -- one text per
number, whatever its exponent -- under the v2 book and valuation schemes.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.common.arithmetic import canonical_text
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.fx import NO_RATES
from alphalab.portfolio.multi_strategy import (
    BOOK_VALUATION_SCHEME,
    MULTI_STRATEGY_BOOK_SCHEME,
    MultiStrategyBook,
    StrategySleeve,
    value_book,
)

ACCOUNT = Account(account_id="ACC", base_currency="USD", name="Fund", created_at=0.0)


# --------------------------------------------------------------------------- #
# canonical_text
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("spellings", "text"),
    [
        (("100", "100.00", "1E+2", "100.000000"), "100"),
        (("0", "0.00", "-0", "-0.00", "0E-8", "0E+3"), "0"),
        (("-12.34", "-12.3400", "-1234E-2"), "-12.34"),
        (("0.0000005", "5E-7", "0.00000050"), "0.0000005"),
    ],
)
def test_every_spelling_of_a_number_has_one_text(spellings: tuple[str, ...], text: str) -> None:
    assert {canonical_text(Decimal(spelling)) for spelling in spellings} == {text}


def test_no_digit_is_rounded_away() -> None:
    """Beyond the 34 digits of the accounting context two numbers still differ."""

    longer = Decimal("1.0000000000000000000000000000000000000001")

    assert canonical_text(longer) == "1.0000000000000000000000000000000000000001"
    assert canonical_text(longer) != canonical_text(Decimal("1"))
    assert canonical_text(Decimal("1E+40")) == "1" + "0" * 40


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_a_value_that_is_not_a_number_has_no_text(value: str) -> None:
    with pytest.raises(AlphaLabValidationError, match="not a number"):
        canonical_text(Decimal(value))


# --------------------------------------------------------------------------- #
# The book and its valuation
# --------------------------------------------------------------------------- #


def _book(unassigned: str) -> MultiStrategyBook:
    return MultiStrategyBook("FUND", (), CurrencyAmounts.single(Decimal(unassigned), "USD"))


def _sleeve(deposit: str, quantity: str, price: str) -> StrategySleeve:
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=ACCOUNT), Decimal(deposit), "USD", 1.0
    )
    state = PortfolioEngine.apply_fill(
        state, "XYZ", Decimal(quantity), Decimal(price), Decimal("0"), 2.0, "USD"
    )
    return StrategySleeve.from_portfolio_state("MOM", state)


def test_the_schemes_that_render_by_value_are_the_v2_schemes() -> None:
    assert MULTI_STRATEGY_BOOK_SCHEME == "alphalab.multi_strategy_book.v2"
    assert BOOK_VALUATION_SCHEME == "alphalab.book_valuation.v2"


def test_equal_unassigned_capital_gives_one_book_identity() -> None:
    whole, cents = _book("250000"), _book("250000.00")

    assert whole == cents
    assert whole.book_id == cents.book_id


def test_equal_holdings_give_one_book_identity() -> None:
    first = MultiStrategyBook(
        "FUND", (_sleeve("100000", "100", "10"),), CurrencyAmounts.single(Decimal("0"), "USD")
    )
    second = MultiStrategyBook(
        "FUND",
        (_sleeve("100000.00", "100.0", "10.00"),),
        CurrencyAmounts.single(Decimal("0.00"), "USD"),
    )

    assert first == second
    assert first.book_id == second.book_id


def test_equal_books_give_one_valuation_identity() -> None:
    whole = value_book(_book("250000"), reporting_currency="USD", rates=NO_RATES, as_of=3.0)
    cents = value_book(_book("250000.00"), reporting_currency="USD", rates=NO_RATES, as_of=3.0)

    assert whole.valuation_id == cents.valuation_id


def test_a_different_amount_is_still_a_different_book() -> None:
    assert _book("250000").book_id != _book("250000.01").book_id
    assert _book("250000").book_id != _book("-250000").book_id
