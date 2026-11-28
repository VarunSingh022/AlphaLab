"""A leg's dates, by its declared step, convention and calendars (v4.3, ADR-0065 section 4).

A regular schedule runs from its start for a whole number of steps. Its k-th
date is the start plus k steps -- each counted from the start, never from the
date before it, so a month-end start does not drift -- moved to a business day
of every calendar by the leg's convention, under its end-of-month rule
(:func:`alphalab.conventions.business_days.add_tenor`). A length that is not a
whole number of steps would need a stub, and where a stub goes is the
instrument's choice, so it is refused rather than placed.

The dates accrue as they are adjusted: each period runs from one adjusted date
to the next, and pays at its end.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from itertools import pairwise

from alphalab.conventions.business_days import (
    BusinessDayConvention,
    Tenor,
    TenorUnit,
    add_tenor,
    adjust,
)
from alphalab.conventions.exceptions import ConventionError
from alphalab.conventions.settlement import TradingDayCalendar
from alphalab.rates.exceptions import RatesInputError

__all__ = ["regular_schedule", "tenor_months"]


def tenor_months(tenor: Tenor) -> int:
    """A tenor in months; one in days or weeks is refused, since it is no whole month.

    Raises:
        RatesInputError: If ``tenor`` is not a :class:`Tenor` in months or years.
    """

    if not isinstance(tenor, Tenor):
        raise RatesInputError(f"{tenor!r} is not a Tenor.")
    if tenor.unit is TenorUnit.MONTHS:
        return tenor.count
    if tenor.unit is TenorUnit.YEARS:
        return 12 * tenor.count
    raise RatesInputError(
        f"A schedule steps in months or years; {tenor.label} is in "
        f"{tenor.unit.name.lower()}, which no whole number of months is."
    )


def regular_schedule(
    start: date,
    length: Tenor,
    step: Tenor,
    convention: BusinessDayConvention,
    calendars: Sequence[TradingDayCalendar],
    *,
    end_of_month: bool,
) -> tuple[date, ...]:
    """The adjusted dates of a regular schedule: its start, then each step's end.

    Args:
        start: The first accrual date, before adjustment.
        length: How long the schedule runs, a whole number of ``step``.
        step: The period, in months or years.
        convention: How a date off a business day moves.
        calendars: The calendars a date must be a business day of, at least one.
        end_of_month: Whether a start on its month's last business day holds
            each date to its month's last business day.

    Raises:
        RatesInputError: If ``length`` is not a whole number of ``step``, a
            tenor is in days or weeks, or a convention or calendar is
            malformed.
    """

    months, every = tenor_months(length), tenor_months(step)
    if months % every:
        raise RatesInputError(
            f"{length.label} is not a whole number of {step.label} periods; a stub would have "
            "to be placed, and where is the instrument's choice, so it is refused."
        )
    try:
        dates = [adjust(start, convention, calendars)]
        dates += [
            add_tenor(
                start,
                Tenor(every * k, TenorUnit.MONTHS),
                convention,
                calendars,
                end_of_month=end_of_month,
            )
            for k in range(1, months // every + 1)
        ]
    except ConventionError as exc:
        raise RatesInputError(str(exc)) from exc
    for earlier, later in pairwise(dates):
        if later <= earlier:
            raise RatesInputError(
                f"The schedule's dates {earlier} and {later} are not in order after adjustment: "
                "its step is shorter than a calendar's closures."
            )
    return tuple(dates)
