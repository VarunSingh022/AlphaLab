"""Bootstrapping a curve from quotes, each pillar solved in turn to a stated tolerance.

A bootstrap (ADR-0065 section 3) reads the researcher's quotes
(:mod:`alphalab.rates.quotes`) in order of the last date each reads, its
pillar, and solves each pillar's discount factor so that the curve reprices its
quote, every earlier pillar held. Between the previous pillar and the new one
the curve is read by its stated interpolation, so a quote with dates between
them is solved under the interpolation it will be read by.

How a pillar is solved
----------------------
Each pillar's ``ln P`` is found by Brent's method
(:func:`alphalab.common.optimize.brent_root`) to the stated tolerance, within
the stated iterations: both required, as for every iterative method (ADR-0058
section 2). It is sought where the new segment's continuously compounded
forward rate lies between -100% and +1,000% a year (:data:`FORWARD_SEARCH`).
A quote no discount factor there reprices -- one that would need a negative
discount factor, or none at all -- is refused with its residual at both ends
of the range, and never skipped.

Multi-curve
-----------
Given a discount curve, the bootstrap builds a projection curve: a swap's
fixed leg and each floating coupon are discounted at the given curve, and its
forwards are read off the curve being built. OIS discounting is that choice,
stated by passing the OIS curve. A bond's price states discount factors, not an
index's forwards, so it is refused in a projection curve's bootstrap.

What it returns
---------------
:class:`CurveBootstrap`: the curve, each quote's repricing -- the value the
returned curve implies beside the one quoted -- and each pillar's convergence
report. Its identity, :attr:`CurveBootstrap.bootstrap_id`, is SHA-256 over
:attr:`CurveBootstrap.canonical_key`, the problem: the curve's name, currency,
anchor, axis, interpolation and extrapolation; the tolerance, iteration limit
and search range; the discount curve's identity; and each quote in pillar
order, its dates as its conventions placed them. The solved discount factors
are numerical class 3 and stay out of it (ADR-0058 section 3). The curve
returned names the bootstrap as its derivation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Final

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import ConvergenceError
from alphalab.common.optimize import brent_root
from alphalab.conventions.daycount import DayCount, year_fraction
from alphalab.rates._checks import (
    accrual,
    require_currency,
    require_day,
    require_finite,
    require_line,
)
from alphalab.rates.curve import (
    DiscountCurve,
    Extrapolation,
    Interpolation,
    Pillar,
    _axis,
    _Grid,
)
from alphalab.rates.exceptions import BootstrapError, CurveRangeError, RatesInputError
from alphalab.rates.quotes import (
    DepositQuote,
    FraQuote,
    FutureQuote,
    RateQuote,
    SwapQuote,
    require_quotes,
)

__all__ = [
    "BOOTSTRAP_KEY_SCHEME",
    "FORWARD_SEARCH",
    "CurveBootstrap",
    "Repricing",
    "bootstrap_curve",
]

#: The scheme of a bootstrap's canonical key; a change to the key is a new scheme.
BOOTSTRAP_KEY_SCHEME: Final = "alphalab.rates.bootstrap.v1"

#: Where a pillar is sought: its segment's continuously compounded forward rate,
#: from -100% to +1,000% a year. A quote that needs a rate outside it is refused.
FORWARD_SEARCH: Final = (-1.0, 10.0)


def _read(grid: _Grid, time: float) -> float:
    value = grid.discount(time)
    if value is None:  # pragma: no cover - every quote reads at or before its own pillar
        raise CurveRangeError(f"A quote read the curve at {time!r} years, beyond its own pillar.")
    return value


class _Simple:
    """``P(start) / P(end) = 1 + rate * tau``: a deposit, an FRA, or a future at its forward."""

    __slots__ = ("end", "rate", "start", "tau")

    def __init__(self, start: float, end: float, rate: float, tau: float) -> None:
        self.start, self.end, self.rate, self.tau = start, end, rate, tau

    def residual(self, grid: _Grid) -> float:
        return _read(grid, self.end) * (1.0 + self.rate * self.tau) - _read(grid, self.start)

    def implied(self, grid: _Grid) -> float:
        return (_read(grid, self.start) / _read(grid, self.end) - 1.0) / self.tau


class _Future(_Simple):
    """A future: solved at its forward, repriced as the price the curve implies."""

    __slots__ = ("adjustment",)

    def __init__(
        self, start: float, end: float, rate: float, tau: float, adjustment: float
    ) -> None:
        super().__init__(start, end, rate, tau)
        self.adjustment = adjustment

    def implied(self, grid: _Grid) -> float:
        return 100.0 * (1.0 - _Simple.implied(self, grid) - self.adjustment)


class _Swap:
    """A par swap: its fixed leg's value equals its floating leg's."""

    __slots__ = ("annuity", "fixed", "float_discounts", "floating", "rate", "taus")

    def __init__(
        self,
        rate: float,
        fixed: tuple[float, ...],
        taus: tuple[float, ...],
        floating: tuple[float, ...],
        annuity: float | None,
        float_discounts: tuple[float, ...] | None,
    ) -> None:
        self.rate, self.fixed, self.taus, self.floating = rate, fixed, taus, floating
        self.annuity, self.float_discounts = annuity, float_discounts

    def legs(self, grid: _Grid) -> tuple[float, float]:
        if self.float_discounts is None:
            # One curve: each floating coupon telescopes, P(s_j-1) - P(s_j).
            annuity = math.fsum(
                tau * _read(grid, time) for tau, time in zip(self.taus, self.fixed[1:], strict=True)
            )
            return annuity, _read(grid, self.floating[0]) - _read(grid, self.floating[-1])
        assert self.annuity is not None
        floating = math.fsum(
            (_read(grid, start) / _read(grid, end) - 1.0) * discount
            for start, end, discount in zip(
                self.floating[:-1], self.floating[1:], self.float_discounts, strict=True
            )
        )
        return self.annuity, floating

    def residual(self, grid: _Grid) -> float:
        annuity, floating = self.legs(grid)
        return self.rate * annuity - floating

    def implied(self, grid: _Grid) -> float:
        annuity, floating = self.legs(grid)
        return floating / annuity


class _Bond:
    """A bond: its cash flows are worth its full price at settlement."""

    __slots__ = ("amounts", "price", "settlement", "times")

    def __init__(
        self, settlement: float, times: tuple[float, ...], amounts: tuple[float, ...], price: float
    ) -> None:
        self.settlement, self.times, self.amounts, self.price = settlement, times, amounts, price

    def _flows(self, grid: _Grid) -> float:
        return math.fsum(
            amount * _read(grid, time)
            for amount, time in zip(self.amounts, self.times, strict=True)
        )

    def residual(self, grid: _Grid) -> float:
        return self._flows(grid) - self.price * _read(grid, self.settlement)

    def implied(self, grid: _Grid) -> float:
        return self._flows(grid) / _read(grid, self.settlement)


_Equation = _Simple | _Swap | _Bond


@dataclass(frozen=True, slots=True)
class Repricing:
    """A quote beside the value the bootstrapped curve implies for it.

    Attributes:
        quote: The quote.
        quoted: Its quoted value: a rate, or a price for a future or a bond.
        implied: The same value read off the curve returned.
    """

    quote: RateQuote
    quoted: float
    implied: float

    @property
    def pillar(self) -> date:
        return self.quote.pillar

    @property
    def error(self) -> float:
        """``implied - quoted``, in the quote's own units."""

        return self.implied - self.quoted


