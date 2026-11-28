"""Floaters, linkers and repos: their cash flows by declared terms, and their values (ADR-0065).

* **A floater** pays each floating period's ``notional * (rate + spread) *
  tau`` and its redemption at maturity. A rate fixed before the valuation date
  is read as supplied, and one fixing on or after it is projected, as a swap's
  floating leg is (:mod:`alphalab.rates.pricing`).
* **A linker** pays each real coupon, ``notional * rate * tau``, scaled by the
  index ratio ``I(t) / base`` on its payment date. At maturity it pays the
  scaled principal, or the notional itself when it is floored and the ratio is
  below one. ``I(t)`` is its reference index (:func:`reference_index`), read
  from the index's monthly prints by the bond's lag and indexation. A print it
  needs and nobody supplies is refused, never assumed.
* **A repo** lends one unit of cash per unit held from its start and is repaid
  ``1 + rate * tau`` at its end.

Values
------
A floater and a repo are valued as a swap is, off the curves supplied, as of
the discount curve's anchor. A linker is valued off a **real** curve: its real
coupons and principal discounted at real rates are worth that sum in today's
index units, so its value is today's index ratio times it,
``I(today) / base * (sum(notional * rate * tau_i * R(t_i)) + notional * R(T))``.
The value of its deflation floor, an option, is not modelled. The floor is
applied where a principal is paid, by :func:`linker_principal`.
"""

from __future__ import annotations

import calendar as gregorian
import math
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Final

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.conventions.settlement import TradingDayCalendar
from alphalab.rates._checks import accrual
from alphalab.rates.curve import DiscountCurve
from alphalab.rates.exceptions import RatesInputError
from alphalab.rates.pricing import (
    Fixings,
    Valuation,
    _accrued_fraction,
    _floating_rate,
    _key,
    _require_curves,
    leg_periods,
)
from alphalab.rates.schedule import tenor_months
from alphalab.rates.terms import (
    FloaterTerms,
    Indexation,
    LinkerTerms,
    RepoTerms,
)

__all__ = [
    "IndexPrints",
    "floater_maturity",
    "linker_principal",
    "reference_index",
    "repo_repayment",
    "value_floater",
    "value_linker",
    "value_repo",
]

#: An index's monthly prints, by index name and the first day of the month each measures.
IndexPrints = Mapping[tuple[str, date], Decimal]

_FIRST: Final = 1


