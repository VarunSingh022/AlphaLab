"""The position book keeps an exact gross per group as it changes (v3.12 stress finding).

The 10,000-asset stress run found every order judged against a classification
limit summing its whole bucket. A book given a :class:`BookGroups` keeps each
group's gross instead; the contract is the book's own -- the number, and the
representation, a fresh sum over the group's positions would produce, whatever
order the positions changed in -- driven here through long random sequences of
opens, re-marks, flips, flat positions, currency changes and closes.
"""

import copy
import pickle
import random
from collections.abc import Mapping
from decimal import Decimal

import pytest

from alphalab.portfolio.book import BookGroups, PositionBook
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.money import ZERO_MONEY
from alphalab.portfolio.position import Position

DIMENSIONS = frozenset({"country", "sector"})
LABELS = {"sector": ("Energy", "Health", "Tech"), "country": ("DE", "US")}


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


def _groups(assets: int, seed: int) -> BookGroups:
    """Every asset in one sector, most in one country, a few in neither."""

    rng = random.Random(seed)
    keys: dict[str, tuple[tuple[str, str], ...]] = {}
    for index in range(assets):
        if index % 7 == 6:
            continue  # unclassified: belongs to no group
        found = [("sector", rng.choice(LABELS["sector"]))]
        if index % 3:
            found.append(("country", rng.choice(LABELS["country"])))
        keys[f"A{index}"] = tuple(found)
    return BookGroups(DIMENSIONS, keys)


def _fresh(
    positions: Mapping[str, Position], groups: BookGroups, dimension: str, label: str
) -> dict[str, Decimal]:
    """What the risk gate summed before the book kept the figure."""

    found: dict[str, Decimal] = {}
    for asset_id, position in positions.items():
        if (dimension, label) in groups.keys.get(asset_id, ()):
            held = found.get(position.currency, ZERO_MONEY)
            found[position.currency] = held + abs(position.market_value)
    return found


def _assert_matches(book: PositionBook, reference: dict[str, Position], groups: BookGroups) -> None:
    for dimension, labels in LABELS.items():
        for label in labels:
            kept = book.group_gross(dimension, label)
            expected = _fresh(reference, groups, dimension, label)
            assert kept == expected
            # Equal as numbers *and* written the same way.
            assert kept is not None
            assert {c: str(v) for c, v in kept.items()} == {c: str(v) for c, v in expected.items()}


@pytest.mark.parametrize("seed", range(6))
def test_the_gross_of_every_group_equals_a_fresh_sum_after_every_change(seed: int) -> None:
    rng = random.Random(seed)
    groups = _groups(14, seed)
    currencies = ["USD", "JPY", "KWD"]  # 2, 0 and 3 minor units
    reference: dict[str, Position] = {}
    book = PositionBook().grouped(groups)
    for step in range(400):
        asset = f"A{rng.randrange(14)}"
        if asset in reference and rng.random() < 0.25:
            del reference[asset]
            book = book.delete(asset)
        else:
            # A position may change currency: the gross moves between them.
            currency = rng.choice(currencies)
            if asset in reference and rng.random() < 0.8:
                currency = reference[asset].currency
            quantity = str(rng.choice([-3, -1, 0, 1, 2, 5]) * rng.choice([1, 10, 100]))
            price = str(Decimal(rng.randrange(1, 100000)) / Decimal(rng.choice([1, 100, 1000])))
            reference[asset] = _position(asset, quantity, price, currency)
            book = book.set(asset, reference[asset])
        _assert_matches(book, reference, groups)
        if step % 50 == 49:
            # Grouping a book in one pass gives what was kept a change at a time.
            regrouped = PositionBook(dict(book)).grouped(groups)
            _assert_matches(regrouped, reference, groups)


def test_a_book_groups_by_nothing_unless_asked() -> None:
    book = PositionBook({"A0": _position("A0", "1", "10")})

    assert book.groups is None
    assert book.group_gross("sector", "Energy") is None
    assert book.market_values.groups is None  # type: ignore[attr-defined]


def test_an_uncovered_dimension_reads_none_and_an_empty_group_reads_empty() -> None:
    groups = BookGroups(frozenset({"sector"}), {"A0": (("sector", "Energy"),)})
    book = PositionBook({"A0": _position("A0", "2", "10")}).grouped(groups)

    assert book.group_gross("country", "US") is None
    assert book.group_gross("sector", "Health") == {}
    assert book.group_gross("sector", "Energy") == {"USD": Decimal("20.00")}
    # The values the risk gate reads answer from the same book.
    assert book.market_values.group_gross("sector", "Energy") == {"USD": Decimal("20.00")}  # type: ignore[attr-defined]


def test_a_group_of_flat_positions_reads_zero_in_its_minor_unit_and_an_emptied_one_nothing() -> (
    None
):
    groups = BookGroups(frozenset({"sector"}), {"A0": (("sector", "Energy"),)})
    book = PositionBook().grouped(groups).set("A0", _position("A0", "5", "10"))

    flat = book.set("A0", _position("A0", "0", "10"))
    gross = flat.group_gross("sector", "Energy")
    assert gross is not None and str(gross["USD"]) == "0.00"

    assert flat.delete("A0").group_gross("sector", "Energy") == {}


def test_the_grouping_is_not_part_of_the_books_value() -> None:
    groups = _groups(6, 0)
    positions = {f"A{i}": _position(f"A{i}", str(i - 2), f"{100 + i}.5") for i in range(6)}
    plain = PositionBook(positions)
    grouped = plain.grouped(groups)

    assert grouped == plain == positions
    assert grouped.__serializable__() == plain.__serializable__()
    assert grouped.totals("USD") == plain.totals("USD")
    # A book made from the book carries what it keeps; a pickle or a copy
    # carries the value only, and groups by nothing.
    assert PositionBook(grouped).groups is groups
    assert pickle.loads(pickle.dumps(grouped)) == plain
    assert copy.deepcopy(grouped).groups is None
    assert grouped.grouped(None).groups is None


def test_a_group_total_that_cannot_be_kept_exactly_is_refused() -> None:
    """The book's own rule: a gross beyond 34 significant digits raises, never rounds."""

    groups = BookGroups(
        frozenset({"sector"}), {"A": (("sector", "Energy"),), "B": (("sector", "Energy"),)}
    )
    huge = "9" * 32 + ".99"
    long = _position("A", "1", huge)
    short = _position("B", "-1", huge)
    # Ungrouped, the longs and the shorts each total within the precision ...
    assert PositionBook().set("A", long).set("B", short).totals("USD").positions == 2

    # ... and their gross does not.
    with pytest.raises(PortfolioError, match="group total"):
        PositionBook().grouped(groups).set("A", long).set("B", short)