@dataclass(frozen=True, slots=True)
class CurveBootstrap:
    """A bootstrapped curve, how well it reprices each quote, and the problem it answers.

    Attributes:
        curve: The curve, its derivation the bootstrap's identity.
        repricing: Each quote's repricing, in pillar order.
        convergence: Each pillar's convergence report, in pillar order.
        canonical_key: The problem, as the module docstring states it.
    """

    curve: DiscountCurve
    repricing: tuple[Repricing, ...]
    convergence: tuple[ConvergenceReport, ...]
    canonical_key: str

    @property
    def bootstrap_id(self) -> str:
        """SHA-256 over :attr:`canonical_key`."""

        return hashlib.sha256(self.canonical_key.encode("utf-8")).hexdigest()

    @property
    def largest_error(self) -> float:
        """The largest repricing error in magnitude, across the quotes."""

        return max(abs(repricing.error) for repricing in self.repricing)


def _equation(quote: RateQuote, time: _Time, discount: DiscountCurve | None) -> _Equation:
    if isinstance(quote, DepositQuote | FraQuote):
        tau = accrual(quote.start, quote.end, quote.day_count)
        return _Simple(time(quote.start, quote), time(quote.end, quote), quote.rate, tau)
    if isinstance(quote, FutureQuote):
        tau = accrual(quote.start, quote.end, quote.day_count)
        return _Future(
            time(quote.start, quote),
            time(quote.end, quote),
            quote.forward,
            tau,
            quote.convexity_adjustment,
        )
    if isinstance(quote, SwapQuote):
        fixed = tuple(time(day, quote) for day in quote.fixed_dates)
        floating = tuple(time(day, quote) for day in quote.float_dates)
        if discount is None:
            return _Swap(quote.rate, fixed, quote.fixed_accruals, floating, None, None)
        annuity = math.fsum(
            tau * discount.discount(day)
            for tau, day in zip(quote.fixed_accruals, quote.fixed_dates[1:], strict=True)
        )
        float_discounts = tuple(discount.discount(day) for day in quote.float_dates[1:])
        return _Swap(quote.rate, fixed, quote.fixed_accruals, floating, annuity, float_discounts)
    if discount is not None:
        raise RatesInputError(
            f"{quote.canonical()} is a bond's price, which states discount factors, not an "
            "index's forwards: a projection curve is not bootstrapped from one."
        )
    return _Bond(
        time(quote.settlement, quote),
        tuple(time(day, quote) for day, _ in quote.flows),
        tuple(amount for _, amount in quote.flows),
        quote.price,
    )