def _month_back(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    return date(index // 12, index % 12 + 1, _FIRST)


def _print(prints: IndexPrints, index: str, month: date) -> Decimal:
    value = prints.get((index, month))
    if value is None:
        raise RatesInputError(
            f"{index}'s print for {month:%B %Y} is not supplied, and a linker's reference index "
            "is read from its prints, never assumed."
        )
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise RatesInputError(
            f"{index}'s print for {month:%B %Y} is {value!r}, not a positive Decimal."
        )
    return value


def reference_index(terms: LinkerTerms, day: date, prints: IndexPrints) -> Decimal:
    """``day``'s reference index for the linker, by its lag and indexation, in ``Decimal``.

    ``MONTHLY`` is the print of the month ``lag_months`` before ``day``'s.
    ``DAILY_INTERPOLATED`` moves from that print towards the next month's by
    the day of the month: ``I(m - lag) + (d - 1) / D * (I(m - lag + 1) -
    I(m - lag))`` on day ``d`` of a month of ``D`` days. Computed under the
    accounting context, so it is the same on every host.

    Raises:
        RatesInputError: If a print it reads is not supplied.
    """

    if not isinstance(terms, LinkerTerms):
        raise RatesInputError(f"terms are LinkerTerms, got {terms!r}.")
    first = _print(prints, terms.index, _month_back(day, terms.lag_months))
    if terms.indexation is Indexation.MONTHLY:
        return first
    second = _print(prints, terms.index, _month_back(day, terms.lag_months - 1))
    ctx = ACCOUNTING_CONTEXT
    days = gregorian.monthrange(day.year, day.month)[1]
    weight = ctx.divide(Decimal(day.day - 1), Decimal(days))
    return ctx.add(first, ctx.multiply(weight, ctx.subtract(second, first)))


def linker_principal(
    terms: LinkerTerms, prints: IndexPrints, calendars: Mapping[str, TradingDayCalendar]
) -> Decimal:
    """What one unit's principal pays at maturity: the indexed notional, floored at par if stated.

    Raises:
        RatesInputError: As :func:`reference_index`, or if a calendar is missing.
    """

    maturity = leg_periods(terms.leg, terms.start, terms.length, calendars)[-1].end
    ctx = ACCOUNTING_CONTEXT
    ratio = ctx.divide(reference_index(terms, maturity, prints), terms.base)
    if terms.floored and ratio < 1:
        ratio = Decimal(1)
    return ctx.multiply(terms.leg.notional, ratio)


def floater_maturity(terms: FloaterTerms, calendars: Mapping[str, TradingDayCalendar]) -> date:
    """The day a floater's last coupon and its redemption are paid."""

    return leg_periods(terms.leg, terms.start, terms.length, calendars)[-1].end


def repo_repayment(terms: RepoTerms) -> Decimal:
    """What one unit of a repo repays at its end: ``1 + rate * tau``, in ``Decimal``.

    ``tau`` is the shortest ``repr`` of the period's year fraction under the
    repo's basis, so the repayment is the same on every host.
    """

    if not isinstance(terms, RepoTerms):
        raise RatesInputError(f"terms are RepoTerms, got {terms!r}.")
    tau = Decimal(repr(accrual(terms.start, terms.end, terms.day_count)))
    ctx = ACCOUNTING_CONTEXT
    return ctx.add(Decimal(1), ctx.multiply(terms.rate, tau))


def value_floater(
    terms: FloaterTerms,
    *,
    discount: DiscountCurve,
    projection: DiscountCurve,
    calendars: Mapping[str, TradingDayCalendar],
    fixings: Fixings,
) -> Valuation:
    """A floater's value to one unit held, as of the curves' date.

    Raises:
        RatesInputError: As :func:`~alphalab.rates.pricing.value_swap`, or if
            the floater has matured.
    """

    if not isinstance(terms, FloaterTerms):
        raise RatesInputError(f"terms are FloaterTerms, got {terms!r}.")
    curves = _require_curves(terms.currency, discount, projection)
    as_of = discount.anchor
    periods = leg_periods(terms.leg, terms.start, terms.length, calendars)
    maturity = periods[-1].end
    if maturity <= as_of:
        raise RatesInputError(
            f"The floater matured on {maturity}; on {as_of} nothing is left to value."
        )
    leg = terms.leg
    read: dict[tuple[str, date], Decimal] = {}
    coupons: list[float] = []
    accrued = 0.0
    for period in periods:
        if period.end <= as_of:
            continue
        rate = _floating_rate(leg, period, as_of, projection, fixings, read)
        coupons.append(
            float(leg.notional)
            * (rate + float(leg.spread))
            * period.accrual
            * discount.discount(period.end)
        )
        if period.start < as_of:
            fraction = _accrued_fraction(leg, period, as_of, tenor_months(leg.schedule.step))
            accrued += float(leg.notional) * (rate + float(leg.spread)) * fraction
    coupon_value = math.fsum(coupons)
    principal = float(terms.redemption) * discount.discount(maturity)
    key = _key(
        "floater",
        terms,
        as_of,
        (("discount", curves[0]), ("projection", curves[1])),
        (("coupon", periods),),
        read,
    )
    return Valuation(
        as_of,
        terms.currency,
        coupon_value + principal,
        (("coupons", coupon_value), ("redemption", principal)),
        accrued,
        None,
        None,
        key,
    )


def value_linker(
    terms: LinkerTerms,
    *,
    real: DiscountCurve,
    calendars: Mapping[str, TradingDayCalendar],
    prints: IndexPrints,
) -> Valuation:
    """A linker's value to one unit held, off a real curve, as of its date.

    Today's index ratio, ``I(as_of) / base``, times its real coupons and
    principal discounted at ``real``. Its deflation floor's option value is
    not modelled.

    Raises:
        RatesInputError: If the real curve is in another currency, a calendar
            or a print is missing, or the linker has matured.
    """

    if not isinstance(terms, LinkerTerms):
        raise RatesInputError(f"terms are LinkerTerms, got {terms!r}.")
    if not isinstance(real, DiscountCurve) or real.currency != terms.currency:
        raise RatesInputError(f"A linker in {terms.currency} is valued off a real curve in it.")
    as_of = real.anchor
    periods = leg_periods(terms.leg, terms.start, terms.length, calendars)
    maturity = periods[-1].end
    if maturity <= as_of:
        raise RatesInputError(
            f"The linker matured on {maturity}; on {as_of} nothing is left to value."
        )
    today = reference_index(terms, as_of, prints)
    ratio = float(today / terms.base)
    leg = terms.leg
    coupons: list[float] = []
    accrued = 0.0
    for period in periods:
        if period.end <= as_of:
            continue
        coupons.append(
            float(leg.notional) * float(leg.rate) * period.accrual * real.discount(period.end)
        )
        if period.start < as_of:
            fraction = _accrued_fraction(leg, period, as_of, tenor_months(leg.schedule.step))
            accrued += float(leg.notional) * float(leg.rate) * fraction * ratio
    coupon_value = ratio * math.fsum(coupons)
    principal = ratio * float(leg.notional) * real.discount(maturity)
    key = _key(
        "linker",
        terms,
        as_of,
        (("real", real),),
        (("coupon", periods),),
        {},
        (f"reference_index={today}",),
    )
    return Valuation(
        as_of,
        terms.currency,
        coupon_value + principal,
        (("coupons", coupon_value), ("principal", principal)),
        accrued,
        None,
        None,
        key,
    )


def value_repo(terms: RepoTerms, *, discount: DiscountCurve) -> Valuation:
    """A repo's value to one unit held -- one unit of cash lent -- as of the curve's date.

    Before its start: its repayment discounted from its end, less the unit lent
    at its start. From its start: its repayment discounted. Its par rate, before
    it starts, is the rate that values it at zero.

    Raises:
        RatesInputError: If the curve is in another currency, or the repo has
            been repaid.
    """

    if not isinstance(terms, RepoTerms):
        raise RatesInputError(f"terms are RepoTerms, got {terms!r}.")
    if not isinstance(discount, DiscountCurve) or discount.currency != terms.currency:
        raise RatesInputError(f"A repo in {terms.currency} is discounted at a curve in it.")
    as_of = discount.anchor
    if terms.end <= as_of:
        raise RatesInputError(
            f"The repo was repaid on {terms.end}; on {as_of} nothing is left to value."
        )
    tau = accrual(terms.start, terms.end, terms.day_count)
    repaid = (1.0 + float(terms.rate) * tau) * discount.discount(terms.end)
    if as_of < terms.start:
        lent = discount.discount(terms.start)
        value, accrued = repaid - lent, 0.0
        par: float | None = (lent / discount.discount(terms.end) - 1.0) / tau
    else:
        value, par = repaid, None
        accrued = (
            float(terms.rate) * accrual(terms.start, as_of, terms.day_count)
            if as_of > terms.start
            else 0.0
        )
    key = _key("repo", terms, as_of, (("discount", discount),), (), {})
    return Valuation(
        as_of, terms.currency, value, (("repayment", repaid),), accrued, par, None, key
    )
