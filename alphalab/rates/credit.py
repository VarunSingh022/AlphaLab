"""Credit, basic and reduced form: risky cash flows priced off stated curves (ADR-0065 s. 6).

Two inputs price a risky cash flow, each the researcher's: supplied as stated,
or built from spreads or hazards they quote. Default events are not simulated
at M10.

* **A credit spread curve** (:func:`spread_curve`) is a curve of spread
  factors ``S(t) = exp(-s(t) * t)``, ``s`` the continuously compounded spread
  over the discount curve to each date. A flow is worth ``cf * D(t) * S(t)``
  (:func:`spread_value`).
* **A hazard-rate curve** (:func:`hazard_curve`) is a :class:`CreditCurve`:
  survival probabilities ``Q(t)``, from hazard rates held constant between its
  dates -- ``Q`` read log-linearly -- and the recovery a default pays, stated.
  A sequence of flows on a notional is worth (:func:`risky_value`)
  ``sum(cf_i * D(t_i) * Q(t_i)) + R * notional * sum(D(t_i) * (Q(t_i-1) - Q(t_i)))``.
  Each flow is paid only if there is no default by its date. A default in a
  flow's interval, from the date before it (the curve's anchor for the
  first), recovers ``R`` of the notional at the flow's date: a recovery
  discretised on the payment grid, stated.

Each value is a :class:`~alphalab.rates.pricing.Valuation` with an identity
over the flows, the curves, the recovery and the date.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from alphalab.conventions.daycount import DayCount, year_fraction
from alphalab.rates._checks import require_day, require_finite
from alphalab.rates.curve import DiscountCurve, Extrapolation, Interpolation, Pillar
from alphalab.rates.exceptions import RatesInputError
from alphalab.rates.pricing import VALUATION_KEY_SCHEME, Valuation

__all__ = [
    "CreditCurve",
    "hazard_curve",
    "risky_value",
    "spread_curve",
    "spread_value",
]


@dataclass(frozen=True, slots=True)
class CreditCurve:
    """Survival probabilities as a curve, and the recovery a default pays.

    Attributes:
        survival: A curve whose pillars are the probabilities of no default by
            each date, each in ``(0, 1]`` and none above the one before.
        recovery: The fraction of the notional a default recovers, in ``[0, 1)``.

    Raises:
        RatesInputError: If a survival probability is above one or rises, or
            the recovery is not in ``[0, 1)``.
    """

    survival: DiscountCurve
    recovery: float

    def __post_init__(self) -> None:
        if not isinstance(self.survival, DiscountCurve):
            raise RatesInputError(f"survival is a DiscountCurve, got {self.survival!r}.")
        previous = 1.0
        for pillar in self.survival.pillars:
            if pillar.discount > previous:
                raise RatesInputError(
                    f"The survival probability at {pillar.day} is {pillar.discount!r}, above "
                    f"{previous!r} before it: a probability of no default never rises."
                )
            previous = pillar.discount
        recovery = require_finite(self.recovery, "A recovery")
        if not 0.0 <= recovery < 1.0:
            raise RatesInputError(f"A recovery is a fraction in [0, 1), got {recovery!r}.")
        object.__setattr__(self, "recovery", recovery)


def _pillars(
    anchor: date, day_count: DayCount, rates: Sequence[tuple[date, float]], what: str
) -> list[tuple[date, float, float]]:
    out, previous = [], anchor
    for day, rate in rates:
        require_day(day, f"A {what}'s date")
        value = require_finite(rate, f"The {what} to {day}")
        if day <= previous:
            raise RatesInputError(f"The {what}s' dates follow the anchor in order; {day} does not.")
        out.append((day, value, year_fraction(anchor, day, day_count)))
        previous = day
    if not out:
        raise RatesInputError(f"A {what} curve needs at least one {what}.")
    return out


def hazard_curve(
    name: str,
    currency: str,
    anchor: date,
    day_count: DayCount,
    hazards: Sequence[tuple[date, float]],
    recovery: float,
    *,
    extrapolation: Extrapolation | None = None,
) -> CreditCurve:
    """A credit curve from hazard rates, each held from the date before it to its own.

    ``hazards`` gives each date with the continuously compounded hazard rate,
    a year, in force from the previous date (the anchor for the first). The
    survival probability at each date is ``exp(-sum(h_k * (t_k - t_k-1)))``,
    and the curve is read log-linearly, so the hazard is constant between
    them.

    Raises:
        RatesInputError: If a hazard is negative or the dates are not in
            order after the anchor.
    """

    rows = _pillars(anchor, day_count, hazards, "hazard")
    pillars, log, previous = [], 0.0, 0.0
    for day, hazard, time in rows:
        if hazard < 0.0:
            raise RatesInputError(f"The hazard to {day} is {hazard!r}; a hazard is not negative.")
        log -= hazard * (time - previous)
        pillars.append(Pillar(day, math.exp(log)))
        previous = time
    survival = DiscountCurve(
        name,
        currency,
        anchor,
        day_count,
        tuple(pillars),
        Interpolation.LOG_LINEAR_DISCOUNT,
        extrapolation,
    )
    return CreditCurve(survival, recovery)


def spread_curve(
    name: str,
    currency: str,
    anchor: date,
    day_count: DayCount,
    spreads: Sequence[tuple[date, float]],
    interpolation: Interpolation,
    *,
    extrapolation: Extrapolation | None = None,
) -> DiscountCurve:
    """A credit spread curve: factors ``exp(-s * t)`` from the spread quoted to each date.

    Each spread is the continuously compounded spread over the discount curve
    to its date. The curve is read by the stated interpolation, as every curve
    is.

    Raises:
        RatesInputError: If the dates are not in order after the anchor.
    """

    rows = _pillars(anchor, day_count, spreads, "spread")
    pillars = tuple(Pillar(day, math.exp(-spread * time)) for day, spread, time in rows)
    return DiscountCurve(name, currency, anchor, day_count, pillars, interpolation, extrapolation)


def _flows(flows: Sequence[tuple[date, float]], anchor: date) -> list[tuple[date, float]]:
    out, previous = [], anchor
    for day, amount in flows:
        require_day(day, "A flow's date")
        value = require_finite(amount, f"The flow on {day}")
        if day <= previous:
            raise RatesInputError(
                f"Flows are valued after the curves' date, in order; {day} is not after {previous}."
            )
        out.append((day, value))
        previous = day
    if not out:
        raise RatesInputError("A risky value needs at least one flow.")
    return out


def _valuation(
    kind: str,
    currency: str,
    as_of: date,
    value: float,
    legs: tuple[tuple[str, float], ...],
    lines: Sequence[str],
) -> Valuation:
    key = "\n".join([VALUATION_KEY_SCHEME, f"kind={kind}", f"as_of={as_of.isoformat()}", *lines])
    return Valuation(as_of, currency, value, legs, 0.0, None, None, key)


def _same(discount: DiscountCurve, other: DiscountCurve, what: str) -> None:
    if not isinstance(discount, DiscountCurve) or not isinstance(other, DiscountCurve):
        raise RatesInputError(f"The discount curve and the {what} are DiscountCurves.")
    if discount.currency != other.currency or discount.anchor != other.anchor:
        raise RatesInputError(
            f"The discount curve is in {discount.currency} from {discount.anchor}, and the "
            f"{what} in {other.currency} from {other.anchor}: a value is in one currency, as of "
            "one date."
        )


def spread_value(
    flows: Sequence[tuple[date, float]], *, discount: DiscountCurve, spread: DiscountCurve
) -> Valuation:
    """Risky flows' value at a spread over the discount curve: ``sum(cf * D(t) * S(t))``.

    Raises:
        RatesInputError: If a flow is not after the curves' date, or the curves
            disagree on currency or date.
    """

    _same(discount, spread, "spread curve")
    stated = _flows(flows, discount.anchor)
    value = math.fsum(
        amount * discount.discount(day) * spread.discount(day) for day, amount in stated
    )
    lines = [
        f"discount={discount.curve_id}",
        f"spread={spread.curve_id}",
        *(f"flow={day.isoformat()}|{amount!r}" for day, amount in stated),
    ]
    return _valuation(
        "spread", discount.currency, discount.anchor, value, (("flows", value),), lines
    )


def risky_value(
    flows: Sequence[tuple[date, float]],
    *,
    discount: DiscountCurve,
    credit: CreditCurve,
    notional: float,
) -> Valuation:
    """Risky flows on a notional, under a hazard-rate curve and its recovery.

    See the module docstring for the formula and its recovery on the payment
    grid.

    Raises:
        RatesInputError: If a flow is not after the curves' date, the curves
            disagree on currency or date, or the notional is not positive.
    """

    if not isinstance(credit, CreditCurve):
        raise RatesInputError(f"credit is a CreditCurve, got {credit!r}.")
    _same(discount, credit.survival, "survival curve")
    size = require_finite(notional, "A notional")
    if size <= 0.0:
        raise RatesInputError(f"A notional is positive, got {size!r}.")
    stated = _flows(flows, discount.anchor)
    survive, recover, previous = [], [], 1.0
    for day, amount in stated:
        q, d = credit.survival.discount(day), discount.discount(day)
        survive.append(amount * d * q)
        recover.append(d * (previous - q))
        previous = q
    paid = math.fsum(survive)
    recovered = credit.recovery * size * math.fsum(recover)
    lines = [
        f"discount={discount.curve_id}",
        f"survival={credit.survival.curve_id}",
        f"recovery={credit.recovery!r}",
        f"notional={size!r}",
        *(f"flow={day.isoformat()}|{amount!r}" for day, amount in stated),
    ]
    return _valuation(
        "risky",
        discount.currency,
        discount.anchor,
        paid + recovered,
        (("survival", paid), ("recovery", recovered)),
        lines,
    )
