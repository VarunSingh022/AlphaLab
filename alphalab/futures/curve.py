"""Futures curve (term structure) analysis: contango and backwardation.

Two measurements, deliberately kept apart
------------------------------------------

:func:`curve_slope` compares the **nearest and furthest** months and has since
v1. It answers "is the far end above the near end", which is what a carry
screen wants and is a single number.

:func:`curve_shape` reads **every adjacent pair**. The difference matters on the
curves where it matters most: a humped curve -- backwardated at the front,
contango behind it, which is the normal shape of a crude curve in a supply
squeeze -- has a positive endpoint slope and is not in contango. Reporting it as
contango is not a rounding error; it is the wrong answer about the part of the
curve a spread trades on. So the endpoint measure keeps its name and its
meaning, and the pairwise one has its own.

:func:`roll_yield` annualizes a carry between two named months. It is reported
per year because a 2% pickup over one month and the same 2% over six are not
the same trade, and an un-annualized figure invites them to be compared.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from itertools import pairwise

from alphalab.futures.exceptions import FuturesInputError

#: Seconds in a year, matching ``alphalab.options.pricing``'s convention so that
#: an annualized futures carry and an annualized option input mean the same
#: "year". 365.25 days, which averages the leap cycle rather than dropping it.
SECONDS_PER_YEAR = 365.25 * 86400


@dataclass(frozen=True, slots=True)
class FuturesCurvePoint:
    """A single point on a futures curve.

    Attributes:
        contract_month: Unix timestamp representing the delivery month.
        price: Settlement or last price for that contract month.
    """

    contract_month: float
    price: Decimal


@dataclass(frozen=True, slots=True)
class FuturesCurve:
    """An immutable snapshot of prices across contract months for one underlying.

    Attributes:
        underlying_asset_id: Root symbol of the underlying.
        timestamp: Unix timestamp this snapshot is as-of.
        points: Every observed contract month, in no particular order.
    """

    underlying_asset_id: str
    timestamp: float
    points: tuple[FuturesCurvePoint, ...]


def sorted_by_month(curve: FuturesCurve) -> tuple[FuturesCurvePoint, ...]:
    """Returns curve points ordered from nearest to furthest contract month."""
    return tuple(sorted(curve.points, key=lambda p: p.contract_month))


def curve_slope(curve: FuturesCurve) -> Decimal:
    """Computes (far_price - near_price) / near_price between the nearest and
    furthest contract months on the curve.

    Positive values indicate contango (far months priced above near months);
    negative values indicate backwardation.

    Raises:
        FuturesInputError: If the curve has fewer than two points, or the nearest
            month's price is not positive.
    """
    ordered = sorted_by_month(curve)
    if len(ordered) < 2:
        raise FuturesInputError("curve_slope requires at least two contract months.")

    near, far = ordered[0], ordered[-1]
    if near.price <= Decimal("0"):
        raise FuturesInputError(f"Nearest month price must be positive, got {near.price}.")

    return (far.price - near.price) / near.price


def is_contango(curve: FuturesCurve) -> bool:
    """True if the curve slopes upward (far months priced above near months)."""
    return curve_slope(curve) > Decimal("0")


def is_backwardation(curve: FuturesCurve) -> bool:
    """True if the curve slopes downward (far months priced below near months)."""
    return curve_slope(curve) < Decimal("0")


class CurveShape(Enum):
    """What a term structure does across every adjacent pair of months."""

    #: Every adjacent pair rises: each further month is priced above the one
    #: before it.
    CONTANGO = auto()

    #: Every adjacent pair falls.
    BACKWARDATION = auto()

    #: Every adjacent pair is equal. Rare in a traded curve and real in a
    #: constructed one, and not the same answer as MIXED.
    FLAT = auto()

    #: Some pairs rise and some fall -- a humped or a dipped curve. This is the
    #: answer :func:`curve_slope` cannot give, and the one that stops a humped
    #: curve being reported as whichever its endpoints happen to suggest.
    MIXED = auto()


def curve_shape(curve: FuturesCurve) -> CurveShape:
    """Classify a curve by reading every adjacent pair of months.

    Raises:
        FuturesInputError: If the curve has fewer than two points. One month is
            not a term structure, and calling it flat would assert something
            about a shape that was never observed.
    """
    ordered = sorted_by_month(curve)
    if len(ordered) < 2:
        raise FuturesInputError("curve_shape requires at least two contract months.")

    rising = falling = False
    for near, far in pairwise(ordered):
        if far.price > near.price:
            rising = True
        elif far.price < near.price:
            falling = True

    if rising and falling:
        return CurveShape.MIXED
    if rising:
        return CurveShape.CONTANGO
    if falling:
        return CurveShape.BACKWARDATION
    return CurveShape.FLAT


def roll_yield(curve: FuturesCurve, near_month: float, far_month: float) -> Decimal:
    """Annualized carry from holding the near month against the far one.

    ``(near_price - far_price) / far_price``, scaled to a year by the time
    between the two contract months. Positive in backwardation -- the near month
    rolls *up* the curve toward the far one as it ages, which is the return a
    long roll earns -- and negative in contango.

    The sign convention is stated because both are in use: this one reports the
    return to being long the front month, so that a positive figure means the
    position makes money from the shape of the curve.

    Args:
        curve: The observed term structure.
        near_month: Contract month of the leg held, as a Unix timestamp.
        far_month: Contract month rolled toward. Must be strictly later.

    Raises:
        FuturesInputError: If either month is absent from the curve, the far
            month is not later than the near one, or the far price is not
            positive.
    """
    prices = {point.contract_month: point.price for point in curve.points}
    for label, month in (("near_month", near_month), ("far_month", far_month)):
        if month not in prices:
            raise FuturesInputError(
                f"{label} {month} is not on the curve for {curve.underlying_asset_id}; "
                f"observed months are {sorted(prices)}. Interpolating a month that was not "
                "quoted would invent the price the yield is computed from."
            )
    if far_month <= near_month:
        raise FuturesInputError(
            f"far_month {far_month} is not later than near_month {near_month}; a roll yield "
            "is measured across time, and reversing the legs reverses its sign silently."
        )
    far_price = prices[far_month]
    if far_price <= Decimal("0"):
        raise FuturesInputError(f"Far month price must be positive, got {far_price}.")

    years = Decimal(str((far_month - near_month) / SECONDS_PER_YEAR))
    return (prices[near_month] - far_price) / far_price / years
