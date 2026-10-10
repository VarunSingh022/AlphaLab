"""The book of open positions, with the totals every valuation reads kept as it changes.

Until v3.10 ``PortfolioState.positions`` was a plain mapping, and everything that
valued the book walked all of it: the mark-to-market re-marked and logged every
held position on every market event, and the valuation, the net asset value and
the risk exposure each summed every position's market value again -- twice per
event for the valuation. A backtest's cost therefore grew with the square of its
universe: 160 assets for 50 bars took 55 CPU-seconds and 337 MB, and 500 assets
for ten years of daily bars extrapolated to hours (ledger PRF-001).

:class:`PositionBook` is the same immutable mapping of asset to
:class:`~alphalab.portfolio.position.Position`, with what those walks computed
kept alongside it and updated on every :meth:`~PositionBook.set` and
:meth:`~PositionBook.delete`:

* per currency, the summed market value of long and of short positions, the
  summed unrealized P&L, the market value that adds nothing to equity -- the
  notional of a position whose gains settle as cash (ledger ACC-005), zero for
  every fully paid one -- and how many positions of each kind there are;
* per asset, the position's market value, unrealized P&L and that uncarried
  value.

Every total is **exact**. Market values are money rounded once to the currency's
minor unit (see :mod:`alphalab.portfolio.money`), so adding and removing them is
exact decimal arithmetic, and a total kept this way is the same number a fresh
sum would produce -- with the same representation, because a total of no
positions is reported as :data:`~alphalab.portfolio.money.ZERO_MONEY`, as an
empty sum is. An update that could not be exact -- a total beyond 34 significant
digits -- raises rather than rounds.

Every update costs the same whatever the book holds: the persistent maps share
structure, and the per-currency table is as large as the number of currencies.

Gross per group (v3.12)
-----------------------

A book given a :class:`BookGroups` -- which group each asset belongs to along
each of a few dimensions -- also keeps, per group and currency, the summed
absolute market value of its members' positions and how many there are, updated
on the same :meth:`~PositionBook.set` and :meth:`~PositionBook.delete`. It is
what a classification limit reads: until v3.12 every order judged against one
summed its whole bucket, so a rebalance of a 10,000-name book under sector
limits cost the square of the book divided by the number of sectors. Each gross
is exact and written as a fresh sum of its members' values would be -- every
value in one currency has that currency's minor unit, and a group nobody holds
has no entry rather than a zero -- and an update it could not keep exactly
raises, as every other total here does. The grouping is the caller's and is not
part of the book's value: two books holding the same positions are equal
whatever either groups by, and a book built from a plain mapping groups by
nothing.
"""

from __future__ import annotations

import decimal
from collections.abc import ItemsView, Iterator, KeysView, Mapping, ValuesView
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.common.persistent_map import PersistentMap
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.money import ZERO_MONEY
from alphalab.portfolio.position import Position

__all__ = ["BookGroups", "BookMarketValues", "CurrencyTotals", "PositionBook"]

#: Totals are kept exactly or not at all: rounding a running total would make it
#: depend on the order positions changed in.
_EXACT = ACCOUNTING_CONTEXT.copy()
_EXACT.traps[decimal.Inexact] = True


@dataclass(frozen=True, slots=True)
class CurrencyTotals:
    """What the positions declaring one currency sum to.

    Attributes:
        positions: How many positions declare the currency, flat ones included.
        longs: How many have a positive quantity.
        shorts: How many have a negative quantity.
        long_value: Summed market value of the longs; zero or positive.
        short_value: Summed market value of the shorts; zero or negative.
        unrealized_pnl: Summed unrealized P&L of every position.
        uncarried_value: Summed market value that adds nothing to equity:
            ``market_value - carrying_value`` of each position whose gains
            settle as cash (a future, a perpetual). Zero for a book of fully
            paid positions, so ``long_value + short_value - uncarried_value``
            -- what the positions add to equity -- is exactly what it always
            was for every such book.
    """

    positions: int = 0
    longs: int = 0
    shorts: int = 0
    long_value: Decimal = ZERO_MONEY
    short_value: Decimal = ZERO_MONEY
    unrealized_pnl: Decimal = ZERO_MONEY
    uncarried_value: Decimal = ZERO_MONEY


