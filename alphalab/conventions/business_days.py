"""Business days, tenors and FX value dates, each by a declared rule (v4.3, plan N21, ADR-0067).

A payment date that falls on a holiday moves, and which way it moves is a
convention, not a fact of the date. A tenor added to a date lands on a business
day by a stated convention, and from the last business day of a month it may be
held to the last business day of its month. An FX deal's value date is counted
in business days of the calendars its rule names, and must fall on a day the
calendars it settles in are all open. None of that is a property of a currency
or a market's name, so every rule here is **declared**, and every calendar is
**supplied**, as :mod:`alphalab.conventions.settlement` takes its calendar: by
the :class:`~alphalab.conventions.settlement.TradingDayCalendar` protocol, so
this package still imports nothing but ``alphalab.common``.

Business-day conventions (ISDA 2006 Definitions, section 4.12)
--------------------------------------------------------------
* ``FOLLOWING``: the first following business day.
* ``MODIFIED_FOLLOWING``: the first following business day, unless that is in
  the next calendar month, then the first preceding one.
* ``PRECEDING``: the first preceding business day.
* ``UNADJUSTED``: the date itself, business day or not: a stated choice, for
  the dates a contract leaves unadjusted.

Value dates
-----------
:class:`ValueDateRule` states how many business days spot is after the trade,
which calendars a counted day must be open in, and which calendars the value
date must be open in. A pair against the US dollar commonly counts its lag in
the other currency's calendar alone -- a US holiday the day after the trade
does not delay spot -- while the value date must be a business day in both. A
cross commonly counts in both its currencies' calendars and must also settle on
a US business day. Both are stated by the calendars listed, never inferred
from the pair.
"""

from __future__ import annotations

import calendar as gregorian
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from alphalab.conventions.exceptions import ConventionInputError, ConventionViolationError
from alphalab.conventions.settlement import MAX_SETTLEMENT_SEARCH_DAYS, TradingDayCalendar

__all__ = [
    "BusinessDayConvention",
    "Tenor",
    "TenorUnit",
    "ValueDateRule",
    "add_business_days",
    "add_tenor",
    "adjust",
    "forward_value_date",
    "is_business_day",
    "spot_value_date",
]


class BusinessDayConvention(Enum):
    """How a date that is not a business day moves (ISDA 2006 Definitions, section 4.12)."""

    FOLLOWING = "following"
    MODIFIED_FOLLOWING = "modified_following"
    PRECEDING = "preceding"
    UNADJUSTED = "unadjusted"


class TenorUnit(Enum):
    """What a tenor counts."""

    DAYS = "D"
    WEEKS = "W"
    MONTHS = "M"
    YEARS = "Y"


@dataclass(frozen=True, slots=True)
class Tenor:
    """A length of time a schedule steps by: ``count`` of ``unit``, ``3M`` or ``1Y``.

    Raises:
        ConventionInputError: If ``count`` is not a positive integer or ``unit``
            not a :class:`TenorUnit`.
    """

    count: int
    unit: TenorUnit

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ConventionInputError(f"A tenor's count is {self.count!r}; a positive integer.")
        if not isinstance(self.unit, TenorUnit):
            raise ConventionInputError(f"A tenor's unit must be a TenorUnit, got {self.unit!r}.")

    @classmethod
    def parse(cls, text: str) -> Tenor:
        """``"3M"``, ``"1Y"``, ``"2W"`` or ``"10D"``."""

        if not isinstance(text, str) or len(text) < 2 or not text[:-1].isdigit():
            raise ConventionInputError(
                f"{text!r} is not a tenor such as '3M', '1Y', '2W' or '10D'."
            )
        try:
            unit = TenorUnit(text[-1].upper())
        except ValueError:
            raise ConventionInputError(
                f"{text!r} ends in {text[-1]!r}, not D, W, M or Y."
            ) from None
        return cls(int(text[:-1]), unit)

    @property
    def label(self) -> str:
        return f"{self.count}{self.unit.value}"


def _require_calendars(calendars: Sequence[TradingDayCalendar], what: str) -> None:
    if not calendars:
        raise ConventionInputError(f"{what} needs at least one calendar; none is assumed open.")
    for calendar in calendars:
        if not callable(getattr(calendar, "is_trading_day", None)):
            raise ConventionInputError(
                f"{calendar!r} is not a calendar: it states no is_trading_day."
            )


def is_business_day(day: date, calendars: Sequence[TradingDayCalendar]) -> bool:
    """Whether every calendar in ``calendars`` is open on ``day``."""

    _require_calendars(calendars, "A business day")
    return all(calendar.is_trading_day(day) for calendar in calendars)


def _step(day: date, direction: int, calendars: Sequence[TradingDayCalendar]) -> date:
    """The first business day strictly after (``+1``) or before (``-1``) ``day``."""

    cursor = day
    for _ in range(MAX_SETTLEMENT_SEARCH_DAYS):
        cursor += timedelta(days=direction)
        if all(calendar.is_trading_day(cursor) for calendar in calendars):
            return cursor
    raise ConventionViolationError(
        f"No business day within {MAX_SETTLEMENT_SEARCH_DAYS} days of {day.isoformat()}: "
        "a calendar is almost certainly mis-declared."
    )


