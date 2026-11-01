"""Queue models: whether a resting limit order has reached the front of its price level.

Without one, a simulated resting limit order fills when the market reaches its
limit -- a bar whose low touches a buy limit fills it -- as if nothing had been
resting at that price before it. On a venue it would have joined a queue, and
traded only once what was ahead of it had traded or left. A backtest that
ignores the queue is optimistic about every passive fill, and most of all about
a market maker's (ledger FUT-011).

A :class:`QueueModel` is that question, and nothing else. It is given what one
market event showed, projected into a :class:`QueueObservation` -- the quote,
the trade print, the bar's range, each when the event carried it -- and keeps,
per resting order, a :class:`QueuePosition`: how much is ahead of it at its
limit. It answers with a :class:`QueueTurn`: the order's new position, and how
much of it may fill at this event. How much *does* fill is still the run's fill
policy, and the price and costs are still the simulator's -- the queue model
only bounds which passive fills may happen, so a run that declares one fills
through exactly the path a run that does not fills through.

Like a fill policy it depends on nothing but :mod:`alphalab.core` and never
touches state: the per-order positions are a :class:`QueueBook`, a value the
driver threads through the run (see
:class:`~alphalab.backtesting.state.QueuedResult`).

What :class:`PriceTimeQueue` assumes, and what it does not
-------------------------------------------------------------
It models one venue's **price-time (FIFO) priority over displayed liquidity**,
which is a common rule and not a universal one: a pro-rata or size-priority
book allocates differently and is another model implementing the protocol, not
a parameter of this one. Hidden and iceberg liquidity, self-trade prevention,
and orders that keep their priority when amended are not modelled -- the
simulated path has no amend; a replacement is a new order and joins at the
back. It reads only the event at hand, so it never knows more than the venue's
public feed had shown by that instant.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import Side

__all__ = [
    "PriceTimeQueue",
    "QueueBook",
    "QueueModel",
    "QueueObservation",
    "QueuePosition",
    "QueueTurn",
]

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class QueueObservation:
    """What one market event showed that bears on a resting order's queue.

    Each group is ``None`` when the event did not carry it -- a bar carries a
    range and no quote, a trade print a price and no quote -- and a model reads
    an absent group as "not observed", never as zero.

    Attributes:
        timestamp: The event's instant.
        bid, bid_size, ask, ask_size: The top of the book a quote showed.
        trade_price, trade_quantity: The print a trade or tick reported.
        aggressor: Which side crossed the spread to make the print, when the
            venue reported it -- ``BUY`` for a buyer taking the offer. A print's
            side is never inferred.
        low, high: The range a bar traded through.
    """

    timestamp: float
    bid: Decimal | None = None
    bid_size: Decimal | None = None
    ask: Decimal | None = None
    ask_size: Decimal | None = None
    trade_price: Decimal | None = None
    trade_quantity: Decimal | None = None
    aggressor: Side | None = None
    low: Decimal | None = None
    high: Decimal | None = None


@dataclass(frozen=True, slots=True)
class QueuePosition:
    """Where one resting order stands at its limit.

    Attributes:
        limit_price: The price level the order rests at.
        ahead: The quantity ahead of it at that level, or ``None`` when no
            event has shown it -- the order joined a level the feed did not
            size, a bar's or one behind the best price. An unknown queue is
            treated as long: the order fills only when the market trades
            through its limit.
    """

    limit_price: Decimal
    ahead: Decimal | None


@dataclass(frozen=True, slots=True)
class QueueTurn:
    """A model's answer for one resting order at one event.

    Attributes:
        position: The order's position after the event.
        eligible: How much of the order may fill at this event: ``None`` when
            nothing is ahead of it and the event reached its limit, so the fill
            policy alone decides; a quantity when only that much traded beyond
            the queue ahead of it; zero when it may not fill.
    """

    position: QueuePosition
    eligible: Decimal | None

    @property
    def may_fill(self) -> bool:
        """Whether any of the order may fill at this event."""

        return self.eligible is None or self.eligible > _ZERO


class QueueModel(Protocol):
    """Keeps a resting limit order's place in its price level's queue."""

    def join(
        self, side: Side, limit_price: Decimal, observation: QueueObservation
    ) -> QueuePosition:
        """Where an order resting at ``limit_price`` stands, from the event it joined at."""
        ...

    def advance(
        self, position: QueuePosition, side: Side, observation: QueueObservation
    ) -> QueueTurn:
        """The order's position after ``observation``, and what of it may fill there."""
        ...


