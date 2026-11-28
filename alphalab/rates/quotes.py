"""What a curve is bootstrapped from: quotes the researcher supplies, with their conventions.

Each quote states its instrument's dates and conventions and its quoted value,
and nothing has a default (ADR-0065 section 3, AC-12). The engine ships no
quote (AC-22).

* :class:`DepositQuote`: a deposit's simple rate from its start to its end.
* :class:`FraQuote`: a forward rate agreement's simple rate over its period.
* :class:`FutureQuote`: a rate future's price over its reference period, with
  its convexity adjustment stated -- zero states none.
* :class:`SwapQuote`: a par swap's fixed rate, with each leg's schedule.
* :class:`BondQuote`: a fixed-coupon bond's full price at its settlement, a
  zero-coupon bond's included.

What each states about the curve
--------------------------------
Writing ``P`` for the curve being bootstrapped, ``D`` for the curve its cash
flows are discounted at -- ``P`` itself unless the bootstrap is given a
discount curve -- and ``tau`` for a period's year fraction under the quote's
basis:

* a deposit or an FRA: ``P(start) / P(end) = 1 + rate * tau``;
* a future: the same, at the rate ``1 - price / 100`` less its convexity
  adjustment;
* a swap: ``rate * sum(tau_i * D(t_i))`` over its fixed periods equals
  ``sum((P(s_j-1) / P(s_j) - 1) * D(s_j))`` over its floating ones, each
  floating period's forward read over the period itself;
* a bond: its coupons and redemption, each discounted at its date, are worth
  its full price at settlement: ``sum(amount_k * P(d_k)) = price * P(settlement)``.

A floating leg's day count is not stated, because nothing reads it: a floating
period's coupon projected off a curve, ``tau * (P(s_j-1) / P(s_j) - 1) / tau``,
does not depend on it.
"""

from __future__ import annotations

import calendar as gregorian
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from alphalab.conventions.business_days import BusinessDayConvention, Tenor
from alphalab.conventions.daycount import DayCount
from alphalab.conventions.settlement import TradingDayCalendar
from alphalab.rates._checks import (
    accrual,
    period_fraction,
    require_basis,
    require_day,
    require_finite,
)
from alphalab.rates.exceptions import RatesInputError
from alphalab.rates.schedule import regular_schedule, tenor_months

__all__ = [
    "BondQuote",
    "DepositQuote",
    "FraQuote",
    "FutureQuote",
    "RateQuote",
    "SwapQuote",
    "fixed_bond_flows",
]


def _period(start: object, end: object, what: str) -> tuple[date, date]:
    first = require_day(start, f"{what}'s start")
    last = require_day(end, f"{what}'s end")
    if last <= first:
        raise RatesInputError(f"{what} runs from {first} to a later date, not to {last}.")
    return first, last


@dataclass(frozen=True, slots=True)
class DepositQuote:
    """A deposit's simple rate from its start to its end, accrued under its basis.

    Raises:
        RatesInputError: If the period is not one, the rate not a finite
            number, or the basis one that needs an instrument's terms.
    """

    start: date
    end: date
    rate: float
    day_count: DayCount

    def __post_init__(self) -> None:
        _period(self.start, self.end, "A deposit")
        object.__setattr__(self, "rate", require_finite(self.rate, "A deposit's rate"))
        require_basis(self.day_count, "A deposit's day count", terms_free=True)
        accrual(self.start, self.end, self.day_count)

    @property
    def pillar(self) -> date:
        """The last date the quote reads: its end."""

        return self.end

    @property
    def quoted(self) -> float:
        return self.rate

    def canonical(self) -> str:
        """The quote as one line of a bootstrap's identity."""

        return (
            f"deposit|{self.start.isoformat()}|{self.end.isoformat()}|{self.day_count.name}|"
            f"{self.rate!r}"
        )


@dataclass(frozen=True, slots=True)
class FraQuote:
    """A forward rate agreement's simple rate over its period, accrued under its basis.

    Raises:
        RatesInputError: As :class:`DepositQuote`.
    """

    start: date
    end: date
    rate: float
    day_count: DayCount

    def __post_init__(self) -> None:
        _period(self.start, self.end, "An FRA")
        object.__setattr__(self, "rate", require_finite(self.rate, "An FRA's rate"))
        require_basis(self.day_count, "An FRA's day count", terms_free=True)
        accrual(self.start, self.end, self.day_count)

    @property
    def pillar(self) -> date:
        return self.end

    @property
    def quoted(self) -> float:
        return self.rate

    def canonical(self) -> str:
        return (
            f"fra|{self.start.isoformat()}|{self.end.isoformat()}|{self.day_count.name}|"
            f"{self.rate!r}"
        )


