"""How much of a year has passed, which is not one answer.

An interest rate is quoted per year, and turning it into an accrual needs a
year fraction. The conventions in use for that disagree: between 2025-01-31 and
2025-07-31, ACT/365F gives 0.4959, ACT/360 gives 0.5028, and 30/360 gives
exactly 0.5. On a 100 million notional at 5% those are three different numbers,
and none of them is more correct than the others -- each is the convention of a
different market.

So the basis is named at every call. A rate without its day-count basis is not a
rate, it is a number, which is the same position
:class:`alphalab.data.assets.RateSpec` already takes by making ``day_count``
required.

The world's conventions (v4.3, N21, GAP-36)
-------------------------------------------
Until v4.3 three bases were here, the US and money-market ones. The others the
world's markets use are here now, each by its published definition (ISDA 2006
Definitions, section 4.16; ICMA Rule 251):

* **30E/360**, the Eurobond basis: both day-of-month 31s become 30.
* **30E/360 (ISDA)**: a last day of the month becomes 30, except a February end
  that is the schedule's termination date.
* **ACT/ACT (ISDA)**: the days in each calendar year over that year's length.
* **ACT/ACT (ICMA)**: the days over the frequency times the coupon period's
  days -- the government bond basis of the US, UK, euro area and Japan.
* **ACT/365L**: the days over 365, or 366 when a leap day bears on the period.
* **NL/365**: the days less each 29 February, over 365.
* **BUS/252**: Brazilian business days over 252.

Several need what only the instrument knows: ICMA and ACT/365L their coupon
period and frequency, 30E/360 (ISDA) its termination date, BUS/252 its business
calendar. They take those as :class:`DayCountTerms`, keyword-only, and refuse
without them. Nothing is inferred from a name: a basis that needs the coupon
period never guesses one, and a terms field a basis does not use is refused
rather than ignored. Three bases have no single year -- ACT/ACT (ISDA), ACT/ACT
(ICMA) and ACT/365L -- so :func:`days_in_year` refuses them; their year fraction
is computed exactly as a rational and rounded once.

An ICMA or ACT/365L accrual lies within its stated period. A calculation period
longer than one determination period -- a long first coupon -- is, by the
definition (ISDA 4.16(c)), the sum of its part in each determination period,
each over its own period: the caller, who holds the schedule and its
end-of-month rule, splits it, and nothing here infers the notional periods.
The published examples are held in ``tests/unit/conventions``.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum, auto
from fractions import Fraction
from typing import Final

from alphalab.conventions.exceptions import ConventionInputError

__all__ = [
    "DayCount",
    "DayCountTerms",
    "day_count_days",
    "days_in_year",
    "require_accrual_basis",
    "required_terms",
    "year_fraction",
]


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

    #: 30E/360, the Eurobond basis (ISDA 4.16(g)): a start or end day of 31
    #: becomes 30, whatever the other is.
    THIRTY_E_360 = auto()

    #: 30E/360 (ISDA 4.16(h)): a start or end on the last day of its month
    #: becomes 30, except an end on the last day of February that is the
    #: termination date. Needs :attr:`DayCountTerms.termination`.
    THIRTY_E_360_ISDA = auto()

    #: ACT/ACT (ISDA 4.16(b)): the days falling in each calendar year over that
    #: year's 365 or 366, summed.
    ACT_ACT_ISDA = auto()

    #: ACT/ACT (ICMA Rule 251): the days over ``frequency`` times the days of the
    #: coupon period that holds them. Needs the period and frequency.
    ACT_ACT_ICMA = auto()

    #: ACT/365L: the days over 366 if a 29 February falls in the coupon period
    #: (annual coupons) or the period ends in a leap year (otherwise), over 365
    #: if not. Needs the period and frequency.
    ACT_365_LEAP = auto()

    #: NL/365: the days less each 29 February between the dates, over 365.
    NO_LEAP_365 = auto()

    #: BUS/252: the business days from the start (included) to the end
    #: (excluded) over 252 -- the Brazilian convention. Needs the calendar.
    BUSINESS_252 = auto()


@dataclass(frozen=True, slots=True)
class DayCountTerms:
    """What an instrument states that some day counts need (v4.3).

    Each basis takes exactly the fields :func:`required_terms` names; a field
    it does not use is refused, not ignored.

    Attributes:
        period_start: The coupon period's first date (ICMA, ACT/365L).
        period_end: The coupon period's last date (ICMA, ACT/365L).
        frequency: Coupons per year, a positive integer (ICMA, ACT/365L).
        termination: The schedule's final date (30E/360 ISDA).
        holidays: Dates that are not business days, beyond the weekend
            (BUS/252). An empty set is a statement that there are none.
        weekend: The weekdays (``date.weekday()``, Monday 0) that are not
            business days (BUS/252).
    """

    period_start: date | None = None
    period_end: date | None = None
    frequency: int | None = None
    termination: date | None = None
    holidays: frozenset[date] | None = None
    weekend: frozenset[int] | None = None

    def __post_init__(self) -> None:
        for name in ("period_start", "period_end", "termination"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, date):
                raise ConventionInputError(f"{name} must be a date, got {value!r}.")
        if self.frequency is not None and (
            isinstance(self.frequency, bool)
            or not isinstance(self.frequency, int)
            or self.frequency < 1
        ):
            raise ConventionInputError(
                f"frequency must be a positive integer, got {self.frequency!r}."
            )
        if self.holidays is not None:
            holidays = frozenset(self.holidays)
            if not all(isinstance(day, date) for day in holidays):
                raise ConventionInputError("holidays must be dates.")
            object.__setattr__(self, "holidays", holidays)
        if self.weekend is not None:
            weekend = frozenset(self.weekend)
            if not all(
                isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6
                for day in weekend
            ):
                raise ConventionInputError("weekend must be weekday numbers, Monday 0 to Sunday 6.")
            object.__setattr__(self, "weekend", weekend)

    def given(self) -> frozenset[str]:
        """The fields that are stated."""

        return frozenset(
            name
            for name in (
                "period_start",
                "period_end",
                "frequency",
                "termination",
                "holidays",
                "weekend",
            )
            if getattr(self, name) is not None
        )


_COUPON_PERIOD: Final = frozenset({"period_start", "period_end", "frequency"})

#: What each basis needs from its instrument, stated per member: a basis added
#: later is refused until its terms are written here.
_REQUIRED: Final[dict[DayCount, frozenset[str]]] = {
    DayCount.ACT_365_FIXED: frozenset(),
    DayCount.ACT_360: frozenset(),
    DayCount.THIRTY_360_US: frozenset(),
    DayCount.THIRTY_E_360: frozenset(),
    DayCount.THIRTY_E_360_ISDA: frozenset({"termination"}),
    DayCount.ACT_ACT_ISDA: frozenset(),
    DayCount.ACT_ACT_ICMA: _COUPON_PERIOD,
    DayCount.ACT_365_LEAP: _COUPON_PERIOD,
    DayCount.NO_LEAP_365: frozenset(),
    DayCount.BUSINESS_252: frozenset({"holidays", "weekend"}),
}

#: The year each basis divides by, stated per member: a basis added later is
#: refused by :func:`days_in_year` until its year is written here, rather than
#: divided by whichever year a fall-through would pick. The three bases whose
#: year varies are absent on purpose: see :data:`_VARIABLE_YEAR`.
_YEAR_DAYS: Final[dict[DayCount, int]] = {
    DayCount.ACT_365_FIXED: 365,
    DayCount.ACT_360: 360,
    DayCount.THIRTY_360_US: 360,
    DayCount.THIRTY_E_360: 360,
    DayCount.THIRTY_E_360_ISDA: 360,
    DayCount.NO_LEAP_365: 365,
    DayCount.BUSINESS_252: 252,
}

#: The bases with no single year: their denominator depends on the dates.
_VARIABLE_YEAR: Final = frozenset(
    {DayCount.ACT_ACT_ISDA, DayCount.ACT_ACT_ICMA, DayCount.ACT_365_LEAP}
)


def required_terms(basis: DayCount) -> frozenset[str]:
    """The :class:`DayCountTerms` fields ``basis`` needs, and the only ones it takes.

    Raises:
        ConventionInputError: If ``basis`` is not a :class:`DayCount` whose terms
            are stated.
    """

    try:
        return _REQUIRED[basis]
    except (KeyError, TypeError) as exc:
        raise ConventionInputError(f"No terms are stated for the day count {basis!r}.") from exc


def _terms(basis: DayCount, terms: DayCountTerms | None) -> DayCountTerms:
    needed = required_terms(basis)
    if terms is not None and not isinstance(terms, DayCountTerms):
        raise ConventionInputError(f"terms must be DayCountTerms, got {terms!r}.")
    stated = terms or DayCountTerms()
    given = stated.given()
    missing = sorted(needed - given)
    if missing:
        raise ConventionInputError(
            f"{basis.name} needs {', '.join(missing)} in its terms: the instrument states "
            "them, and a day count never infers them."
        )
    unused = sorted(given - needed)
    if unused:
        raise ConventionInputError(
            f"{basis.name} takes no {', '.join(unused)}; a term the basis does not use is "
            "refused rather than ignored, since passing it means it was expected to matter."
        )
    return stated


def days_in_year(basis: DayCount) -> int:
    """The denominator: how many days ``basis`` counts in a year.

    Raises:
        ConventionInputError: If ``basis`` is not a :class:`DayCount` whose year
            is stated, or is one of the three whose year depends on the dates.
    """

    if basis in _VARIABLE_YEAR:
        raise ConventionInputError(
            f"{basis.name} has no single year: its denominator depends on the dates. Use "
            "year_fraction."
        )
    try:
        return _YEAR_DAYS[basis]
    except (KeyError, TypeError) as exc:
        raise ConventionInputError(f"No year is stated for the day count {basis!r}.") from exc


def require_accrual_basis(basis: DayCount) -> None:
    """Refuse a basis that cannot weight a single day on its own.

    A daily accrual -- financing, a borrow fee -- weights each day by the
    basis's count for it over the basis's year. That needs a basis with a fixed
    year that takes no terms: ACT/365F, ACT/360, 30/360, 30E/360 and NL/365.

    Raises:
        ConventionInputError: For any other basis, naming why.
    """

    needed = required_terms(basis)
    if needed:
        raise ConventionInputError(
            f"{basis.name} needs {', '.join(sorted(needed))} from its instrument, which a daily "
            "accrual does not carry."
        )
    days_in_year(basis)


def _last_day_of_month(day: date) -> bool:
    return day.day == calendar.monthrange(day.year, day.month)[1]


def _leap_days(start: date, end: date) -> int:
    """How many 29 Februaries fall in ``(start, end]``."""

    count = 0
    for year in range(start.year, end.year + 1):
        if calendar.isleap(year) and start < date(year, 2, 29) <= end:
            count += 1
    return count


def _business_days(start: date, end: date, terms: DayCountTerms) -> int:
    """Business days in ``[start, end)``."""

    holidays = terms.holidays or frozenset()
    weekend = terms.weekend or frozenset()
    count = 0
    day = start
    while day < end:
        if day.weekday() not in weekend and day not in holidays:
            count += 1
        day += timedelta(days=1)
    return count


def day_count_days(
    start: date, end: date, basis: DayCount, *, terms: DayCountTerms | None = None
) -> int:
    """The numerator: how many days this basis counts between two dates.

    Signed, so an ``end`` before ``start`` yields a negative count rather than
    a silent absolute value -- a negative accrual is a real answer about a date
    order the caller got wrong, and hiding it produces a positive one. For the
    ACT/ACT bases and ACT/365L it is the actual days.

    Raises:
        ConventionInputError: If the basis's terms are missing, or a term it
            does not use is given.
    """

    stated = _terms(basis, terms)
    if basis is DayCount.THIRTY_360_US:
        d1, d2 = start.day, end.day
        if d1 == 31:
            d1 = 30
        if d2 == 31 and d1 == 30:
            d2 = 30
        return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)
    if basis is DayCount.THIRTY_E_360:
        d1, d2 = min(start.day, 30), min(end.day, 30)
        return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)
    if basis is DayCount.THIRTY_E_360_ISDA:
        d1 = 30 if _last_day_of_month(start) else start.day
        february_termination = end == stated.termination and end.month == 2
        d2 = 30 if _last_day_of_month(end) and not february_termination else end.day
        return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)
    if basis is DayCount.NO_LEAP_365:
        if end < start:
            return -((start - end).days - _leap_days(end, start))
        return (end - start).days - _leap_days(start, end)
    if basis is DayCount.BUSINESS_252:
        if end < start:
            return -_business_days(end, start, stated)
        return _business_days(start, end, stated)
    return (end - start).days


def _within_period(start: date, end: date, basis: DayCount, terms: DayCountTerms) -> int:
    """The coupon period's days, after checking the accrual lies within it."""

    period_start, period_end, frequency = terms.period_start, terms.period_end, terms.frequency
    assert period_start is not None and period_end is not None and frequency is not None
    if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency < 1:
        raise ConventionInputError(f"frequency must be a positive integer, got {frequency!r}.")
    if not period_start < period_end:
        raise ConventionInputError(
            f"The coupon period {period_start} to {period_end} spans no time."
        )
    if not period_start <= start <= end <= period_end:
        raise ConventionInputError(
            f"{basis.name} measures an accrual within its coupon period; {start} to {end} is "
            f"not within {period_start} to {period_end}. An accrual across periods, or in an "
            "irregular one, follows the schedule's own notional periods, which are not "
            "inferred here."
        )
    return (period_end - period_start).days