def adjust(
    day: date, convention: BusinessDayConvention, calendars: Sequence[TradingDayCalendar]
) -> date:
    """``day`` moved to a business day of every calendar by ``convention``.

    Raises:
        ConventionInputError: If ``convention`` is not one, or no calendar is given.
        ConventionViolationError: If no business day lies within the search span.
    """

    if not isinstance(convention, BusinessDayConvention):
        raise ConventionInputError(f"{convention!r} is not a BusinessDayConvention.")
    _require_calendars(calendars, "Adjusting a date")
    if convention is BusinessDayConvention.UNADJUSTED or is_business_day(day, calendars):
        return day
    if convention is BusinessDayConvention.PRECEDING:
        return _step(day, -1, calendars)
    following = _step(day, 1, calendars)
    if convention is BusinessDayConvention.MODIFIED_FOLLOWING and following.month != day.month:
        return _step(day, -1, calendars)
    return following


def add_business_days(day: date, count: int, calendars: Sequence[TradingDayCalendar]) -> date:
    """The ``count``-th business day after ``day`` (``day`` itself when ``count`` is zero)."""

    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ConventionInputError(f"A business-day count is {count!r}; a non-negative integer.")
    _require_calendars(calendars, "Counting business days")
    cursor = day
    for _ in range(count):
        cursor = _step(cursor, 1, calendars)
    return cursor


def _last_business_day(year: int, month: int, calendars: Sequence[TradingDayCalendar]) -> date:
    last = date(year, month, gregorian.monthrange(year, month)[1])
    return last if is_business_day(last, calendars) else _step(last, -1, calendars)


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, gregorian.monthrange(year, month)[1]))


def add_tenor(
    start: date,
    tenor: Tenor,
    convention: BusinessDayConvention,
    calendars: Sequence[TradingDayCalendar],
    *,
    end_of_month: bool,
) -> date:
    """``start`` plus ``tenor``, on a business day by ``convention``.

    Months and years land on the same day of the month, or the month's last day
    when it has fewer. Under ``end_of_month``, a start on its month's last
    business day lands on the last business day of the month the tenor reaches:
    the end-of-month rule, stated, never assumed.

    Raises:
        ConventionInputError: If ``tenor`` is not a :class:`Tenor`, or the rule
            or calendars are malformed.
    """

    if not isinstance(tenor, Tenor):
        raise ConventionInputError(f"{tenor!r} is not a Tenor.")
    if not isinstance(end_of_month, bool):
        raise ConventionInputError(f"end_of_month must be True or False, got {end_of_month!r}.")
    _require_calendars(calendars, "Adding a tenor")
    if tenor.unit is TenorUnit.DAYS:
        return adjust(start + timedelta(days=tenor.count), convention, calendars)
    if tenor.unit is TenorUnit.WEEKS:
        return adjust(start + timedelta(weeks=tenor.count), convention, calendars)
    months = tenor.count * (12 if tenor.unit is TenorUnit.YEARS else 1)
    target = _add_months(start, months)
    if end_of_month and start == _last_business_day(start.year, start.month, calendars):
        return _last_business_day(target.year, target.month, calendars)
    return adjust(target, convention, calendars)


@dataclass(frozen=True, slots=True)
class ValueDateRule:
    """When an FX deal struck on a date settles: its spot lag and the calendars it is counted in.

    Attributes:
        spot_lag: Business days from the trade to spot: two for most pairs, one
            for some. Zero is a same-day deal.
        counted: The calendars a counted day must be open in: both currencies'
            for a cross, the non-dollar currency's for a common US-dollar pair.
        settles: The calendars the value date must be open in: both
            currencies', and the US calendar where the pair's rule requires it.

    Raises:
        ConventionInputError: If the lag is not a non-negative integer, or a
            calendar list is empty or holds something that is not a calendar.
    """

    spot_lag: int
    counted: tuple[TradingDayCalendar, ...]
    settles: tuple[TradingDayCalendar, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.spot_lag, bool)
            or not isinstance(self.spot_lag, int)
            or self.spot_lag < 0
        ):
            raise ConventionInputError(
                f"spot_lag is {self.spot_lag!r}; a non-negative number of business days."
            )
        object.__setattr__(self, "counted", tuple(self.counted))
        object.__setattr__(self, "settles", tuple(self.settles))
        _require_calendars(self.counted, "A value-date rule's counted days")
        _require_calendars(self.settles, "A value-date rule's value date")


def spot_value_date(trade_day: date, rule: ValueDateRule) -> date:
    """The spot value date of a deal struck on ``trade_day``, by ``rule``.

    ``spot_lag`` days are counted, each a business day of every ``counted``
    calendar; the result rolls forward to the first day every ``settles``
    calendar is open.
    """

    if not isinstance(rule, ValueDateRule):
        raise ConventionInputError(f"{rule!r} is not a ValueDateRule.")
    counted = add_business_days(trade_day, rule.spot_lag, rule.counted)
    if is_business_day(counted, rule.settles):
        return counted
    return _step(counted, 1, rule.settles)


def forward_value_date(
    trade_day: date,
    rule: ValueDateRule,
    tenor: Tenor,
    convention: BusinessDayConvention,
    *,
    end_of_month: bool,
) -> date:
    """A forward's value date: ``tenor`` from spot, on a day every ``settles`` calendar is open.

    The tenor is added to the spot value date under ``convention`` and the
    end-of-month rule, both stated, against the calendars the deal settles in.
    """

    spot = spot_value_date(trade_day, rule)
    return add_tenor(spot, tenor, convention, rule.settles, end_of_month=end_of_month)