class _Time:
    """A date's time on the curve's axis, refused before the anchor."""

    __slots__ = ("anchor", "basis")

    def __init__(self, anchor: date, basis: DayCount) -> None:
        self.anchor, self.basis = anchor, basis

    def __call__(self, day: date, quote: RateQuote) -> float:
        if day < self.anchor:
            raise RatesInputError(
                f"{quote.canonical()} reads {day}, before the curve's anchor {self.anchor}."
            )
        return year_fraction(self.anchor, day, self.basis)


def _problem(
    name: str,
    currency: str,
    anchor: date,
    day_count: DayCount,
    interpolation: Interpolation,
    extrapolation: Extrapolation | None,
    tolerance: float,
    max_iterations: int,
    discount: DiscountCurve | None,
    quotes: Sequence[RateQuote],
) -> str:
    extends = "refused" if extrapolation is None else extrapolation.value
    return "\n".join(
        [
            BOOTSTRAP_KEY_SCHEME,
            f"name={json.dumps(name, ensure_ascii=False)}",
            f"currency={currency}",
            f"anchor={anchor.isoformat()}",
            f"day_count={day_count.name}",
            f"interpolation={interpolation.value}",
            f"extrapolation={extends}",
            f"tolerance={tolerance!r}",
            f"max_iterations={max_iterations}",
            f"search={FORWARD_SEARCH[0]!r}|{FORWARD_SEARCH[1]!r}",
            f"discount={'itself' if discount is None else discount.curve_id}",
            *(f"quote={quote.canonical()}" for quote in quotes),
        ]
    )