def year_fraction(
    start: date, end: date, basis: DayCount, *, terms: DayCountTerms | None = None
) -> float:
    """What fraction of a year separates two dates, under ``basis``.

    A fixed-year basis divides its count by its year. ACT/ACT (ISDA), ACT/ACT
    (ICMA) and ACT/365L are computed as exact rationals and rounded once.

    Args:
        start: The first date.
        end: The last date, not before ``start``.
        basis: The convention.
        terms: What the basis needs from its instrument; see
            :func:`required_terms`. Keyword-only.

    Raises:
        ConventionInputError: If ``end`` precedes ``start`` -- a negative year
            fraction turns a discount factor into a compounding one, which is a
            sign error nothing downstream would catch -- or the terms are
            missing, unused or inconsistent with the dates.
    """

    if end < start:
        raise ConventionInputError(
            f"end {end} precedes start {start}. A negative year fraction inverts every "
            "discount factor computed from it."
        )
    stated = _terms(basis, terms)
    if basis is DayCount.ACT_ACT_ISDA:
        total = Fraction(0)
        cursor = start
        while cursor < end:
            boundary = min(date(cursor.year + 1, 1, 1), end)
            total += Fraction(
                (boundary - cursor).days, 366 if calendar.isleap(cursor.year) else 365
            )
            cursor = boundary
        return float(total)
    if basis is DayCount.ACT_ACT_ICMA:
        period_days = _within_period(start, end, basis, stated)
        assert stated.frequency is not None
        return float(Fraction((end - start).days, stated.frequency * period_days))
    if basis is DayCount.ACT_365_LEAP:
        _within_period(start, end, basis, stated)
        assert stated.period_start is not None and stated.period_end is not None
        if stated.frequency == 1:
            leap = _leap_days(stated.period_start, stated.period_end) > 0
        else:
            leap = calendar.isleap(stated.period_end.year)
        return float(Fraction((end - start).days, 366 if leap else 365))
    return day_count_days(start, end, basis, terms=terms) / days_in_year(basis)
