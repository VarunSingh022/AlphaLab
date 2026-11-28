"""Swaps and FRAs: their periods from declared terms, and their values off curves (ADR-0065).

A leg's periods are its regular schedule from the instrument's start
(:func:`~alphalab.rates.schedule.regular_schedule`), each paying at its end. A
floating period's fixing date is the leg's reset lag in business days before
the period's start, in the leg's calendars. Every calendar is the caller's,
found by the id the terms name.

What is known and what is projected
-----------------------------------
A valuation is as of the discount curve's anchor. A period paying on or before
the anchor has been paid, and is not valued. A floating period fixed before
the anchor has a known rate: its fixing is the caller's to supply, and one not
supplied is refused, never projected. A period fixing on or after the anchor is
projected: the simple forward over the period itself, read off the projection
curve, as a bootstrap's quoted swap reads it.

Values, to one unit held
------------------------
Writing ``D`` for the discount curve and ``tau`` for a period's year fraction
under its leg's basis:

* a fixed leg: ``sum(notional * rate * tau_i * D(t_i))``;
* a floating leg: ``sum(notional * (rate_j + spread) * tau_j * D(t_j))``, each
  ``rate_j`` fixed or projected;
* a swap: its fixed leg less its floating leg. Its annuity is
  ``sum(notional * tau_i * D(t_i))`` over the fixed periods still to pay, and
  its par rate is the fixed rate that values it at zero: the floating leg over
  the annuity;
* an FRA, until it settles at its start: ``notional * (F - rate) * tau *
  D(end)``, ``F`` its fixing or its projected forward.

What a position has accrued is stated by its terms (:func:`swap_accrued`): each
leg's notional times its rate times the year fraction from the current period's
start under its basis, a floating period's at its fixing plus its spread.

Identity
--------
A :class:`Valuation`'s ``valuation_id`` is SHA-256 over its canonical key: the
terms, the periods as the calendars placed them, the curves' identities, every
fixing read, and the date. Its values are class 3 -- the curves are read
through ``exp`` -- and are recorded beside the identity, never in it.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Final

from alphalab.common.arithmetic import in_accounting_context
from alphalab.conventions.business_days import Tenor, subtract_business_days
from alphalab.conventions.daycount import DayCountTerms, required_terms
from alphalab.conventions.exceptions import ConventionError
from alphalab.conventions.settlement import TradingDayCalendar
from alphalab.rates._checks import accrual, period_fraction
from alphalab.rates.curve import DiscountCurve
from alphalab.rates.exceptions import RatesInputError
from alphalab.rates.schedule import regular_schedule, tenor_months
from alphalab.rates.terms import (
    FixedLeg,
    FloatingLeg,
    FraTerms,
    RatesTerms,
    SwapTerms,
    rates_terms_to_primitives,
    resolve_calendars,
)

__all__ = [
    "VALUATION_KEY_SCHEME",
    "CouponPeriod",
    "Fixings",
    "Valuation",
    "fra_settlement",
    "leg_periods",
    "swap_accrued",
    "value_fra",
    "value_swap",
]

#: The scheme of a valuation's canonical key; a change to the key is a new scheme.
VALUATION_KEY_SCHEME: Final = "alphalab.rates.valuation.v1"

#: Fixings by index name and fixing date: the rate each was fixed at.
Fixings = Mapping[tuple[str, date], Decimal]


@dataclass(frozen=True, slots=True)
class CouponPeriod:
    """One period of a leg: its dates, its year fraction, and a floating period's fixing date.

    Attributes:
        start: Its first accrual date.
        end: Its last, and the day it pays.
        accrual: Its year fraction under the leg's basis.
        fixing: The day its rate is fixed; ``None`` for a fixed leg's.
    """

    start: date
    end: date
    accrual: float
    fixing: date | None


def leg_periods(
    leg: FixedLeg | FloatingLeg,
    start: date,
    length: Tenor,
    calendars: Mapping[str, TradingDayCalendar],
) -> tuple[CouponPeriod, ...]:
    """A leg's periods from ``start`` for ``length``, placed in the calendars it names.

    Raises:
        RatesInputError: If a calendar it names is not supplied, the length is
            not a whole number of its steps, or its basis cannot measure a
            period.
    """

    schedule = leg.schedule
    found = resolve_calendars(schedule.calendars, calendars, "A leg")
    dates = regular_schedule(
        start,
        length,
        schedule.step,
        schedule.convention,
        found,
        end_of_month=schedule.end_of_month,
    )
    months = tenor_months(schedule.step)
    frequency = 12 // months if 12 % months == 0 else None
    periods = []
    for first, last in pairwise(dates):
        tau = period_fraction(
            first, last, leg.day_count, frequency=frequency, termination=dates[-1]
        )
        fixing = None
        if isinstance(leg, FloatingLeg):
            try:
                fixing = subtract_business_days(first, leg.reset_lag, found)
            except ConventionError as exc:
                raise RatesInputError(str(exc)) from exc
        periods.append(CouponPeriod(first, last, tau, fixing))
    return tuple(periods)


@dataclass(frozen=True, slots=True)
class Valuation:
    """What an instrument is worth to one unit held, as of a date, and the problem it answers.

    Attributes:
        as_of: The discount curve's anchor.
        currency: The instrument's currency.
        value: Its value to one unit held.
        legs: Each leg's value, by name, to the side that receives it.
        accrued: What one unit has accrued in the current periods.
        par_rate: For a swap, the fixed rate that values it at zero; for an
            FRA, the forward its period is fixed or projected at; for a repo
            not yet started, the rate that values it at zero. ``None`` where
            an instrument has none.
        annuity: For a swap, ``sum(notional * tau_i * D(t_i))`` over the
            fixed periods still to pay; ``None`` otherwise.
        canonical_key: The problem, as the module docstring states it.
    """

    as_of: date
    currency: str
    value: float
    legs: tuple[tuple[str, float], ...]
    accrued: float
    par_rate: float | None
    annuity: float | None
    canonical_key: str

    @property
    def valuation_id(self) -> str:
        """SHA-256 over :attr:`canonical_key`."""

        return hashlib.sha256(self.canonical_key.encode("utf-8")).hexdigest()

    @property
    def clean(self) -> float:
        """The value less what is accrued."""

        return self.value - self.accrued


def _require_curves(
    currency: str, discount: object, projection: object
) -> tuple[DiscountCurve, DiscountCurve]:
    for curve, what in ((discount, "discount"), (projection, "projection")):
        if not isinstance(curve, DiscountCurve):
            raise RatesInputError(f"The {what} curve is a DiscountCurve, got {curve!r}.")
        if curve.currency != currency:
            raise RatesInputError(
                f"The {what} curve {curve.name!r} is in {curve.currency}; the instrument is in "
                f"{currency}."
            )
    assert isinstance(discount, DiscountCurve) and isinstance(projection, DiscountCurve)
    if discount.anchor != projection.anchor:
        raise RatesInputError(
            f"The discount curve is from {discount.anchor} and the projection curve from "
            f"{projection.anchor}: a valuation is as of one date."
        )
    return discount, projection


def _fixing(
    fixings: Fixings, index: str, day: date, read: dict[tuple[str, date], Decimal]
) -> float:
    key = (index, day)
    if key not in fixings:
        raise RatesInputError(
            f"{index}'s fixing on {day} is not supplied, and a rate fixed before the valuation "
            "date is known, never projected."
        )
    value = fixings[key]
    if not isinstance(value, Decimal) or not value.is_finite():
        raise RatesInputError(f"{index}'s fixing on {day} is {value!r}, not a finite Decimal.")
    read[key] = value
    return float(value)


def _floating_rate(
    leg: FloatingLeg,
    period: CouponPeriod,
    as_of: date,
    projection: DiscountCurve,
    fixings: Fixings,
    read: dict[tuple[str, date], Decimal],
) -> float:
    assert period.fixing is not None
    if period.fixing < as_of:
        return _fixing(fixings, leg.index, period.fixing, read)
    return (
        projection.discount(period.start) / projection.discount(period.end) - 1.0
    ) / period.accrual


def _key(
    kind: str,
    terms: RatesTerms,
    as_of: date,
    curves: Sequence[tuple[str, DiscountCurve]],
    periods: tuple[tuple[str, tuple[CouponPeriod, ...]], ...],
    read: Mapping[tuple[str, date], Decimal],
    stated: Sequence[str] = (),
) -> str:
    lines = [
        VALUATION_KEY_SCHEME,
        f"kind={kind}",
        f"terms={json.dumps(rates_terms_to_primitives(terms), sort_keys=True)}",
        f"as_of={as_of.isoformat()}",
        *(f"{role}={curve.curve_id}" for role, curve in curves),
        *stated,
    ]
    for name, leg in periods:
        lines += [
            f"period={name}|{p.start.isoformat()}|{p.end.isoformat()}|"
            f"{'' if p.fixing is None else p.fixing.isoformat()}"
            for p in leg
        ]
    lines += [
        f"fixing={index}|{day.isoformat()}|{value}" for (index, day), value in sorted(read.items())
    ]
    return "\n".join(lines)


def _accrued_fraction(
    leg: FixedLeg | FloatingLeg, period: CouponPeriod, day: date, months: int
) -> float:
    if day <= period.start:
        return 0.0
    frequency = 12 // months if 12 % months == 0 else None
    needed = required_terms(leg.day_count)
    if not needed:
        return accrual(period.start, day, leg.day_count)
    if needed == frozenset({"termination"}):
        return accrual(period.start, day, leg.day_count, DayCountTerms(termination=period.end))
    return accrual(
        period.start,
        day,
        leg.day_count,
        DayCountTerms(period_start=period.start, period_end=period.end, frequency=frequency),
    )


def swap_accrued(
    terms: SwapTerms,
    day: date,
    calendars: Mapping[str, TradingDayCalendar],
    fixings: Fixings,
) -> float:
    """What one unit of the swap has accrued on ``day``: its fixed leg's less its floating leg's.

    Each leg's current period -- the one that started before ``day`` and pays
    after it -- accrues its notional times its rate times the year fraction
    from its start to ``day`` under its basis. A floating period's rate is its
    fixing plus its spread, and its fixing must be supplied.

    Raises:
        RatesInputError: As :func:`leg_periods`, or if a fixing is missing.
    """

    read: dict[tuple[str, date], Decimal] = {}
    total = 0.0
    for leg, sign in ((terms.fixed, 1.0), (terms.floating, -1.0)):
        for period in leg_periods(leg, terms.start, terms.length, calendars):
            if not period.start < day < period.end:
                continue
            fraction = _accrued_fraction(leg, period, day, tenor_months(leg.schedule.step))
            if isinstance(leg, FloatingLeg):
                assert period.fixing is not None
                rate = _fixing(fixings, leg.index, period.fixing, read) + float(leg.spread)
            else:
                rate = float(leg.rate)
            total += sign * float(leg.notional) * rate * fraction
    return total


def value_swap(
    terms: SwapTerms,
    *,
    discount: DiscountCurve,
    projection: DiscountCurve,
    calendars: Mapping[str, TradingDayCalendar],
    fixings: Fixings,
) -> Valuation:
    """A swap's value to one unit held, receiving fixed and paying floating, on the curves' date.

    Args:
        terms: The swap's terms.
        discount: The curve every payment is discounted at.
        projection: The curve the floating leg's forwards are read off: the
            discount curve itself for one curve, or the index's own.
        calendars: Each calendar the terms name, by id.
        fixings: Each fixing a period fixed before the valuation date reads.
            Required; an empty mapping states that there is none to read.

    Raises:
        RatesInputError: If a curve is in another currency or from another
            date, a calendar or a fixing is missing, or the swap has nothing
            left to pay.
        CurveRangeError: If a payment lies beyond a curve that states no
            extrapolation.
    """

    if not isinstance(terms, SwapTerms):
        raise RatesInputError(f"terms are SwapTerms, got {terms!r}.")
    curves = _require_curves(terms.currency, discount, projection)
    as_of = discount.anchor
    fixed_periods = leg_periods(terms.fixed, terms.start, terms.length, calendars)
    floating_periods = leg_periods(terms.floating, terms.start, terms.length, calendars)
    if fixed_periods[-1].end <= as_of and floating_periods[-1].end <= as_of:
        raise RatesInputError(
            f"The swap's last payment was on {fixed_periods[-1].end}; on {as_of} it has "
            "nothing left to value."
        )
    read: dict[tuple[str, date], Decimal] = {}
    fixed, annuity = terms.fixed, 0.0
    fixed_terms = []
    for period in fixed_periods:
        if period.end <= as_of:
            continue
        weight = float(fixed.notional) * period.accrual * discount.discount(period.end)
        annuity += weight
        fixed_terms.append(weight)
    fixed_value = float(fixed.rate) * math.fsum(fixed_terms)
    floating = terms.floating
    pieces = []
    for period in floating_periods:
        if period.end <= as_of:
            continue
        rate = _floating_rate(floating, period, as_of, projection, fixings, read)
        pieces.append(
            float(floating.notional)
            * (rate + float(floating.spread))
            * period.accrual
            * discount.discount(period.end)
        )
    floating_value = math.fsum(pieces)
    accrued = swap_accrued(terms, as_of, calendars, fixings)
    key = _key(
        "swap",
        terms,
        as_of,
        (("discount", curves[0]), ("projection", curves[1])),
        (("fixed", fixed_periods), ("floating", floating_periods)),
        read,
    )
    return Valuation(
        as_of,
        terms.currency,
        fixed_value - floating_value,
        (("fixed", fixed_value), ("floating", floating_value)),
        accrued,
        floating_value / annuity if annuity else None,
        annuity,
        key,
    )


def value_fra(
    terms: FraTerms,
    *,
    discount: DiscountCurve,
    projection: DiscountCurve,
    calendars: Mapping[str, TradingDayCalendar],
    fixings: Fixings,
) -> Valuation:
    """An FRA's value to one unit held -- receiving the fixing, paying its rate -- until it settles.

    Raises:
        RatesInputError: As :func:`value_swap`, or if the FRA has settled: it
            settles at its start, and after that nothing is left to value.
    """

    if not isinstance(terms, FraTerms):
        raise RatesInputError(f"terms are FraTerms, got {terms!r}.")
    curves = _require_curves(terms.currency, discount, projection)
    as_of = discount.anchor
    if terms.start <= as_of:
        raise RatesInputError(
            f"The FRA settled at its start, {terms.start}; on {as_of} nothing is left to value."
        )
    found = resolve_calendars(terms.calendars, calendars, "An FRA")
    try:
        fixed_on = subtract_business_days(terms.start, terms.fixing_lag, found)
    except ConventionError as exc:
        raise RatesInputError(str(exc)) from exc
    tau = accrual(terms.start, terms.end, terms.day_count)
    period = CouponPeriod(terms.start, terms.end, tau, fixed_on)
    read: dict[tuple[str, date], Decimal] = {}
    if fixed_on < as_of:
        forward = _fixing(fixings, terms.index, fixed_on, read)
    else:
        forward = (projection.discount(terms.start) / projection.discount(terms.end) - 1.0) / tau
    value = (
        float(terms.notional) * (forward - float(terms.rate)) * tau * discount.discount(terms.end)
    )
    key = _key(
        "fra",
        terms,
        as_of,
        (("discount", curves[0]), ("projection", curves[1])),
        (("period", (period,)),),
        read,
    )
    return Valuation(
        as_of, terms.currency, value, (("difference", value),), 0.0, forward, None, key
    )


@in_accounting_context
def fra_settlement(terms: FraTerms, fixing: Decimal) -> Decimal:
    """What one unit of an FRA settles for at its start, at ``fixing``: ADR-0065's FRA settlement.

    ``notional * (fixing - rate) * tau / (1 + fixing * tau)``: the period's
    difference, discounted from its end to its start at the fixing itself.
    Exact in ``Decimal`` from the terms and the period's year fraction.

    Raises:
        RatesInputError: If ``fixing`` is not a finite Decimal.
    """

    if not isinstance(fixing, Decimal) or not fixing.is_finite():
        raise RatesInputError(f"A fixing is a finite Decimal, got {fixing!r}.")
    tau = Decimal(repr(accrual(terms.start, terms.end, terms.day_count)))
    return terms.notional * (fixing - terms.rate) * tau / (1 + fixing * tau)
