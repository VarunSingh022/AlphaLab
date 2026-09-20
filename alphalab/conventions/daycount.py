"""How much of a year has passed, which is not one answer.

An interest rate is quoted per year, and turning it into an accrual needs a
year fraction. Four conventions are in common use for that and they disagree:
between 2025-01-31 and 2025-07-31, ACT/365F gives 0.4959, ACT/360 gives 0.5028,
and 30/360 gives exactly 0.5. On a 100 million notional at 5% those are three
different numbers, and none of them is more correct than the others -- each is
the convention of a different market.

So the basis is named at every call. A rate without its day-count basis is not a
rate, it is a number, which is the same position
:class:`alphalab.data.assets.RateSpec` already takes by making ``day_count``
required.

What is here and what is not
-----------------------------

Three bases, each of which can be stated in one line and computed exactly. The
bond market's fuller set -- 30E/360, ACT/ACT ISDA with its leap-year split, the
ISMA variants -- differ in end-of-month rules whose correct form depends on the
instrument's own terms, and implementing them from the name alone is how a
subtly wrong accrual ships looking authoritative. They are a deliberate
boundary, recorded in ROADMAP.md rather than approximated here.
"""

from __future__ import annotations

from datetime import date
from enum import Enum, auto

from alphalab.conventions.exceptions import ConventionInputError

__all__ = ["DayCount", "day_count_days", "year_fraction"]


class DayCount(Enum):
    """Which convention converts a span of dates into a fraction of a year."""

    #: Actual days elapsed over a fixed 365-day year. The sterling and most
    #: money-market convention, and the one that never needs a leap-year rule.
    ACT_365_FIXED = auto()

    #: Actual days elapsed over a fixed 360-day year. The US and euro
    #: money-market convention. Produces a year fraction above 1.0 for a
    #: full calendar year, which is correct and routinely surprising.
    ACT_360 = auto()

    #: Every month counted as 30 days, every year as 360, under the US
    #: (Bond Basis) end-of-month rule: a start day of 31 becomes 30, and an end
    #: day of 31 becomes 30 only when the start day was already 30 or 31.
    THIRTY_360_US = auto()


def day_count_days(start: date, end: date, basis: DayCount) -> int:
    """The numerator: how many days this basis counts between two dates.

    Signed, so an ``end`` before ``start`` yields a negative count rather than
    a silent absolute value -- a negative accrual is a real answer about a date
    order the caller got wrong, and hiding it produces a positive one.
    """

    if basis is DayCount.THIRTY_360_US:
        d1, d2 = start.day, end.day
        if d1 == 31:
            d1 = 30
        if d2 == 31 and d1 == 30:
            d2 = 30
        return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)
    return (end - start).days


def year_fraction(start: date, end: date, basis: DayCount) -> float:
    """What fraction of a year separates two dates, under ``basis``.

    Raises:
        ConventionInputError: If ``end`` precedes ``start``. A negative year
            fraction turns a discount factor into a compounding one, which is a
            sign error nothing downstream would catch.
    """

    if end < start:
        raise ConventionInputError(
            f"end {end} precedes start {start}. A negative year fraction inverts every "
            "discount factor computed from it."
        )
    denominator = 360.0 if basis in (DayCount.ACT_360, DayCount.THIRTY_360_US) else 365.0
    return day_count_days(start, end, basis) / denominator
