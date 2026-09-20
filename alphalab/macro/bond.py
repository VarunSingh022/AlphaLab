"""Bond analytics: cash flows, clean and dirty price, yield, duration, convexity.

A **foundation**, and the docstring says so because the release notes are not
the place to discover it. What is here is the arithmetic that a fixed-rate bond
with known coupon dates admits exactly: its cash flows, the interest accrued
between coupons, the price implied by a yield, the yield implied by a price, and
the first two derivatives of that relationship. Every one of those is a closed
expression or a monotone inversion, and none of them needs a model.

What is deliberately **not** here is everything that does need one: credit
spreads and default, embedded calls and puts, floating coupons, inflation
linkage, and the bootstrapping of a discount curve from traded instruments. Each
requires choices -- a hazard-rate model, a short-rate model, an interpolation
scheme over an incomplete set of quotes -- and a choice made silently by a
library is the thing this repository refuses everywhere else. ROADMAP.md records
the boundary.

Clean, dirty and accrued are three numbers
-------------------------------------------

A bond quoted at 98.50 does not cost 98.50. The buyer also pays the seller the
coupon that has accrued since the last payment date, and the total -- the dirty
price -- is what changes hands. The difference is not a rounding: a 5% annual
coupon eleven months into its period is nearly five points of a hundred-point
price.

So :func:`clean_price`, :func:`dirty_price` and :func:`accrued_interest` are
three functions returning three numbers, and the identity between them is
checked rather than assumed. Which one a market quotes is a convention -- most
government markets quote clean, some quote dirty -- and no function here assumes
either.

Every convention is declared
-----------------------------

:class:`Bond` requires its day-count basis and its coupon frequency. Neither has
a default: the same coupon and the same dates under ACT/365F and 30/360 give
different accrued interest, and a yield quoted annually and one quoted
semi-annually differ by the compounding.

Units
-----

Prices and accrued interest are money, in whatever currency the face value is
in, and are :class:`~decimal.Decimal`. Yields are annualized decimal fractions
and are ``float``, like every other rate AlphaLab takes. Macaulay and modified
duration are **years**; convexity is **years squared**. They are separate
dimensions and are never added.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from alphalab.conventions.daycount import DayCount, year_fraction
from alphalab.conventions.rates import Compounding
from alphalab.macro.exceptions import MacroComputationError, MacroInputError

__all__ = [
    "MAX_YIELD",
    "MIN_YIELD",
    "YIELD_ITERATIONS",
    "YIELD_TOLERANCE",
    "Bond",
    "CashFlow",
    "accrued_interest",
    "cash_flows",
    "clean_price",
    "convexity",
    "dirty_price",
    "macaulay_duration",
    "modified_duration",
    "yield_from_clean_price",
]

_MONEY = Decimal("0.00000001")


def _periods(bond: Bond) -> int:
    """Coupons a year. ``CONTINUOUS`` is refused at construction, so this is an
    integer for every ``Bond`` that exists."""

    periods = bond.frequency.periods_per_year
    assert periods is not None  # refused by Bond.__post_init__
    return periods


#: Bounds the yield inversion searches between, as decimal fractions.
#:
#: -99% to +1000%. Wide enough for every traded bond including the deeply
#: negative yields European government paper printed from 2015, and bounded so
#: that a price outside the reachable range is a refusal rather than a search
#: that wanders.
MIN_YIELD: Final = -0.99
MAX_YIELD: Final = 10.0

#: Bisection steps before the inversion gives up, and how close the re-priced
#: value must come. The same shape as the options solver, for the same reason.
YIELD_ITERATIONS: Final = 200
YIELD_TOLERANCE: Final = 1e-12


@dataclass(frozen=True, slots=True)
class CashFlow:
    """One payment a bond makes.

    Attributes:
        payment_date: When it is paid.
        amount: How much, in the face value's currency.
        is_principal: Whether this payment includes the redemption of principal.
            The final flow carries both a coupon and the face, and reporting
            them as one payment with a flag is how a reader can tell the last
            flow from a large coupon.
    """

    payment_date: date
    amount: Decimal
    is_principal: bool


@dataclass(frozen=True, slots=True)
class Bond:
    """A fixed-rate bond with known coupon dates.

    Attributes:
        face: Redemption amount, in its own currency. Conventionally 100.
        coupon_rate: Annual coupon as a decimal fraction -- ``0.05`` for a 5%
            coupon, never ``5.0``. Zero is a valid zero-coupon bond.
        frequency: Coupons a year, and the yield's compounding basis. One
            field because for a conventional bond they are one convention: a
            semi-annual bond quotes a semi-annual yield, and pricing it off an
            annually-compounded yield is a different number reported under the
            same name. :attr:`~alphalab.conventions.rates.Compounding.CONTINUOUS`
            is refused -- no bond pays continuously. A zero-coupon bond is
            ``ANNUAL`` with a zero coupon rate, which prices correctly and still
            states its compounding.
        issue_date: The date coupons are counted forward from. Coupon dates are
            generated **backwards from maturity**, so this bounds the schedule
            rather than anchoring it -- which is how a bond with a short or long
            first coupon period actually works.
        maturity: When principal is repaid. The last coupon falls here.
        day_count: The basis accrued interest and the first stub period are
            measured under. Required.
        currency: What ``face`` is denominated in. Required and carried, so a
            price is never a number without a currency.

    Raises:
        MacroInputError: If the face is not positive, the coupon is negative,
            maturity does not follow issue, or the currency is unnamed.
    """

    face: Decimal
    coupon_rate: float
    frequency: Compounding
    issue_date: date
    maturity: date
    day_count: DayCount
    currency: str

    def __post_init__(self) -> None:
        if not self.frequency.is_periodic:
            raise MacroInputError(
                f"{self.frequency.name} is not a coupon schedule; no bond pays continuously. "
                "Name the frequency the coupons are actually paid at."
            )
        if self.face <= Decimal("0"):
            raise MacroInputError(f"face must be positive, got {self.face}.")
        if self.coupon_rate < 0.0:
            raise MacroInputError(
                f"coupon_rate is {self.coupon_rate}; a bond paying a negative coupon is the "
                "holder paying the issuer, which is not a coupon. A negative *yield* is real "
                "and is expressible -- it is a price above par, not a coupon sign."
            )
        if self.maturity <= self.issue_date:
            raise MacroInputError(
                f"maturity {self.maturity} does not follow issue_date {self.issue_date}."
            )
        if not self.currency.strip():
            raise MacroInputError(
                "A bond's face value is money and names its currency; a price without one "
                "cannot be added to a portfolio or converted."
            )

    @property
    def coupon_amount(self) -> Decimal:
        """One periodic coupon payment, in the face's currency."""

        return self.face * Decimal(str(self.coupon_rate)) / Decimal(_periods(self))


