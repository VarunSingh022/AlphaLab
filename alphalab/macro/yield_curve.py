"""Yield curve (term structure of interest rates) modeling and inversion signals.

Two spreads are specifically named here rather than left as generic "pick two
tenors" calls, because they are not interchangeable in practice: 2s10s (10-year
minus 2-year) is the spread most commonly cited in financial media as "the yield
curve," while 3m10y (10-year minus 3-month) is the specific spread the New York
Fed's own published recession probability model is built on. Conflating the two is
a common, real error.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.macro.exceptions import MacroInputError

TWO_YEAR = Decimal("2")
TEN_YEAR = Decimal("10")
THREE_MONTH = Decimal("0.25")


@dataclass(frozen=True, slots=True)
class YieldCurvePoint:
    """A single point on a yield curve.

    Attributes:
        tenor_years: Maturity in years, e.g. Decimal("0.25") for 3 months.
        yield_rate: The observed yield, as a decimal fraction (0.045 for 4.5%).
    """

    tenor_years: Decimal
    yield_rate: Decimal


@dataclass(frozen=True, slots=True)
class YieldCurve:
    """An immutable snapshot of yields across maturities for one country/currency.

    Attributes:
        country: ISO-3166-alpha2-style country or region code, e.g. "US".
        currency: Currency the yields are denominated in, e.g. "USD".
        timestamp: Unix timestamp this snapshot is as-of.
        points: Every observed tenor, in no particular order.
    """

    country: str
    currency: str
    timestamp: float
    points: tuple[YieldCurvePoint, ...]


def sorted_by_tenor(curve: YieldCurve) -> tuple[YieldCurvePoint, ...]:
    """Returns curve points ordered from shortest to longest tenor."""
    return tuple(sorted(curve.points, key=lambda p: p.tenor_years))


def yield_at_tenor(curve: YieldCurve, tenor_years: Decimal) -> Decimal | None:
    """Looks up the yield at a given tenor.

    Returns an exact match if present, otherwise linearly interpolates between the
    two nearest observed tenors. Returns None if the curve has no points, or the
    requested tenor falls outside the observed range (no extrapolation).
    """
    ordered = sorted_by_tenor(curve)
    if not ordered:
        return None

    for point in ordered:
        if point.tenor_years == tenor_years:
            return point.yield_rate

    if tenor_years < ordered[0].tenor_years or tenor_years > ordered[-1].tenor_years:
        return None

    lower = max((p for p in ordered if p.tenor_years < tenor_years), key=lambda p: p.tenor_years)
    upper = min((p for p in ordered if p.tenor_years > tenor_years), key=lambda p: p.tenor_years)

    weight = (tenor_years - lower.tenor_years) / (upper.tenor_years - lower.tenor_years)
    return lower.yield_rate + weight * (upper.yield_rate - lower.yield_rate)


def spread(
    curve: YieldCurve, short_tenor_years: Decimal, long_tenor_years: Decimal
) -> Decimal | None:
    """Computes long-tenor yield minus short-tenor yield.

    A negative result means the curve is inverted between these two tenors. Returns
    None if either tenor's yield is unavailable (outside the observed range).

    Raises:
        MacroInputError: If short_tenor_years is not less than long_tenor_years.
    """
    if short_tenor_years >= long_tenor_years:
        raise MacroInputError(
            f"short_tenor_years ({short_tenor_years}) must be less than "
            f"long_tenor_years ({long_tenor_years})."
        )

    short_yield = yield_at_tenor(curve, short_tenor_years)
    long_yield = yield_at_tenor(curve, long_tenor_years)
    if short_yield is None or long_yield is None:
        return None
    return long_yield - short_yield


def two_year_ten_year_spread(curve: YieldCurve) -> Decimal | None:
    """The 2s10s spread: the most commonly cited "the yield curve" measure."""
    return spread(curve, TWO_YEAR, TEN_YEAR)


def three_month_ten_year_spread(curve: YieldCurve) -> Decimal | None:
    """The 3m10y spread: the specific spread the NY Fed's recession model uses."""
    return spread(curve, THREE_MONTH, TEN_YEAR)


def is_inverted(
    curve: YieldCurve, short_tenor_years: Decimal = TWO_YEAR, long_tenor_years: Decimal = TEN_YEAR
) -> bool | None:
    """True if the curve is inverted between the given tenors (2s10s by default).

    Returns None, not False, if the spread cannot be computed -- callers should not
    treat "unknown" as "not inverted."
    """
    result = spread(curve, short_tenor_years, long_tenor_years)
    if result is None:
        return None
    return result < Decimal("0")