@dataclass(frozen=True, slots=True)
class FutureQuote:
    """A rate future's price over its reference period, and its convexity adjustment.

    The forward rate it states is ``1 - price / 100 - convexity_adjustment``:
    a future's rate is above the forward by the convexity of its daily
    margining, and how far is a model's answer, so it is the researcher's to
    state. Zero states that none is made.

    Raises:
        RatesInputError: As :class:`DepositQuote`, or a price or adjustment
            that is not a finite number.
    """

    start: date
    end: date
    price: float
    day_count: DayCount
    convexity_adjustment: float

    def __post_init__(self) -> None:
        _period(self.start, self.end, "A future's reference period")
        object.__setattr__(self, "price", require_finite(self.price, "A future's price"))
        object.__setattr__(
            self,
            "convexity_adjustment",
            require_finite(self.convexity_adjustment, "A future's convexity adjustment"),
        )
        require_basis(self.day_count, "A future's day count", terms_free=True)
        accrual(self.start, self.end, self.day_count)

    @property
    def pillar(self) -> date:
        return self.end

    @property
    def quoted(self) -> float:
        return self.price

    @property
    def forward(self) -> float:
        """The forward rate the price states, after its convexity adjustment."""

        return 1.0 - self.price / 100.0 - self.convexity_adjustment

    def canonical(self) -> str:
        return (
            f"future|{self.start.isoformat()}|{self.end.isoformat()}|{self.day_count.name}|"
            f"{self.price!r}|{self.convexity_adjustment!r}"
        )


@dataclass(frozen=True, slots=True)
class SwapQuote:
    """A par swap's fixed rate, and each leg's schedule.

    Both legs run from ``start`` for ``length`` under one business-day
    convention, calendar set and end-of-month rule, each by its own step
    (:func:`~alphalab.rates.schedule.regular_schedule`). The fixed leg accrues
    under ``fixed_day_count``; a basis that needs a coupon period's terms reads
    each period's own.

    Attributes:
        fixed_dates: The fixed leg's adjusted dates, derived.
        float_dates: The floating leg's adjusted dates, derived.

    Raises:
        RatesInputError: If a field is malformed, or a leg's length is not a
            whole number of its steps.
    """

    start: date
    length: Tenor
    rate: float
    fixed_step: Tenor
    fixed_day_count: DayCount
    float_step: Tenor
    convention: BusinessDayConvention
    calendars: tuple[TradingDayCalendar, ...] = field(compare=False)
    end_of_month: bool
    fixed_dates: tuple[date, ...] = field(init=False)
    float_dates: tuple[date, ...] = field(init=False)
    fixed_accruals: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        require_day(self.start, "A swap's start")
        object.__setattr__(self, "rate", require_finite(self.rate, "A swap's rate"))
        require_basis(self.fixed_day_count, "A swap's fixed day count", terms_free=False)
        calendars = tuple(self.calendars)
        object.__setattr__(self, "calendars", calendars)
        legs = []
        for step in (self.fixed_step, self.float_step):
            legs.append(
                regular_schedule(
                    self.start,
                    self.length,
                    step,
                    self.convention,
                    calendars,
                    end_of_month=self.end_of_month,
                )
            )
        fixed, floating = legs
        if fixed[-1] != floating[-1]:
            raise RatesInputError(
                f"The swap's legs end on {fixed[-1]} and {floating[-1]}; both run for its length."
            )
        months = tenor_months(self.fixed_step)
        if 12 % months:
            raise RatesInputError(
                f"A fixed leg steps a whole fraction of a year; {self.fixed_step.label} is not one."
            )
        accruals = tuple(
            period_fraction(
                earlier, later, self.fixed_day_count, frequency=12 // months, termination=fixed[-1]
            )
            for earlier, later in pairwise(fixed)
        )
        object.__setattr__(self, "fixed_dates", fixed)
        object.__setattr__(self, "float_dates", floating)
        object.__setattr__(self, "fixed_accruals", accruals)

    @property
    def pillar(self) -> date:
        return self.fixed_dates[-1]

    @property
    def quoted(self) -> float:
        return self.rate

    def canonical(self) -> str:
        fixed = ",".join(day.isoformat() for day in self.fixed_dates)
        floating = ",".join(day.isoformat() for day in self.float_dates)
        return f"swap|{self.rate!r}|fixed={self.fixed_day_count.name}:{fixed}|float={floating}"