def _months_back(anchor: date, months: int) -> date:
    """``anchor`` shifted back whole months, clamped to the month's last day."""

    total = anchor.year * 12 + (anchor.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    for day in range(anchor.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise MacroComputationError(  # pragma: no cover - a month always has a day 1
        f"No valid date {months} month(s) before {anchor}."
    )


def _schedule(bond: Bond) -> tuple[date, ...]:
    """Coupon dates, generated backwards from maturity, ascending.

    Backwards because that is how a bond is actually structured: the redemption
    date is fixed and the coupons count back from it, which is what produces the
    short or long first period a forward generation would misplace.
    """

    step = 12 // _periods(bond)
    dates: list[date] = []
    cursor = bond.maturity
    while cursor > bond.issue_date:
        dates.append(cursor)
        cursor = _months_back(bond.maturity, step * (len(dates)))
    return tuple(reversed(dates))


def cash_flows(bond: Bond) -> tuple[CashFlow, ...]:
    """Every payment the bond makes, ascending by date.

    The final flow carries the last coupon **and** the face, as one payment
    marked :attr:`CashFlow.is_principal` -- which is what the issuer actually
    pays on that day.
    """

    dates = _schedule(bond)
    coupon = bond.coupon_amount
    return tuple(
        CashFlow(
            payment_date=payment_date,
            amount=coupon + bond.face if index == len(dates) - 1 else coupon,
            is_principal=index == len(dates) - 1,
        )
        for index, payment_date in enumerate(dates)
    )


def _remaining(bond: Bond, settlement: date) -> tuple[CashFlow, ...]:
    """Flows strictly after settlement. A coupon paid *on* settlement date
    belongs to the seller and is not bought."""

    return tuple(flow for flow in cash_flows(bond) if flow.payment_date > settlement)


def _coupon_period(bond: Bond, settlement: date) -> tuple[date, date]:
    """The coupon period settlement falls in, as ``(previous, next)``."""

    dates = _schedule(bond)
    following = next((day for day in dates if day > settlement), None)
    if following is None:
        raise MacroInputError(
            f"Settlement {settlement} is at or after the final coupon on {dates[-1]}; a "
            "matured bond has no accrual period and no price."
        )
    preceding = max((day for day in dates if day <= settlement), default=None)
    if preceding is None:
        step = 12 // _periods(bond)
        preceding = _months_back(following, step)
    return preceding, following


def accrued_interest(bond: Bond, settlement: date) -> Decimal:
    """Coupon earned by the seller between the last payment and settlement.

    Measured under the bond's declared :attr:`Bond.day_count`, as the fraction
    of the current coupon period elapsed. Zero on a coupon date, which is
    correct: the coupon was just paid.

    Raises:
        MacroInputError: If settlement is at or after maturity, or before the
            issue date.
    """

    if settlement < bond.issue_date:
        raise MacroInputError(
            f"Settlement {settlement} precedes issue {bond.issue_date}; nothing has accrued "
            "on a bond that does not exist yet."
        )
    previous, following = _coupon_period(bond, settlement)
    period = year_fraction(previous, following, bond.day_count)
    if period <= 0.0:  # pragma: no cover - the schedule is strictly increasing
        raise MacroComputationError(f"{previous} to {following} spans no time.")
    elapsed = year_fraction(previous, settlement, bond.day_count)
    fraction = Decimal(str(elapsed / period))
    return (bond.coupon_amount * fraction).quantize(_MONEY, rounding=ROUND_HALF_EVEN)


def _discounted(bond: Bond, yield_rate: float, settlement: date) -> list[tuple[float, float]]:
    """``(years, present value)`` for every remaining flow, in float.

    Time is measured in coupon periods rather than in calendar years so that the
    discounting agrees with the compounding basis: a semi-annual yield discounts
    over half-year periods, and measuring the exponent any other way prices a
    different bond.
    """

    periods = _periods(bond)
    previous, following = _coupon_period(bond, settlement)
    period_length = year_fraction(previous, following, bond.day_count)
    to_next = year_fraction(settlement, following, bond.day_count) / period_length

    factor = 1.0 + yield_rate / periods
    if factor <= 0.0:
        raise MacroComputationError(
            f"A yield of {yield_rate} compounded {periods} times a year makes the per-period "
            "growth factor non-positive, so a present value is not defined."
        )

    values: list[tuple[float, float]] = []
    for index, flow in enumerate(_remaining(bond, settlement)):
        exponent = to_next + index
        years = exponent / periods
        values.append((years, float(flow.amount) * factor**-exponent))
    return values


def dirty_price(bond: Bond, yield_rate: float, settlement: date) -> Decimal:
    """Present value of every remaining flow: what a buyer actually pays.

    Raises:
        MacroInputError: If settlement is outside the bond's life.
        MacroComputationError: If the yield makes the discount factor undefined.
    """

    total = sum(value for _, value in _discounted(bond, yield_rate, settlement))
    return Decimal(str(total)).quantize(_MONEY, rounding=ROUND_HALF_EVEN)


def clean_price(bond: Bond, yield_rate: float, settlement: date) -> Decimal:
    """The quoted price: dirty price less accrued interest."""

    return dirty_price(bond, yield_rate, settlement) - accrued_interest(bond, settlement)


def yield_from_clean_price(bond: Bond, price: Decimal, settlement: date) -> float:
    """The yield that reproduces a quoted clean price.

    Price falls monotonically as yield rises, so when an answer exists it is
    unique and a bisection finds it. When one does not -- a price above the
    undiscounted sum of the remaining flows, or at or below zero -- this refuses
    rather than returning a bound.

    Raises:
        MacroInputError: If the price is not positive.
        MacroComputationError: If no yield in ``[MIN_YIELD, MAX_YIELD]``
            reproduces the price, or the bisection does not converge.
    """

    if price <= Decimal("0"):
        raise MacroInputError(
            f"A clean price of {price} is not a price. A bond trading at zero has defaulted, "
            "which is a credit event this module does not model rather than a yield."
        )

    target = float(price)
    low, high = MIN_YIELD, MAX_YIELD
    at_low = float(clean_price(bond, low, settlement))
    at_high = float(clean_price(bond, high, settlement))
    if target > at_low:
        raise MacroComputationError(
            f"A clean price of {price} exceeds {at_low:.8g}, the price at the lowest yield "
            f"searched ({MIN_YIELD:.0%}). No yield in range reproduces it, and reporting the "
            "bound would present a limit as a measurement."
        )
    if target < at_high:
        raise MacroComputationError(
            f"A clean price of {price} is below {at_high:.8g}, the price at the highest yield "
            f"searched ({MAX_YIELD:.0%})."
        )

    for _ in range(YIELD_ITERATIONS):
        middle = (low + high) / 2.0
        value = float(clean_price(bond, middle, settlement))
        if abs(value - target) <= YIELD_TOLERANCE or (high - low) <= 1e-15:
            return middle
        if value > target:
            low = middle
        else:
            high = middle
    raise MacroComputationError(
        f"The yield bracket for a clean price of {price} did not close within "
        f"{YIELD_ITERATIONS} steps."
    )


def macaulay_duration(bond: Bond, yield_rate: float, settlement: date) -> float:
    """The present-value-weighted average time to a cash flow, **in years**.

    Raises:
        MacroComputationError: If the discounted flows sum to zero, which leaves
            the weighted average undefined rather than zero.
    """

    discounted = _discounted(bond, yield_rate, settlement)
    total = sum(value for _, value in discounted)
    if total == 0.0:
        raise MacroComputationError(
            "The remaining flows discount to zero, so there is no present value to weight "
            "times against and no duration."
        )
    return sum(years * value for years, value in discounted) / total


def modified_duration(bond: Bond, yield_rate: float, settlement: date) -> float:
    """Price sensitivity to a yield change, **in years**.

    ``macaulay / (1 + y/m)``. A modified duration of 7 means a 1% rise in yield
    moves the price about 7% down -- an approximation that :func:`convexity`
    corrects, and which is why both are reported.
    """

    periods = _periods(bond)
    return macaulay_duration(bond, yield_rate, settlement) / (1.0 + yield_rate / periods)


def convexity(bond: Bond, yield_rate: float, settlement: date) -> float:
    """The second derivative of price with respect to yield, **in years squared**.

    Not in years, and not comparable with a duration. The units are stated here
    because a convexity added to or compared against a duration is a category
    error that produces a plausible number.

    Raises:
        MacroComputationError: If the discounted flows sum to zero.
    """

    periods = _periods(bond)
    discounted = _discounted(bond, yield_rate, settlement)
    total = sum(value for _, value in discounted)
    if total == 0.0:
        raise MacroComputationError(
            "The remaining flows discount to zero, so convexity is undefined."
        )
    factor = 1.0 + yield_rate / periods
    weighted = sum(value * years * (years + 1.0 / periods) for years, value in discounted)
    return weighted / total / factor**2