_NO_TOTALS = CurrencyTotals()


@dataclass(frozen=True, slots=True, eq=False)
class BookGroups:
    """Which groups each asset belongs to, for the gross a :class:`PositionBook` keeps per group.

    A group is a ``(dimension, label)`` pair -- ``("sector", "Energy")``.

    Attributes:
        dimensions: Every dimension the grouping covers. A dimension it does
            not cover has no totals, which :meth:`PositionBook.group_gross`
            reports as ``None`` rather than as zero.
        keys: ``asset_id`` -> the groups it belongs to; an asset absent from it
            belongs to none.
        source: What the grouping was derived from, compared by identity: a
            reader holding the classification it would group by now can tell
            whether these totals are of that classification or of another.
    """

    dimensions: frozenset[str]
    keys: Mapping[str, tuple[tuple[str, str], ...]]
    source: object = None


def _exact_sum(total: Decimal, amount: Decimal) -> Decimal:
    try:
        return _EXACT.add(total, amount)
    except decimal.Inexact as exc:
        raise PortfolioError(
            f"A group total of {total} + {amount} exceeds the {_EXACT.prec} significant digits "
            "it is kept to exactly."
        ) from exc


#: ``(dimension, label, currency)`` -> how many positions the group holds in the
#: currency, flat ones included, and their summed absolute market value.
_GroupTotals = PersistentMap[tuple[str, str, str], tuple[int, Decimal]]

_NO_GROUP_TOTALS: _GroupTotals = PersistentMap()


def _regrouped(
    groups: BookGroups | None,
    totals: _GroupTotals,
    asset_id: str,
    old: tuple[str, Decimal] | None,
    new: tuple[str, Decimal] | None,
) -> _GroupTotals:
    """``totals`` with ``asset_id``'s market value moved from ``old`` to ``new``.

    Each side is ``(currency, market value)``, ``None`` for no position. A
    group's entry goes when its last position does, so a gross is always a sum
    over positions the book holds and is written as a fresh one would be.
    """

    if groups is None:
        return totals
    keys = groups.keys.get(asset_id)
    if not keys:
        return totals
    for dimension, label in keys:
        if old is not None and new is not None and old[0] == new[0]:
            # Re-marked or resized in the same currency: one update.
            key = (dimension, label, new[0])
            count, gross = totals[key]
            change = _exact_sum(new[1].copy_abs(), old[1].copy_abs().copy_negate())
            totals = totals.set(key, (count, _exact_sum(gross, change)))
            continue
        if old is not None:
            key = (dimension, label, old[0])
            count, gross = totals[key]
            if count == 1:
                totals = totals.delete(key)
            else:
                gross = _exact_sum(gross, old[1].copy_abs().copy_negate())
                totals = totals.set(key, (count - 1, gross))
        if new is not None:
            key = (dimension, label, new[0])
            count, gross = totals.get(key, (0, ZERO_MONEY))
            totals = totals.set(key, (count + 1, _exact_sum(gross, new[1].copy_abs())))
    return totals


def _add(total: Decimal, amount: Decimal, count: int) -> Decimal:
    """``total + amount``, exactly, or zero money when nothing is left to sum."""

    if count == 0:
        return ZERO_MONEY
    try:
        return _EXACT.add(total, amount)
    except decimal.Inexact as exc:
        raise PortfolioError(
            f"A book total of {total} + {amount} exceeds the {_EXACT.prec} significant "
            "digits it is kept to exactly; it would have to be rounded, and a rounded "
            "running total depends on the order positions changed in."
        ) from exc


def _entry(position: Position) -> tuple[Decimal, Decimal, Decimal]:
    value, pnl = position.valuation()
    return value, pnl, ZERO_MONEY if position.pays_notional else value - pnl


