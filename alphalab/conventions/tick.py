"""The price grid a market quotes on, and what one step of it is worth.

Two facts are routinely confused and are not the same quantity:

* a **tick size** is a *price* increment -- 0.05 index points, 0.25 of a US
  cent, one paisa -- and has the units of a price;
* a **tick value** is *money*, and is the tick size multiplied by the contract
  multiplier. For a contract on 1,000 barrels quoted in dollars per barrel, a
  0.01 tick is worth 10.00 of the settlement currency.

Mixing them is how a risk figure comes out a thousand times too small. They are
separate types here, and :func:`tick_value` is the only place the multiplication
happens.

Tiered grids are normal, not exotic
------------------------------------

A single tick size per instrument is the exception. Most Asian and European cash
markets quote a *schedule*: finer ticks at low prices, coarser ones higher up,
and the band boundaries are published by the exchange. :class:`TickSchedule`
therefore holds bands and :class:`TickBand` is what a flat market degenerates
to -- one band covering every price -- rather than the other way round.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from enum import Enum, auto

from alphalab.conventions.exceptions import ConventionInputError, ConventionViolationError

__all__ = [
    "RoundingDirection",
    "TickBand",
    "TickSchedule",
    "TickValue",
    "is_on_tick",
    "round_to_tick",
    "tick_value",
]


class RoundingDirection(Enum):
    """Which way a price off the grid is moved onto it.

    Required at every call rather than defaulted, because the three answers are
    economically different: a buy limit rounded up crosses further into the
    book, and a sell limit rounded up may never fill. Nothing here chooses on a
    caller's behalf.
    """

    #: Toward negative infinity. The conservative side of a sell limit.
    DOWN = auto()

    #: Toward positive infinity. The conservative side of a buy limit.
    UP = auto()

    #: To the nearer tick, halves to the even one. A mid or a mark, where
    #: neither direction is the cautious one.
    NEAREST = auto()


_MODES = {
    RoundingDirection.DOWN: ROUND_FLOOR,
    RoundingDirection.UP: ROUND_CEILING,
    RoundingDirection.NEAREST: ROUND_HALF_EVEN,
}


@dataclass(frozen=True, slots=True)
class TickBand:
    """One price range and the increment quoted inside it.

    Attributes:
        upper_bound: The exclusive top of this band, or ``None`` for the last
            band, which extends without limit. Exactly one band in a schedule
            carries ``None``.
        tick_size: The price increment inside the band. A price, not money.
    """

    upper_bound: Decimal | None
    tick_size: Decimal

    def __post_init__(self) -> None:
        if self.tick_size <= Decimal("0"):
            raise ConventionInputError(
                f"tick_size is {self.tick_size}; a non-positive increment is not a price grid."
            )
        if self.upper_bound is not None and self.upper_bound <= Decimal("0"):
            raise ConventionInputError(
                f"upper_bound is {self.upper_bound}; a band ending at or below zero holds no "
                "quotable price."
            )


@dataclass(frozen=True, slots=True)
class TickSchedule:
    """An instrument's whole price grid, band by band.

    Attributes:
        bands: Ascending by ``upper_bound``, the last one unbounded. Validated
            on construction, because a schedule with a gap or an overlap has two
            answers for one price and would pick by iteration order.
    """

    bands: tuple[TickBand, ...]

    def __post_init__(self) -> None:
        if not self.bands:
            raise ConventionInputError("A tick schedule declares at least one band.")
        for index, band in enumerate(self.bands[:-1]):
            if band.upper_bound is None:
                raise ConventionInputError(
                    f"Band {index} is unbounded but is not the last of {len(self.bands)}; "
                    "every band after it would be unreachable."
                )
        if self.bands[-1].upper_bound is not None:
            raise ConventionInputError(
                f"The last band ends at {self.bands[-1].upper_bound}, so a price above it has "
                "no tick size. The highest band is unbounded; pass upper_bound=None."
            )
        previous: Decimal | None = None
        for index, band in enumerate(self.bands[:-1]):
            bound = band.upper_bound
            assert bound is not None  # established by the unbounded-band check above
            if previous is not None and bound <= previous:
                raise ConventionInputError(
                    f"Band {index} ends at {bound}, which does not exceed the previous band's "
                    f"{previous}. Bands ascend and do not overlap."
                )
            previous = bound

    @classmethod
    def flat(cls, tick_size: Decimal) -> TickSchedule:
        """One increment at every price -- the shape most futures markets use."""

        return cls((TickBand(upper_bound=None, tick_size=tick_size),))

    @property
    def is_flat(self) -> bool:
        """Whether one increment covers every price."""

        return len(self.bands) == 1

    def tick_size_at(self, price: Decimal) -> Decimal:
        """The increment quoted at ``price``.

        Raises:
            ConventionViolationError: If ``price`` is negative. A negative price
                is real in some markets -- WTI settled below zero in April 2020
                -- but no exchange publishes a tick schedule for one, and
                returning the lowest band's increment would be an invented
                answer rather than a read one.
        """

        if price < Decimal("0"):
            raise ConventionViolationError(
                f"No band covers the negative price {price}. A tick schedule is published "
                "over non-negative prices; a negative print needs a schedule that declares it."
            )
        for band in self.bands:
            if band.upper_bound is None or price < band.upper_bound:
                return band.tick_size
        raise ConventionViolationError(  # pragma: no cover - the last band is unbounded
            f"No band covers {price}, which a validated schedule cannot produce."
        )


@dataclass(frozen=True, slots=True)
class TickValue:
    """What one tick is worth, and in what.

    A separate type from the tick size on purpose: this one is **money per
    contract** and the other is a **price**, and a field that held either would
    let them be added.

    Attributes:
        amount: Money per contract for a one-tick move.
        currency: What that money is denominated in.
        tick_size: The price increment it was derived from, kept so the figure
            can be audited rather than merely trusted.
        multiplier: The contract multiplier it was derived from.
    """

    amount: Decimal
    currency: str
    tick_size: Decimal
    multiplier: Decimal


def tick_value(
    tick_size: Decimal, multiplier: Decimal, currency: str, price: Decimal | None = None
) -> TickValue:
    """What a one-tick move is worth on one contract.

    ``price`` is accepted only so that a tiered instrument can record which
    band's increment produced the figure; the arithmetic is
    ``tick_size * multiplier`` either way. Resolving a schedule to a tick size
    is :meth:`TickSchedule.tick_size_at`'s job, and doing it here too would be a
    second answer to one question.

    Raises:
        ConventionInputError: If ``tick_size`` or ``multiplier`` is not
            positive, or ``currency`` is unnamed.
    """

    if tick_size <= Decimal("0"):
        raise ConventionInputError(f"tick_size is {tick_size}; a tick is a positive increment.")
    if multiplier <= Decimal("0"):
        raise ConventionInputError(
            f"multiplier is {multiplier}; a contract controlling nothing has no tick value."
        )
    if not currency.strip():
        raise ConventionInputError(
            "A tick value is money and names its currency; an unnamed one cannot be added to "
            "anything or converted."
        )
    if price is not None and price < Decimal("0"):
        raise ConventionInputError(f"price is {price}; a tick value is not read at a negative.")
    return TickValue(
        amount=tick_size * multiplier,
        currency=currency.strip(),
        tick_size=tick_size,
        multiplier=multiplier,
    )


def is_on_tick(price: Decimal, tick_size: Decimal) -> bool:
    """Whether ``price`` is an exact multiple of ``tick_size``.

    Raises:
        ConventionInputError: If ``tick_size`` is not positive.
    """

    if tick_size <= Decimal("0"):
        raise ConventionInputError(f"tick_size is {tick_size}; a tick is a positive increment.")
    return price % tick_size == Decimal("0")


def round_to_tick(price: Decimal, tick_size: Decimal, direction: RoundingDirection) -> Decimal:
    """Move ``price`` onto the grid, the way ``direction`` says.

    Raises:
        ConventionInputError: If ``tick_size`` is not positive.
    """

    if tick_size <= Decimal("0"):
        raise ConventionInputError(f"tick_size is {tick_size}; a tick is a positive increment.")
    steps = (price / tick_size).quantize(Decimal("1"), rounding=_MODES[direction])
    return steps * tick_size
