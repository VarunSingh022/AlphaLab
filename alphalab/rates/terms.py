"""A rates instrument's declared terms (v4.3, ADR-0065 section 4).

Each instrument states every leg's notional, currency and day count; its step,
business-day convention and calendars; and, for a floating leg, its index,
reset lag and fixing source. None has a default (AC-12): a swap whose fixed
leg's day count was assumed would accrue a different coupon in every market.

* :class:`SwapTerms`: a single-currency interest rate swap. One unit held
  receives its fixed leg and pays its floating leg, each on its leg's notional
  per unit; a negative quantity is the payer's side.
* :class:`FraTerms`: a forward rate agreement. One unit held receives the
  period's fixing and pays the contract rate on its notional, settled at the
  period's start, discounted at the fixing.

Calendars are named by ``calendar_id``. Terms ride on an instrument's record
and are persisted with it, so a calendar is supplied where the terms are used
-- by the run, or by the caller of a valuation -- and one they name that nobody
supplies is refused there.

Rates and notionals are ``Decimal``: a coupon is booked from them exactly, and
the shortest ``repr`` of a period's year fraction -- a correctly rounded value
on every host -- is the only float a booked amount reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from alphalab.conventions.business_days import BusinessDayConvention, Tenor
from alphalab.conventions.daycount import DayCount
from alphalab.conventions.exceptions import ConventionInputError
from alphalab.conventions.settlement import TradingDayCalendar
from alphalab.rates._checks import require_basis, require_currency, require_day, require_line
from alphalab.rates.exceptions import RatesInputError
from alphalab.rates.schedule import tenor_months

__all__ = [
    "FixedLeg",
    "FloatingLeg",
    "FraTerms",
    "LegSchedule",
    "RatesTerms",
    "SwapTerms",
    "rates_terms_from_primitives",
    "rates_terms_to_primitives",
    "resolve_calendars",
]


def _decimal(value: object, what: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise RatesInputError(f"{what} is a finite Decimal, got {value!r}.")
    if positive and value <= 0:
        raise RatesInputError(f"{what} is positive, got {value}.")
    return value


def _lag(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RatesInputError(f"{what} is {value!r}; a non-negative number of business days.")
    return value


def _calendar_ids(value: object, what: str) -> tuple[str, ...]:
    ids = tuple(value) if isinstance(value, tuple | list) else ()
    if not ids or any(not isinstance(i, str) or not i.strip() for i in ids):
        raise RatesInputError(
            f"{what} names at least one calendar by its calendar_id, got {value!r}: no calendar "
            "is assumed open."
        )
    if len(set(ids)) != len(ids):
        raise RatesInputError(f"{what} names a calendar twice: {list(ids)}.")
    return ids


def resolve_calendars(
    ids: Sequence[str], calendars: Mapping[str, TradingDayCalendar], what: str
) -> tuple[TradingDayCalendar, ...]:
    """The calendars ``ids`` name, each supplied in ``calendars``.

    Raises:
        RatesInputError: If an id is not supplied.
    """

    missing = [i for i in ids if i not in calendars]
    if missing:
        raise RatesInputError(
            f"{what} names {', '.join(missing)}, and no calendar of that id is supplied: a "
            "date it would place cannot be."
        )
    return tuple(calendars[i] for i in ids)


@dataclass(frozen=True, slots=True)
class LegSchedule:
    """How a leg's dates are placed: a whole number of steps from the instrument's start.

    Attributes:
        step: The period, in months or years.
        convention: How a date off a business day moves.
        calendars: The calendars a date must be a business day of, by id.
        end_of_month: Whether a start on its month's last business day holds
            each date to its month's last business day.
    """

    step: Tenor
    convention: BusinessDayConvention
    calendars: tuple[str, ...]
    end_of_month: bool

    def __post_init__(self) -> None:
        tenor_months(self.step)
        if not isinstance(self.convention, BusinessDayConvention):
            raise RatesInputError(
                f"A leg's convention is a BusinessDayConvention, got {self.convention!r}."
            )
        object.__setattr__(self, "calendars", _calendar_ids(self.calendars, "A leg"))
        if not isinstance(self.end_of_month, bool):
            raise RatesInputError(f"end_of_month is True or False, got {self.end_of_month!r}.")


@dataclass(frozen=True, slots=True)
class FixedLeg:
    """A leg paying a fixed rate on its notional, accrued under its basis.

    Attributes:
        notional: Per unit held.
        rate: A year's rate, as a fraction: ``Decimal("0.0425")`` is 4.25%.
        day_count: How each period accrues. A basis that needs a coupon
            period's terms reads each period's own.
        schedule: Its dates.
    """

    notional: Decimal
    rate: Decimal
    day_count: DayCount
    schedule: LegSchedule

    def __post_init__(self) -> None:
        _decimal(self.notional, "A fixed leg's notional", positive=True)
        _decimal(self.rate, "A fixed leg's rate")
        require_basis(self.day_count, "A fixed leg's day count", terms_free=False)
        if not isinstance(self.schedule, LegSchedule):
            raise RatesInputError(f"A leg's schedule is a LegSchedule, got {self.schedule!r}.")


@dataclass(frozen=True, slots=True)
class FloatingLeg:
    """A leg paying an index's fixing plus a spread on its notional.

    Each period's rate is the index's fixing on its fixing date, ``reset_lag``
    business days before the period's start in the schedule's calendars, from
    ``fixing_source``. A fixing a payment needs and nobody supplies is refused
    where it is needed, never assumed.

    Attributes:
        notional: Per unit held.
        index: The index's name, as the supplied fixings name it:
            ``"USD-SOFR"``, ``"EUR-EURIBOR-6M"``.
        spread: Added to each fixing, as a fraction a year.
        day_count: How each period accrues.
        schedule: Its dates.
        reset_lag: Business days from a period's fixing to its start.
        fixing_source: Where its fixings are published, as the supplied
            fixings name it.
    """

    notional: Decimal
    index: str
    spread: Decimal
    day_count: DayCount
    schedule: LegSchedule
    reset_lag: int
    fixing_source: str

    def __post_init__(self) -> None:
        _decimal(self.notional, "A floating leg's notional", positive=True)
        require_line(self.index, "A floating leg's index")
        _decimal(self.spread, "A floating leg's spread")
        require_basis(self.day_count, "A floating leg's day count", terms_free=False)
        if not isinstance(self.schedule, LegSchedule):
            raise RatesInputError(f"A leg's schedule is a LegSchedule, got {self.schedule!r}.")
        _lag(self.reset_lag, "A floating leg's reset lag")
        require_line(self.fixing_source, "A floating leg's fixing source")


@dataclass(frozen=True, slots=True)
class SwapTerms:
    """A single-currency interest rate swap's terms (ADR-0065 section 4).

    One unit held receives the fixed leg and pays the floating leg. Both run
    from ``start`` for ``length``, each by its own schedule.

    Raises:
        RatesInputError: If a field is malformed, or ``length`` is not a whole
            number of either leg's steps.
    """

    currency: str
    start: date
    length: Tenor
    fixed: FixedLeg
    floating: FloatingLeg

    def __post_init__(self) -> None:
        require_currency(self.currency, "A swap's currency")
        require_day(self.start, "A swap's start")
        months = tenor_months(self.length)
        if not isinstance(self.fixed, FixedLeg) or not isinstance(self.floating, FloatingLeg):
            raise RatesInputError("A swap states a FixedLeg and a FloatingLeg.")
        for leg in (self.fixed, self.floating):
            if months % tenor_months(leg.schedule.step):
                raise RatesInputError(
                    f"A swap of {self.length.label} is not a whole number of "
                    f"{leg.schedule.step.label} periods; a stub is refused, not placed."
                )


@dataclass(frozen=True, slots=True)
class FraTerms:
    """A forward rate agreement's terms (ADR-0065 section 4).

    One unit held receives the period's fixing and pays ``rate``, each on
    ``notional`` accrued over ``[start, end]`` under ``day_count``. It settles
    at ``start``: ``notional * (fixing - rate) * tau / (1 + fixing * tau)``,
    the difference discounted from ``end`` at the fixing itself, as the
    market's FRAs settle.

    Raises:
        RatesInputError: If a field is malformed or the period is not one.
    """

    currency: str
    notional: Decimal
    rate: Decimal
    start: date
    end: date
    day_count: DayCount
    index: str
    fixing_lag: int
    calendars: tuple[str, ...]
    fixing_source: str

    def __post_init__(self) -> None:
        require_currency(self.currency, "An FRA's currency")
        _decimal(self.notional, "An FRA's notional", positive=True)
        _decimal(self.rate, "An FRA's rate")
        require_day(self.start, "An FRA's start")
        require_day(self.end, "An FRA's end")
        if self.end <= self.start:
            raise RatesInputError(
                f"An FRA's period runs from {self.start} to a later date, not {self.end}."
            )
        require_basis(self.day_count, "An FRA's day count", terms_free=True)
        require_line(self.index, "An FRA's index")
        _lag(self.fixing_lag, "An FRA's fixing lag")
        object.__setattr__(self, "calendars", _calendar_ids(self.calendars, "An FRA"))
        require_line(self.fixing_source, "An FRA's fixing source")


#: A rates instrument's terms.
RatesTerms = SwapTerms | FraTerms


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _schedule_out(schedule: LegSchedule) -> dict[str, Any]:
    return {
        "step": schedule.step.label,
        "convention": schedule.convention.name,
        "calendars": list(schedule.calendars),
        "end_of_month": schedule.end_of_month,
    }


def _schedule_in(value: Mapping[str, Any]) -> LegSchedule:
    return LegSchedule(
        Tenor.parse(str(value["step"])),
        BusinessDayConvention[value["convention"]],
        tuple(str(i) for i in value["calendars"]),
        value["end_of_month"],
    )


def rates_terms_to_primitives(terms: RatesTerms) -> dict[str, Any]:
    """The terms as plain primitives: enum members by name, amounts and dates as text."""

    if isinstance(terms, SwapTerms):
        fixed, floating = terms.fixed, terms.floating
        return {
            "kind": "swap",
            "currency": terms.currency,
            "start": terms.start.isoformat(),
            "length": terms.length.label,
            "fixed": {
                "notional": str(fixed.notional),
                "rate": str(fixed.rate),
                "day_count": fixed.day_count.name,
                "schedule": _schedule_out(fixed.schedule),
            },
            "floating": {
                "notional": str(floating.notional),
                "index": floating.index,
                "spread": str(floating.spread),
                "day_count": floating.day_count.name,
                "schedule": _schedule_out(floating.schedule),
                "reset_lag": floating.reset_lag,
                "fixing_source": floating.fixing_source,
            },
        }
    return {
        "kind": "fra",
        "currency": terms.currency,
        "notional": str(terms.notional),
        "rate": str(terms.rate),
        "start": terms.start.isoformat(),
        "end": terms.end.isoformat(),
        "day_count": terms.day_count.name,
        "index": terms.index,
        "fixing_lag": terms.fixing_lag,
        "calendars": list(terms.calendars),
        "fixing_source": terms.fixing_source,
    }


def rates_terms_from_primitives(value: Mapping[str, Any]) -> RatesTerms:
    """Read terms :func:`rates_terms_to_primitives` wrote.

    Raises:
        RatesInputError: If the value is not one of the shapes, names an enum
            member this build does not have, or states terms that do not
            validate.
    """

    try:
        kind = value["kind"]
        if kind == "swap":
            fixed, floating = value["fixed"], value["floating"]
            return SwapTerms(
                currency=str(value["currency"]),
                start=date.fromisoformat(value["start"]),
                length=Tenor.parse(str(value["length"])),
                fixed=FixedLeg(
                    Decimal(fixed["notional"]),
                    Decimal(fixed["rate"]),
                    DayCount[fixed["day_count"]],
                    _schedule_in(fixed["schedule"]),
                ),
                floating=FloatingLeg(
                    Decimal(floating["notional"]),
                    str(floating["index"]),
                    Decimal(floating["spread"]),
                    DayCount[floating["day_count"]],
                    _schedule_in(floating["schedule"]),
                    floating["reset_lag"],
                    str(floating["fixing_source"]),
                ),
            )
        if kind == "fra":
            return FraTerms(
                currency=str(value["currency"]),
                notional=Decimal(value["notional"]),
                rate=Decimal(value["rate"]),
                start=date.fromisoformat(value["start"]),
                end=date.fromisoformat(value["end"]),
                day_count=DayCount[value["day_count"]],
                index=str(value["index"]),
                fixing_lag=value["fixing_lag"],
                calendars=tuple(str(i) for i in value["calendars"]),
                fixing_source=str(value["fixing_source"]),
            )
    except (KeyError, TypeError, ValueError, InvalidOperation, ConventionInputError) as exc:
        raise RatesInputError(f"These are not rates terms: {exc}.") from exc
    raise RatesInputError(f"Rates terms are of kind {value.get('kind')!r}, not swap or fra.")