def _difference(new: Decimal, old: Decimal) -> Decimal:
    try:
        return _EXACT.subtract(new, old)
    except decimal.Inexact as exc:
        raise PortfolioError(
            f"A book entry's change from {old} to {new} exceeds the {_EXACT.prec} significant "
            "digits totals are kept to exactly."
        ) from exc


def _moved(
    totals: CurrencyTotals,
    long: bool,
    old: tuple[Decimal, Decimal, Decimal],
    new: tuple[Decimal, Decimal, Decimal],
) -> CurrencyTotals:
    """``totals`` with one open position re-valued on the same side, counts unchanged.

    ``total + (new - old)`` is the same exact number, written the same way, as
    removing the old entry and adding the new one: both are exact, and the
    exponent of an exact sum is the smallest of its operands' either way. One
    update instead of two (ledger PRF-006).
    """

    open_positions = totals.longs + totals.shorts
    long_value = totals.long_value
    short_value = totals.short_value
    if long:
        long_value = _add(long_value, _difference(new[0], old[0]), totals.longs)
    else:
        short_value = _add(short_value, _difference(new[0], old[0]), totals.shorts)
    return CurrencyTotals(
        totals.positions,
        totals.longs,
        totals.shorts,
        long_value,
        short_value,
        _add(totals.unrealized_pnl, _difference(new[1], old[1]), open_positions),
        _add(totals.uncarried_value, _difference(new[2], old[2]), open_positions),
    )


def _apply(
    totals: CurrencyTotals,
    position: Position,
    entry: tuple[Decimal, Decimal, Decimal],
    sign: int,
) -> CurrencyTotals:
    """``totals`` with ``position`` added (``sign`` 1) or removed (``sign`` -1)."""

    value, pnl, uncarried = entry
    count = totals.positions + sign
    longs = totals.longs
    shorts = totals.shorts
    long_value = totals.long_value
    short_value = totals.short_value
    signed_value = value if sign > 0 else -value
    if position.quantity > 0:
        longs += sign
        long_value = _add(long_value, signed_value, longs)
    elif position.quantity < 0:
        shorts += sign
        short_value = _add(short_value, signed_value, shorts)
    # A flat position's unrealized P&L is an exact zero written ``0``, so a sum
    # over flat positions alone is ``0`` however many there are: the total is
    # normalized on the open positions, not on every position.
    unrealized = _add(totals.unrealized_pnl, pnl if sign > 0 else -pnl, longs + shorts)
    settled = _add(totals.uncarried_value, uncarried if sign > 0 else -uncarried, longs + shorts)
    return CurrencyTotals(count, longs, shorts, long_value, short_value, unrealized, settled)