def bootstrap_curve(
    name: str,
    currency: str,
    anchor: date,
    day_count: DayCount,
    interpolation: Interpolation,
    quotes: Sequence[RateQuote],
    *,
    tolerance: float,
    max_iterations: int,
    extrapolation: Extrapolation | None = None,
    discount: DiscountCurve | None = None,
) -> CurveBootstrap:
    """A curve that reprices each of ``quotes``, solved pillar by pillar.

    Args:
        name: The curve's name.
        currency: Its currency, and the discount curve's.
        anchor: Its date, the discount curve's too when one is given.
        day_count: Its time axis.
        interpolation: How it is read between pillars, while it is solved and
            after. Required.
        quotes: At least one, each reaching a different pillar.
        tolerance: How closely each pillar's ``ln P`` is solved, absolutely.
            Required.
        max_iterations: The iterations each pillar is allowed. Required.
        extrapolation: How the curve returned extends beyond its last pillar;
            ``None`` refuses a read there. It plays no part in solving it.
        discount: The curve a swap's cash flows are discounted at, when it
            is not the curve being built: OIS discounting, stated.

    Raises:
        RatesInputError: If an argument is malformed, two quotes reach one
            pillar or one time on the axis, a quote reads a date before the
            anchor, the discount curve is in another currency or from another
            date, or a bond is quoted for a projection curve.
        BootstrapError: If no discount factor in the search range reprices a
            quote, or a pillar is not solved within its iterations.
        CurveRangeError: If a swap's cash flows lie beyond the discount curve.
    """

    require_line(name, "A curve's name")
    require_currency(currency, "A curve's currency")
    require_day(anchor, "A curve's anchor")
    _axis(day_count)
    if not isinstance(interpolation, Interpolation):
        raise RatesInputError(
            f"interpolation must be an Interpolation, got {interpolation!r}: a bootstrap solves "
            "under the method its curve is read by, which it names."
        )
    if extrapolation is not None and not isinstance(extrapolation, Extrapolation):
        raise RatesInputError(
            f"extrapolation must be an Extrapolation or None, got {extrapolation!r}."
        )
    tolerance = require_finite(tolerance, "A bootstrap's tolerance")
    if tolerance <= 0.0:
        raise RatesInputError(f"A bootstrap's tolerance is {tolerance!r}; it must be positive.")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 1
    ):
        raise RatesInputError(f"max_iterations is {max_iterations!r}; a positive integer.")
    if discount is not None:
        if not isinstance(discount, DiscountCurve):
            raise RatesInputError(f"discount must be a DiscountCurve or None, got {discount!r}.")
        if discount.currency != currency or discount.anchor != anchor:
            raise RatesInputError(
                f"The discount curve {discount.name!r} is in {discount.currency} from "
                f"{discount.anchor}; the curve bootstrapped against it is in {currency} from "
                f"{anchor}, and both must agree."
            )
    ordered = sorted(require_quotes(quotes), key=lambda quote: quote.pillar)
    for earlier, later in pairwise(ordered):
        if earlier.pillar == later.pillar:
            raise RatesInputError(
                f"{earlier.canonical()} and {later.canonical()} both reach {later.pillar}: each "
                "pillar is solved from one quote."
            )
    time = _Time(anchor, day_count)
    equations = [_equation(quote, time, discount) for quote in ordered]
    times = [time(quote.pillar, quote) for quote in ordered]
    previous = 0.0
    for quote, at in zip(ordered, times, strict=True):
        if at <= previous:
            raise RatesInputError(
                f"{quote.canonical()} reaches {quote.pillar}, {at!r} years from the anchor under "
                f"{day_count.name}: no later than the pillar before it on the curve's axis."
            )
        previous = at
    key = _problem(
        name,
        currency,
        anchor,
        day_count,
        interpolation,
        extrapolation,
        tolerance,
        max_iterations,
        discount,
        ordered,
    )
    derivation = hashlib.sha256(key.encode("utf-8")).hexdigest()
    grid = _Grid([], [], interpolation, extrapolation)
    reports: list[ConvergenceReport] = []
    previous_time, previous_log = 0.0, 0.0
    for quote, equation, at in zip(ordered, equations, times, strict=True):
        span = at - previous_time
        lower = previous_log - FORWARD_SEARCH[1] * span
        upper = previous_log - FORWARD_SEARCH[0] * span

        def residual(
            log: float, grid: _Grid = grid, at: float = at, equation: _Equation = equation
        ) -> float:
            return equation.residual(grid.with_trial(at, log))

        at_lower, at_upper = residual(lower), residual(upper)
        if at_lower != 0.0 and at_upper != 0.0 and (at_lower > 0.0) == (at_upper > 0.0):
            raise BootstrapError(
                f"No discount factor at {quote.pillar} reprices {quote.canonical()}: with the "
                "segment's forward rate anywhere from -100% to +1,000% a year, its residual runs "
                f"from {at_upper!r} to {at_lower!r} and never reaches zero."
            )
        try:
            root = brent_root(
                residual, lower, upper, tolerance=tolerance, max_iterations=max_iterations
            )
        except ConvergenceError as exc:
            raise BootstrapError(
                f"The pillar at {quote.pillar} was not solved to {tolerance!r} within "
                f"{max_iterations} iterations: {exc}"
            ) from exc
        grid = grid.with_trial(at, root.x)
        reports.append(root.convergence)
        previous_time, previous_log = at, root.x
    curve = DiscountCurve(
        name,
        currency,
        anchor,
        day_count,
        tuple(
            Pillar(quote.pillar, value)
            for quote, value in zip(ordered, grid.discounts, strict=True)
        ),
        interpolation,
        extrapolation,
        derivation,
    )
    final = curve._grid
    repricing = tuple(
        Repricing(quote, quote.quoted, equation.implied(final))
        for quote, equation in zip(ordered, equations, strict=True)
    )
    return CurveBootstrap(curve, repricing, tuple(reports), key)
