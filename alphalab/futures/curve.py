"""Futures curve (term structure) analysis: contango and backwardation."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.futures.exceptions import FuturesInputError


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
