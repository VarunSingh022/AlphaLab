"""The position book keeps exact totals as it changes (ledger PRF-001).

The contract is that every total the book keeps is the number -- and the
representation -- a fresh sum over its positions would produce, whatever order
the positions changed in. These tests drive long random sequences of opens,
re-marks, flips and closes through the book and compare against that fresh sum
after every step.
"""

import copy
import pickle
import random
from collections.abc import Mapping
from decimal import Decimal

import pytest

from alphalab.portfolio.book import CurrencyTotals, PositionBook
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.money import ZERO_MONEY
from alphalab.portfolio.position import Position


def _position(asset_id: str, quantity: str, price: str, currency: str = "USD") -> Position:
    return Position(
        asset_id=asset_id,
        quantity=Decimal(quantity),
        average_cost=Decimal("100"),
        market_price=Decimal(price),
        realized_pnl=ZERO_MONEY,
        currency=currency,
        last_updated=1.0,
    )


def _fresh(positions: Mapping[str, Position], currency: str) -> tuple[Decimal, Decimal, Decimal]:
    """What the totals were computed as before the book kept them."""

    held = [p for p in positions.values() if p.currency == currency]
    long_value = sum((p.market_value for p in held if p.quantity > 0), ZERO_MONEY)
    short_value = sum((p.market_value for p in held if p.quantity < 0), ZERO_MONEY)
    unrealized = sum((p.unrealized_pnl for p in held), ZERO_MONEY)
    return long_value, short_value, unrealized


def _assert_matches(book: PositionBook, reference: dict[str, Position]) -> None:
    assert book == reference
    assert list(book) == list(reference)
    for currency in {"USD", "JPY", "KWD"}:
        totals = book.totals(currency)
        expected = _fresh(reference, currency)
        kept = (totals.long_value, totals.short_value, totals.unrealized_pnl)
        # Equal as numbers *and* written the same way.
        assert kept == expected
        assert [str(value) for value in kept] == [str(value) for value in expected]
        held = [p for p in reference.values() if p.currency == currency]
        assert totals.positions == len(held)
        assert totals.longs == sum(1 for p in held if p.quantity > 0)
        assert totals.shorts == sum(1 for p in held if p.quantity < 0)
    assert book.currencies == tuple(sorted({p.currency for p in reference.values()}))


@pytest.mark.parametrize("seed", range(6))
def test_totals_equal_a_fresh_sum_after_every_change(seed: int) -> None:
    rng = random.Random(seed)
    currencies = ["USD", "JPY", "KWD"]  # 2, 0 and 3 minor units
    reference: dict[str, Position] = {}
    book = PositionBook()
    for _ in range(400):
        asset = f"A{rng.randrange(12)}"
        if asset in reference and rng.random() < 0.25:
            del reference[asset]
            book = book.delete(asset)
        else:
            currency = reference[asset].currency if asset in reference else rng.choice(currencies)
            quantity = str(rng.choice([-3, -1, 0, 1, 2, 5]) * rng.choice([1, 10, 100]))
            price = str(Decimal(rng.randrange(1, 100000)) / Decimal(rng.choice([1, 100, 1000])))
            reference[asset] = _position(asset, quantity, price, currency)
            book = book.set(asset, reference[asset])
        _assert_matches(book, reference)


def test_a_book_built_at_once_matches_one_built_a_change_at_a_time() -> None:
    positions = {
        f"A{index}": _position(f"A{index}", str(index - 3), f"{100 + index}.25")
        for index in range(8)
    }
    stepwise = PositionBook()
    for asset, position in positions.items():
        stepwise = stepwise.set(asset, position)

    at_once = PositionBook(positions)

    assert at_once == stepwise
    assert at_once.totals("USD") == stepwise.totals("USD")


def test_an_empty_book_sums_to_an_empty_sum() -> None:
    totals = PositionBook().totals("USD")

    assert totals == CurrencyTotals()
    assert str(totals.long_value) == str(sum((), ZERO_MONEY)) == "0"


def test_closing_everything_returns_to_an_empty_sum() -> None:
    book = PositionBook().set("A", _position("A", "10", "101.10"))
    book = book.set("B", _position("B", "-5", "99.99"))
    book = book.delete("A").delete("B")

    assert book.totals("USD") == CurrencyTotals()
    assert book.currencies == ()


def test_a_position_that_flips_moves_between_the_long_and_short_totals() -> None:
    book = PositionBook().set("A", _position("A", "10", "100"))
    assert book.totals("USD").long_value == Decimal("1000.00")

    book = book.set("A", _position("A", "-4", "100"))

    totals = book.totals("USD")
    assert (totals.longs, totals.shorts) == (0, 1)
    assert totals.long_value == ZERO_MONEY
    assert totals.short_value == Decimal("-400.00")


def test_a_position_is_booked_under_its_own_asset() -> None:
    with pytest.raises(PortfolioError, match="cannot be booked"):
        PositionBook().set("B", _position("A", "1", "1"))


def test_deleting_an_absent_asset_is_the_same_book() -> None:
    book = PositionBook().set("A", _position("A", "1", "1"))

    assert book.delete("missing") is book


def test_a_total_that_cannot_be_kept_exactly_is_refused() -> None:
    huge = _position("A", "1", "9" * 32 + ".99")
    book = PositionBook().set("A", huge)

    with pytest.raises(PortfolioError, match="significant"):
        book.set("B", _position("B", "1", "9" * 32 + ".99"))


def test_the_book_is_a_mapping_that_compares_and_copies_as_one() -> None:
    positions = {"A": _position("A", "1", "10"), "B": _position("B", "-1", "20")}
    book = PositionBook(positions)

    assert book == positions
    assert dict(book) == positions
    assert book != {"A": positions["A"]}
    assert "A" in book and "Z" not in book
    assert len(book) == 2
    assert book.market_values == {"A": Decimal("10.00"), "B": Decimal("-20.00")}
    assert pickle.loads(pickle.dumps(book)) == book
    assert copy.deepcopy(book).totals("USD") == book.totals("USD")
    assert book.__serializable__() == positions


def test_a_book_does_not_change_when_a_later_version_does() -> None:
    first = PositionBook().set("A", _position("A", "1", "10"))
    second = first.set("A", _position("A", "2", "10")).set("B", _position("B", "1", "5"))

    assert dict(first) == {"A": _position("A", "1", "10")}
    assert first.totals("USD").long_value == Decimal("10.00")
    assert second.totals("USD").long_value == Decimal("25.00")
