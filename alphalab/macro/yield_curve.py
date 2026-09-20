"""Yield curve (term structure of interest rates) modeling and inversion signals.

Two spreads are specifically named here rather than left as generic "pick two
tenors" calls, because they are not interchangeable in practice: 2s10s (10-year
minus 2-year) is the spread most commonly cited in financial media as "the yield
curve," while 3m10y (10-year minus 3-month) is the specific spread the New York
Fed's own published recession probability model is built on. Conflating the two is
a common, real error.

Observed, not constructed
--------------------------

A :class:`YieldCurve` is a set of **observed** yields at observed tenors. It is
not a bootstrapped discount curve, and v3.4 deliberately did not make it one.
Bootstrapping requires choosing an interpolation scheme over an incomplete set
of quotes, and the choice changes every forward rate read off the result -- a
log-linear discount interpolation and a linear-in-yield one disagree most
exactly where the quotes are sparsest, which is where a forward is most often
wanted. That choice belongs to whoever is doing the research, and a library
making it silently would be the "hidden market assumption" this release exists
to remove. ROADMAP.md records the boundary.

What v3.4 does add is :func:`discount_factor_at`, which turns an *observed*
yield into a discount factor under a **named** compounding convention. It reads
the curve through :func:`yield_at_tenor` -- the same linear-in-yield
interpolation that has been this module's stated behaviour since v1, with the
same refusal to extrapolate -- rather than introducing a second way to read a
curve.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.conventions.rates import Compounding
from alphalab.conventions.rates import discount_factor as _discount_factor
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


def discount_factor_at(
    curve: YieldCurve, tenor_years: Decimal, compounding: Compounding
) -> float | None:
    """What one unit received at ``tenor_years`` is worth now, on this curve.

    ``compounding`` is required and has no default: the same observed 5% gives
    0.6139 annually compounded and 0.6065 continuously over ten years, and
    nothing in the curve says which convention its yields were quoted under.
    That is the publisher's fact and the caller's to state.

    Returns ``None`` -- not ``1.0`` and not an extrapolated factor -- when
    :func:`yield_at_tenor` has no yield for the tenor, for the reason that
    function returns ``None``: a tenor outside the observed range was never
    quoted, and a discount factor for it would be invented.

    Raises:
        MacroInputError: If ``tenor_years`` is negative. A negative tenor
            compounds forward while being read as a discount.
    """
    if tenor_years < Decimal("0"):
        raise MacroInputError(
            f"tenor_years is {tenor_years}; a discount factor is read over a forward period."
        )
    observed = yield_at_tenor(curve, tenor_years)
    if observed is None:
        return None
    return _discount_factor(float(observed), float(tenor_years), compounding)