def _months_before(anchor: date, months: int) -> date:
    index = anchor.month - 1 - months
    year, month = anchor.year + index // 12, index % 12 + 1
    return date(year, month, min(anchor.day, gregorian.monthrange(year, month)[1]))


def fixed_bond_flows(
    settlement: date, maturity: date, coupon: float, frequency: int, day_count: DayCount
) -> tuple[tuple[date, float], ...]:
    """A fixed-coupon bond's cash flows after ``settlement``, per 100 of face, by its conventions.

    The coupon dates step back from maturity by ``12 / frequency`` months, each
    counted from maturity, so a month-end maturity keeps month-end coupons.
    Each coupon pays ``100 * coupon`` times its period's year fraction under
    ``day_count``; a basis that needs the coupon period, ACT/ACT (ICMA) among
    them, reads each period's own. The redemption of 100 is paid with the
    last coupon. A zero-coupon bond's only flow is its redemption. Each flow is
    dated by its unadjusted date.

    Raises:
        RatesInputError: If maturity is not after settlement, the coupon is
            not a finite number, the frequency does not divide a year into
            whole months, or the basis cannot measure a coupon period.
    """

    _period(settlement, maturity, "A bond")
    rate = require_finite(coupon, "A bond's coupon")
    if (
        isinstance(frequency, bool)
        or not isinstance(frequency, int)
        or frequency < 1
        or 12 % frequency
    ):
        raise RatesInputError(
            f"A bond's frequency is {frequency!r}; coupons a year that divide it into whole "
            "months: 1, 2, 3, 4, 6 or 12."
        )
    require_basis(day_count, "A bond's day count", terms_free=False)
    step = 12 // frequency
    dates = [maturity]
    while dates[-1] > settlement:
        dates.append(_months_before(maturity, step * len(dates)))
    flows = []
    for later, earlier in pairwise(dates):
        fraction = period_fraction(
            earlier, later, day_count, frequency=frequency, termination=maturity
        )
        amount = 100.0 * rate * fraction + (100.0 if later == maturity else 0.0)
        if amount != 0.0:
            flows.append((later, amount))
    return tuple(reversed(flows))


@dataclass(frozen=True, slots=True)
class BondQuote:
    """A fixed-coupon bond's full price per 100 of face, paid at its settlement.

    Its coupon dates step back from maturity by ``12 / frequency`` months,
    each counted from maturity, and each coupon pays ``100 * coupon`` times its
    period's year fraction under ``day_count``. A basis that needs the coupon
    period, ACT/ACT (ICMA) among them, reads each period's own. The coupons
    after settlement and the redemption of 100 at maturity are the bond's cash
    flows, each discounted at its unadjusted date. A zero-coupon bond states a
    coupon of zero.

    Attributes:
        flows: Each cash flow after settlement, ``(date, amount)``, derived.

    Raises:
        RatesInputError: If a field is malformed, the frequency does not
            divide a year into whole months, or maturity is not after
            settlement.
    """

    settlement: date
    maturity: date
    coupon: float
    frequency: int
    day_count: DayCount
    price: float
    flows: tuple[tuple[date, float], ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", require_finite(self.price, "A bond's price"))
        flows = fixed_bond_flows(
            self.settlement, self.maturity, self.coupon, self.frequency, self.day_count
        )
        object.__setattr__(self, "coupon", float(self.coupon))
        object.__setattr__(self, "flows", flows)

    @property
    def pillar(self) -> date:
        return self.maturity

    @property
    def quoted(self) -> float:
        return self.price

    def canonical(self) -> str:
        return (
            f"bond|{self.settlement.isoformat()}|{self.maturity.isoformat()}|{self.coupon!r}|"
            f"{self.frequency}|{self.day_count.name}|{self.price!r}"
        )


#: What a curve is bootstrapped from.
RateQuote = DepositQuote | FraQuote | FutureQuote | SwapQuote | BondQuote


def require_quotes(quotes: Sequence[object]) -> tuple[RateQuote, ...]:
    """``quotes`` as a tuple, each one a quote, at least one."""

    stated: list[RateQuote] = []
    for quote in quotes:
        if not isinstance(quote, DepositQuote | FraQuote | FutureQuote | SwapQuote | BondQuote):
            raise RatesInputError(f"{quote!r} is not a quote a curve is bootstrapped from.")
        stated.append(quote)
    if not stated:
        raise RatesInputError("A bootstrap needs at least one quote.")
    return tuple(stated)
