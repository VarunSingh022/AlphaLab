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

__all__ = ["CurrencyTotals", "PositionBook"]

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

    __slots__ = ("_entries", "_positions", "_totals")

    _positions: PersistentMap[str, Position]
    _entries: PersistentMap[str, tuple[Decimal, Decimal, Decimal]]
    _totals: Mapping[str, CurrencyTotals]

    def __init__(self, positions: Mapping[str, Position] | None = None) -> None:
        if isinstance(positions, PositionBook):
            self._positions = positions._positions
            self._entries = positions._entries
            self._totals = positions._totals
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

    @classmethod
    def _of(
        cls,
        positions: PersistentMap[str, Position],
        entries: PersistentMap[str, tuple[Decimal, Decimal, Decimal]],
        totals: Mapping[str, CurrencyTotals],
    ) -> PositionBook:
        book: PositionBook = cls.__new__(cls)
        book._positions = positions
        book._entries = entries
        book._totals = totals
        return book

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

        return _MarketValues(self._entries)


class _MarketValues(Mapping[str, Decimal]):
    """A position book's market values, read through without copying."""

    __slots__ = ("_entries",)

    def __init__(self, entries: PersistentMap[str, tuple[Decimal, Decimal, Decimal]]) -> None:
        self._entries = entries

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