@dataclass(frozen=True, slots=True)
class PriceTimeQueue:
    """Price-time priority over displayed liquidity, read from the top of the book and prints.

    **Joining.** An order at a price better than the best on its side has
    nothing ahead of it. At the best price, everything the quote showed there
    is ahead. Behind the best price, or on an event with no quote, the queue is
    unknown (:attr:`QueuePosition.ahead` is ``None``).

    **Advancing**, at each later event of the order's asset:

    * a quote showing the order's level as the best on its side bounds the
      queue by the size shown there -- what is shown includes everyone still
      ahead, and may include orders that arrived behind, so the bound is
      conservative; a best price *worse* than the level means nothing is
      displayed there any more, and the queue ahead has left, by trading or
      cancelling;
    * a print **at** the limit, by a seller hitting a buy level (a buyer
      lifting a sell level), consumes the queue first, and what trades beyond
      it may fill the order. A print whose side the venue did not report counts
      only with ``count_unsigned_trades``, since it may have traded on the
      other side of the book;
    * the market trading **through** the limit -- a print, a quote or a bar's
      range strictly beyond it -- cleared the level, and the order may fill;
    * the market **touching** the limit without trading through it fills the
      order only when nothing is ahead of it.

    A quote never adds to the queue ahead and nothing here is drawn at random,
    so a run is reproducible and every assumption is on the conservative side
    of what the feed showed.

    Attributes:
        count_unsigned_trades: Whether a print at the limit with no reported
            aggressor consumes the queue. Off by default: a feed that does not
            sign its prints then fills a resting order only on a trade-through
            or once its queue is displayed as gone.
    """

    count_unsigned_trades: bool = False

    def join(
        self, side: Side, limit_price: Decimal, observation: QueueObservation
    ) -> QueuePosition:
        best, size = _same_side(side, observation)
        if best is None:
            return QueuePosition(limit_price, None)
        if _better(side, limit_price, best):
            return QueuePosition(limit_price, _ZERO)
        if limit_price == best:
            return QueuePosition(limit_price, size)
        return QueuePosition(limit_price, None)

    def advance(
        self, position: QueuePosition, side: Side, observation: QueueObservation
    ) -> QueueTurn:
        limit = position.limit_price
        ahead = position.ahead

        best, size = _same_side(side, observation)
        if best is not None:
            if _better(side, limit, best):
                ahead = _ZERO
            elif best == limit and size is not None:
                ahead = size if ahead is None else min(ahead, size)

        through = False
        touched = False
        beyond: Decimal | None = None

        opposite = observation.ask if side is Side.BUY else observation.bid
        if opposite is not None:
            through = through or _better(side, limit, opposite)
            touched = touched or opposite == limit

        price = observation.trade_price
        if price is not None:
            if _better(side, limit, price):
                through = True
            elif price == limit and self._consumes(side, observation.aggressor):
                printed = observation.trade_quantity
                if ahead is not None and printed is not None:
                    if printed > ahead:
                        beyond = printed - ahead
                        ahead = _ZERO
                    else:
                        ahead = ahead - printed
                        beyond = _ZERO

        extreme = observation.low if side is Side.BUY else observation.high
        if extreme is not None:
            through = through or _better(side, limit, extreme)
            touched = touched or extreme == limit

        if through:
            return QueueTurn(QueuePosition(limit, _ZERO), None)
        if beyond is not None and beyond > _ZERO:
            return QueueTurn(QueuePosition(limit, ahead), beyond)
        if touched and ahead == _ZERO:
            return QueueTurn(QueuePosition(limit, ahead), None)
        return QueueTurn(QueuePosition(limit, ahead), _ZERO)

    def _consumes(self, side: Side, aggressor: Side | None) -> bool:
        """Whether a print at the limit traded against the order's side of the book."""

        if aggressor is None:
            return self.count_unsigned_trades
        return aggressor is not side


def _same_side(side: Side, observation: QueueObservation) -> tuple[Decimal | None, Decimal | None]:
    if side is Side.BUY:
        return observation.bid, observation.bid_size
    return observation.ask, observation.ask_size


def _better(side: Side, limit: Decimal, price: Decimal) -> bool:
    """Whether ``limit`` is strictly better for its order than ``price`` -- the market beyond it."""

    return limit > price if side is Side.BUY else limit < price


@dataclass(frozen=True, slots=True)
class QueueBook:
    """A run's queue model and the position of each resting order it governs.

    A value: every method returns a new book. Keyed by the order's identifier;
    an order is in the book from the event it joined at until it is no longer
    working.

    Attributes:
        model: The queue model the run declared.
        positions: Each governed order's position, by order id.
    """

    model: QueueModel
    positions: PersistentMap[str, QueuePosition] = field(default_factory=PersistentMap)

    def position_of(self, order_id: str) -> QueuePosition | None:
        """The order's position, or ``None`` if it has not joined."""

        return self.positions.get(order_id)

    def placed(self, order_id: str, position: QueuePosition) -> QueueBook:
        """This book with ``order_id`` at ``position``."""

        return QueueBook(self.model, self.positions.set(order_id, position))

    def without(self, order_id: str) -> QueueBook:
        """This book without ``order_id`` -- an order that is no longer working."""

        if order_id not in self.positions:
            return self
        return QueueBook(self.model, self.positions.delete(order_id))

    def keeping(self, order_ids: Iterable[str]) -> QueueBook:
        """This book holding only the orders named -- the ones still working."""

        keep = set(order_ids)
        stale = [order_id for order_id in self.positions if order_id not in keep]
        if not stale:
            return self
        positions = self.positions
        for order_id in stale:
            positions = positions.delete(order_id)
        return QueueBook(self.model, positions)
