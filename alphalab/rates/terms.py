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
* :class:`FloaterTerms`: a floating-rate note -- a bond whose coupons are a
  floating leg's -- and its redemption.
* :class:`LinkerTerms`: an inflation-linked bond -- real coupons and principal
  scaled by an index's ratio to its base -- read from the index's monthly
  prints by a stated lag and :class:`Indexation`, floored at par or not.
* :class:`RepoTerms`: a repurchase agreement -- cash lent or borrowed from a
  start to an end at a rate, against collateral less a haircut.

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
from enum import Enum
from typing import Any, Final

from alphalab.conventions.business_days import BusinessDayConvention, Tenor
from alphalab.conventions.daycount import DayCount
from alphalab.conventions.exceptions import ConventionInputError
from alphalab.conventions.settlement import TradingDayCalendar
from alphalab.rates._checks import require_basis, require_currency, require_day, require_line
from alphalab.rates.exceptions import RatesInputError
from alphalab.rates.schedule import tenor_months

__all__ = [
    "RATES_TERMS_KINDS",
    "FixedLeg",
    "FloaterTerms",
    "FloatingLeg",
    "FraTerms",
    "Indexation",
    "LegSchedule",
    "LinkerTerms",
    "RatesTerms",
    "RepoTerms",
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
        tenor_months(self.length)
        if not isinstance(self.fixed, FixedLeg) or not isinstance(self.floating, FloatingLeg):
            raise RatesInputError("A swap states a FixedLeg and a FloatingLeg.")
        for leg in (self.fixed, self.floating):
            _whole(self.length, leg.schedule, "A swap")


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


class Indexation(Enum):
    """How a linker's reference index is read for a date, from monthly prints (ADR-0065 s. 4)."""

    #: The print of the month ``lag_months`` before the date's month.
    MONTHLY = "monthly"
    #: Interpolated by the day of the month between the prints of the months
    #: ``lag_months`` and ``lag_months - 1`` before the date's: on day ``d`` of a
    #: month of ``D`` days, ``I(m - lag) + (d - 1) / D * (I(m - lag + 1) - I(m - lag))``.
    #: The method of US TIPS and of index-linked gilts issued since 2005.
    DAILY_INTERPOLATED = "daily_interpolated"


@dataclass(frozen=True, slots=True)
class FloaterTerms:
    """A floating-rate note's coupon terms: a bond whose coupons are fixings plus a spread.

    One unit held receives each period's ``notional * (fixing + spread) *
    tau``, its fixing as its leg states, and ``redemption`` at maturity: the
    end of ``length`` from ``start``.

    Raises:
        RatesInputError: If a field is malformed, or ``length`` is not a whole
            number of the leg's steps.
    """

    currency: str
    start: date
    length: Tenor
    leg: FloatingLeg
    redemption: Decimal

    def __post_init__(self) -> None:
        require_currency(self.currency, "A floater's currency")
        require_day(self.start, "A floater's start")
        if not isinstance(self.leg, FloatingLeg):
            raise RatesInputError(f"A floater's leg is a FloatingLeg, got {self.leg!r}.")
        _whole(self.length, self.leg.schedule, "A floater")
        _decimal(self.redemption, "A floater's redemption", positive=True)


@dataclass(frozen=True, slots=True)
class LinkerTerms:
    """An inflation-linked bond's coupon terms: real coupons scaled by an index ratio.

    Each coupon pays ``notional * rate * tau * I(t) / base``, where ``I(t)`` is
    the reference index for its payment date by ``indexation`` and
    ``lag_months``, read from the index's monthly prints. At maturity the
    principal pays ``notional * I(T) / base``, or the notional itself when
    ``floored`` and the ratio is below one.

    Attributes:
        leg: The real coupon: its notional, rate, day count and schedule.
        index: The index's name, as its supplied prints name it.
        lag_months: How many months before a date its reference print is.
        indexation: How a date's reference index is read from the prints.
        base: The reference index the bond was issued against.
        floored: Whether the principal is paid at no less than par.

    Raises:
        RatesInputError: If a field is malformed, or ``length`` is not a whole
            number of the leg's steps.
    """

    currency: str
    start: date
    length: Tenor
    leg: FixedLeg
    index: str
    lag_months: int
    indexation: Indexation
    base: Decimal
    floored: bool

    def __post_init__(self) -> None:
        require_currency(self.currency, "A linker's currency")
        require_day(self.start, "A linker's start")
        if not isinstance(self.leg, FixedLeg):
            raise RatesInputError(f"A linker's leg is a FixedLeg, got {self.leg!r}.")
        _whole(self.length, self.leg.schedule, "A linker")
        require_line(self.index, "A linker's index")
        if (
            isinstance(self.lag_months, bool)
            or not isinstance(self.lag_months, int)
            or self.lag_months < 1
        ):
            raise RatesInputError(
                f"A linker's lag is {self.lag_months!r}; a positive number of months, since an "
                "index is printed after the month it measures."
            )
        if not isinstance(self.indexation, Indexation):
            raise RatesInputError(f"indexation is an Indexation, got {self.indexation!r}.")
        _decimal(self.base, "A linker's base index", positive=True)
        if not isinstance(self.floored, bool):
            raise RatesInputError(f"floored is True or False, got {self.floored!r}.")


@dataclass(frozen=True, slots=True)
class RepoTerms:
    """A repurchase agreement's terms: cash lent from its start to its end against collateral.

    One unit held is one unit of cash lent at ``rate`` -- a reverse repo -- and
    repaid at ``end`` with ``rate * tau`` of interest under ``day_count``; a
    negative quantity is cash borrowed, a repo. ``collateral`` names the
    instrument that secures it, and ``haircut`` the fraction of its value that
    lends nothing: cash of ``q`` needs collateral worth ``q / (1 - haircut)``.

    Raises:
        RatesInputError: If a field is malformed, the period is not one, or the
            haircut is not in ``[0, 1)``.
    """

    currency: str
    collateral: str
    rate: Decimal
    start: date
    end: date
    day_count: DayCount
    haircut: Decimal

    def __post_init__(self) -> None:
        require_currency(self.currency, "A repo's currency")
        require_line(self.collateral, "A repo's collateral")
        _decimal(self.rate, "A repo's rate")
        require_day(self.start, "A repo's start")
        require_day(self.end, "A repo's end")
        if self.end <= self.start:
            raise RatesInputError(f"A repo runs from {self.start} to a later date, not {self.end}.")
        require_basis(self.day_count, "A repo's day count", terms_free=True)
        _decimal(self.haircut, "A repo's haircut")
        if not 0 <= self.haircut < 1:
            raise RatesInputError(
                f"A repo's haircut is {self.haircut}; a fraction of the collateral's value, at "
                "least zero and below one."
            )


def _whole(length: Tenor, schedule: LegSchedule, what: str) -> None:
    months = tenor_months(length)  # refuses a length that is not a Tenor in months or years
    if months % tenor_months(schedule.step):
        raise RatesInputError(
            f"{what} of {length.label} is not a whole number of {schedule.step.label} periods; "
            "a stub is refused, not placed."
        )


#: A rates instrument's terms.
RatesTerms = SwapTerms | FraTerms | FloaterTerms | LinkerTerms | RepoTerms


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


def _fixed_out(leg: FixedLeg) -> dict[str, Any]:
    return {
        "notional": str(leg.notional),
        "rate": str(leg.rate),
        "day_count": leg.day_count.name,
        "schedule": _schedule_out(leg.schedule),
    }


def _fixed_in(value: Mapping[str, Any]) -> FixedLeg:
    return FixedLeg(
        Decimal(value["notional"]),
        Decimal(value["rate"]),
        DayCount[value["day_count"]],
        _schedule_in(value["schedule"]),
    )


def _floating_out(leg: FloatingLeg) -> dict[str, Any]:
    return {
        "notional": str(leg.notional),
        "index": leg.index,
        "spread": str(leg.spread),
        "day_count": leg.day_count.name,
        "schedule": _schedule_out(leg.schedule),
        "reset_lag": leg.reset_lag,
        "fixing_source": leg.fixing_source,
    }


def _floating_in(value: Mapping[str, Any]) -> FloatingLeg:
    return FloatingLeg(
        Decimal(value["notional"]),
        str(value["index"]),
        Decimal(value["spread"]),
        DayCount[value["day_count"]],
        _schedule_in(value["schedule"]),
        value["reset_lag"],
        str(value["fixing_source"]),
    )


def rates_terms_to_primitives(terms: RatesTerms) -> dict[str, Any]:
    """The terms as plain primitives: enum members by name, amounts and dates as text."""

    if isinstance(terms, SwapTerms):
        return {
            "kind": "swap",
            "currency": terms.currency,
            "start": terms.start.isoformat(),
            "length": terms.length.label,
            "fixed": _fixed_out(terms.fixed),
            "floating": _floating_out(terms.floating),
        }
    if isinstance(terms, FloaterTerms):
        return {
            "kind": "floater",
            "currency": terms.currency,
            "start": terms.start.isoformat(),
            "length": terms.length.label,
            "leg": _floating_out(terms.leg),
            "redemption": str(terms.redemption),
        }
    if isinstance(terms, LinkerTerms):
        return {
            "kind": "linker",
            "currency": terms.currency,
            "start": terms.start.isoformat(),
            "length": terms.length.label,
            "leg": _fixed_out(terms.leg),
            "index": terms.index,
            "lag_months": terms.lag_months,
            "indexation": terms.indexation.name,
            "base": str(terms.base),
            "floored": terms.floored,
        }
    if isinstance(terms, RepoTerms):
        return {
            "kind": "repo",
            "currency": terms.currency,
            "collateral": terms.collateral,
            "rate": str(terms.rate),
            "start": terms.start.isoformat(),
            "end": terms.end.isoformat(),
            "day_count": terms.day_count.name,
            "haircut": str(terms.haircut),
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


#: The kinds :func:`rates_terms_to_primitives` writes.
RATES_TERMS_KINDS: Final = ("swap", "fra", "floater", "linker", "repo")


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
            return SwapTerms(
                currency=str(value["currency"]),
                start=date.fromisoformat(value["start"]),
                length=Tenor.parse(str(value["length"])),
                fixed=_fixed_in(value["fixed"]),
                floating=_floating_in(value["floating"]),
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
        if kind == "floater":
            return FloaterTerms(
                currency=str(value["currency"]),
                start=date.fromisoformat(value["start"]),
                length=Tenor.parse(str(value["length"])),
                leg=_floating_in(value["leg"]),
                redemption=Decimal(value["redemption"]),
            )
        if kind == "linker":
            return LinkerTerms(
                currency=str(value["currency"]),
                start=date.fromisoformat(value["start"]),
                length=Tenor.parse(str(value["length"])),
                leg=_fixed_in(value["leg"]),
                index=str(value["index"]),
                lag_months=value["lag_months"],
                indexation=Indexation[value["indexation"]],
                base=Decimal(value["base"]),
                floored=value["floored"],
            )
        if kind == "repo":
            return RepoTerms(
                currency=str(value["currency"]),
                collateral=str(value["collateral"]),
                rate=Decimal(value["rate"]),
                start=date.fromisoformat(value["start"]),
                end=date.fromisoformat(value["end"]),
                day_count=DayCount[value["day_count"]],
                haircut=Decimal(value["haircut"]),
            )
    except (KeyError, TypeError, ValueError, InvalidOperation, ConventionInputError) as exc:
        raise RatesInputError(f"These are not rates terms: {exc}.") from exc
    raise RatesInputError(
        f"Rates terms are of kind {value.get('kind')!r}, not one of {', '.join(RATES_TERMS_KINDS)}."
    )