class PositionBook(Mapping[str, Position]):
    """An immutable mapping of asset to position that keeps its own totals.

    Construct one from any mapping of positions -- that walks it once -- and
    change it with :meth:`set` and :meth:`delete`, which return a new book in
    constant time. It compares equal to any mapping holding the same positions,
    iterates in insertion order as a ``dict`` does, and serializes as one.
    """

    __slots__ = ("_entries", "_group_totals", "_groups", "_positions", "_totals")

    _positions: PersistentMap[str, Position]
    _entries: PersistentMap[str, tuple[Decimal, Decimal, Decimal]]
    _totals: Mapping[str, CurrencyTotals]
    _groups: BookGroups | None
    _group_totals: _GroupTotals

    def __init__(self, positions: Mapping[str, Position] | None = None) -> None:
        if isinstance(positions, PositionBook):
            self._positions = positions._positions
            self._entries = positions._entries
            self._totals = positions._totals
            self._groups = positions._groups
            self._group_totals = positions._group_totals
            return
        items = dict(positions or {})
        totals: dict[str, CurrencyTotals] = {}
        entries: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
        for asset_id, position in items.items():
            entry = entries[asset_id] = _entry(position)
            totals[position.currency] = _apply(
                totals.get(position.currency, _NO_TOTALS), position, entry, 1
            )
        self._positions = PersistentMap(items)
        self._entries = PersistentMap(entries)
        self._totals = MappingProxyType(totals)
        self._groups = None
        self._group_totals = _NO_GROUP_TOTALS

    @classmethod
    def _of(
        cls,
        positions: PersistentMap[str, Position],
        entries: PersistentMap[str, tuple[Decimal, Decimal, Decimal]],
        totals: Mapping[str, CurrencyTotals],
        groups: BookGroups | None = None,
        group_totals: _GroupTotals = _NO_GROUP_TOTALS,
    ) -> PositionBook:
        book: PositionBook = cls.__new__(cls)
        book._positions = positions
        book._entries = entries
        book._totals = totals
        book._groups = groups
        book._group_totals = group_totals
        return book

    def grouped(self, groups: BookGroups | None) -> PositionBook:
        """This book keeping a gross per group of ``groups``; ``None`` keeps none.

        One pass over the positions; every later change keeps the totals at the
        cost of the changed asset's groups.
        """

        if groups is None:
            return PositionBook._of(self._positions, self._entries, self._totals)
        totals: dict[tuple[str, str, str], tuple[int, Decimal]] = {}
        for asset_id, (value, _, _) in self._entries.items():
            keys = groups.keys.get(asset_id)
            if not keys:
                continue
            currency = self._positions[asset_id].currency
            for dimension, label in keys:
                key = (dimension, label, currency)
                count, gross = totals.get(key, (0, ZERO_MONEY))
                totals[key] = (count + 1, _exact_sum(gross, value.copy_abs()))
        return PositionBook._of(
            self._positions, self._entries, self._totals, groups, PersistentMap(totals)
        )

    @property
    def groups(self) -> BookGroups | None:
        """What this book keeps a gross per group of, or ``None``."""

        return self._groups

    def group_gross(self, dimension: str, label: str) -> Mapping[str, Decimal] | None:
        """The summed absolute market value of the group's positions, per currency.

        A currency appears when the group holds a position in it, flat ones
        included, so a group whose positions are all flat reads a zero written
        in its currency's minor unit, and a group holding nothing reads empty.
        ``None`` when this book keeps no totals along ``dimension`` -- which is
        not the same as a group holding nothing.
        """

        if self._groups is None or dimension not in self._groups.dimensions:
            return None
        found: dict[str, Decimal] = {}
        for currency in self._totals:
            held = self._group_totals.get((dimension, label, currency))
            if held is not None:
                found[currency] = held[1]
        return found

    # -- the mapping --------------------------------------------------------

    def __getitem__(self, asset_id: str) -> Position:
        return self._positions[asset_id]

    def __contains__(self, asset_id: object) -> bool:
        return asset_id in self._positions

    def __iter__(self) -> Iterator[str]:
        return iter(self._positions)

    def __len__(self) -> int:
        return len(self._positions)

    def keys(self) -> KeysView[str]:
        return self._positions.keys()

    def items(self) -> ItemsView[str, Position]:
        return self._positions.items()

    def values(self) -> ValuesView[Position]:
        return self._positions.values()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PositionBook):
            return self._positions == other._positions
        if isinstance(other, Mapping):
            return dict(self._positions.items()) == dict(other.items())
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]  # a mapping, and unhashable like one

    def __repr__(self) -> str:
        return f"PositionBook({dict(self._positions.items())!r})"

    def __serializable__(self) -> dict[str, Position]:
        return dict(self._positions.items())

    def __reduce__(self) -> tuple[Any, ...]:
        return (PositionBook, (dict(self._positions.items()),))

    # -- changes ------------------------------------------------------------

    def set(self, asset_id: str, position: Position) -> PositionBook:
        """This book with ``asset_id`` holding ``position``."""

        if position.asset_id != asset_id:
            raise PortfolioError(
                f"A position for {position.asset_id!r} cannot be booked under {asset_id!r}."
            )
        entries = self._entries
        previous = self._positions.get(asset_id)
        entry = _entry(position)
        group_totals = _regrouped(
            self._groups,
            self._group_totals,
            asset_id,
            None if previous is None else (previous.currency, entries[asset_id][0]),
            (position.currency, entry[0]),
        )
        if (
            previous is not None
            and previous.currency == position.currency
            and (
                (previous.quantity > 0 and position.quantity > 0)
                or (previous.quantity < 0 and position.quantity < 0)
            )
        ):
            # Re-valued, resized or re-marked on the same side: the counts
            # stand, and each total moves by the entry's change.
            currency = position.currency
            return PositionBook._of(
                self._positions.set(asset_id, position),
                entries.set(asset_id, entry),
                MappingProxyType(
                    {
                        **self._totals,
                        currency: _moved(
                            self._totals[currency],
                            position.quantity > 0,
                            entries[asset_id],
                            entry,
                        ),
                    }
                ),
                self._groups,
                group_totals,
            )
        totals = dict(self._totals)
        if previous is not None:
            totals[previous.currency] = _apply(
                totals[previous.currency], previous, entries[asset_id], -1
            )
        totals[position.currency] = _apply(
            totals.get(position.currency, _NO_TOTALS), position, entry, 1
        )
        return PositionBook._of(
            self._positions.set(asset_id, position),
            entries.set(asset_id, entry),
            MappingProxyType({c: t for c, t in totals.items() if t.positions}),
            self._groups,
            group_totals,
        )

    def delete(self, asset_id: str) -> PositionBook:
        """This book without ``asset_id``; the same book if it holds none."""

        previous = self._positions.get(asset_id)
        if previous is None:
            return self
        totals = dict(self._totals)
        totals[previous.currency] = _apply(
            totals[previous.currency], previous, self._entries[asset_id], -1
        )
        return PositionBook._of(
            self._positions.delete(asset_id),
            self._entries.delete(asset_id),
            MappingProxyType({c: t for c, t in totals.items() if t.positions}),
            self._groups,
            _regrouped(
                self._groups,
                self._group_totals,
                asset_id,
                (previous.currency, self._entries[asset_id][0]),
                None,
            ),
        )

    # -- what the book sums to ----------------------------------------------

    @property
    def currencies(self) -> tuple[str, ...]:
        """Every currency a position in the book declares, sorted."""

        return tuple(sorted(self._totals))

    def totals(self, currency: str) -> CurrencyTotals:
        """The totals of the positions declaring ``currency``; empty ones if none do."""

        return self._totals.get(currency, _NO_TOTALS)

    def market_value(self, asset_id: str) -> Decimal:
        """``asset_id``'s market value, as the totals hold it."""

        return self._entries[asset_id][0]

    @property
    def market_values(self) -> Mapping[str, Decimal]:
        """Every position's market value, keyed by asset, in the book's order.

        A read-only view of this book, so it costs nothing to take whatever the
        book holds; each value was computed when its position was booked.
        """

        return BookMarketValues(self)


class BookMarketValues(Mapping[str, Decimal]):
    """A position book's market values, read through without copying.

    It answers :meth:`group_gross` from the same book, so a reader holding the
    values a risk check is computed from holds the group totals of exactly that
    book, never of a later or an earlier one.
    """

    __slots__ = ("_book", "_entries")

    def __init__(self, book: PositionBook) -> None:
        self._book = book
        self._entries = book._entries

    @property
    def groups(self) -> BookGroups | None:
        """:attr:`PositionBook.groups` of the book these values are of."""

        return self._book.groups

    def group_gross(self, dimension: str, label: str) -> Mapping[str, Decimal] | None:
        """:meth:`PositionBook.group_gross` of the book these values are of."""

        return self._book.group_gross(dimension, label)

    def __getitem__(self, asset_id: str) -> Decimal:
        return self._entries[asset_id][0]

    def __contains__(self, asset_id: object) -> bool:
        return asset_id in self._entries

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return repr(dict(self.items()))

    def __serializable__(self) -> dict[str, Decimal]:
        return dict(self.items())

    def __reduce__(self) -> tuple[Any, ...]:
        return (dict, (dict(self.items()),))
