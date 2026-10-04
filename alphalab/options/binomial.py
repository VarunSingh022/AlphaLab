"""American and European option pricing on a Cox-Ross-Rubinstein lattice.

Until v3.13 :mod:`alphalab.options.pricing` was the only model: a European closed
form, so an American contract -- every listed US single-stock option -- was
priced as though it could not be exercised early, and a deep in-the-money
American put was understated by its early-exercise premium (ledger NUM-006). A
known cash dividend could not be stated at all.

This module prices both styles on one lattice. A contract's own
:attr:`~alphalab.options.contract.OptionContract.style` decides whether a node
may exercise: an ``AMERICAN`` contract takes the larger of holding and
exercising at every node, a ``EUROPEAN`` one only holds.

The lattice
-----------

Cox, Ross and Rubinstein (1979), with the underlying's carry ``b`` (see
:mod:`alphalab.options.carry`) and ``n`` equal steps of ``dt = T / n``::

    u = exp(sigma sqrt(dt)),  d = 1 / u,  p = (exp(b dt) - d) / (u - d)

and each step discounts at ``exp(-r dt)``. ``p`` is a probability only while
``|b| sqrt(dt) < sigma``; outside it the lattice has no risk-neutral reading,
and a price from it would be a number without a model. That is refused, with
the step count that would admit it, rather than clamped.

The step count is **required**. It is the method's resolution and it moves the
answer -- a European value on the lattice approaches the closed form as
``O(1/n)``, oscillating -- so it is part of what a price was computed under:
:attr:`BinomialLattice.assumptions` carries it, and two figures computed on
different lattices compare as different models.

Cash dividends: the escrowed model
----------------------------------

A known cash dividend is not a yield. The lattice carries the underlying net of
the present value of the dividends paid before expiry, ``S* = S - PV(D)``, and
lets *that* diffuse; a node's stock price is ``S*`` there plus the present value
of the dividends still to come (Hull, *Options, Futures, and Other Derivatives*,
ch. 21). The tree recombines, a European value on it converges to the closed
form on ``S - PV(D)``, and an American call can be exercised just before the
stock goes ex -- the one case where exercising a call early pays. A dividend is
paid at the first node at or after its ex-date: a node falling exactly on it
sees the stock already ex.

What it does not do
-------------------

One volatility for the whole life, applied to ``S*``; no dividend yield implied
from the dividends; no smile. Greeks come from the lattice itself -- delta and
gamma from its first two layers, theta from the node two steps on at the same
price, vega and rho by central differences of stated size -- so they are the
lattice's sensitivities, not the closed form's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from alphalab.options.carry import Carry, CarryKind
from alphalab.options.contract import OptionContract
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.exceptions import OptionInputError, OptionPricingError
from alphalab.options.greeks import Greeks
from alphalab.options.model import ModelAssumptions, PricingModel
from alphalab.options.pricing import time_to_expiry_years

__all__ = [
    "MAX_STEPS",
    "RATE_BUMP",
    "VOLATILITY_BUMP",
    "BinomialLattice",
    "CashDividend",
    "binomial_greeks",
    "binomial_price",
    "binomial_value",
]

#: The most steps a lattice may take. The work is ``n (n + 1) / 2`` node
#: valuations, about 12.5 million at this bound -- seconds in pure Python, which
#: is what a stated, refusable resolution should cost at most.
MAX_STEPS: Final = 5_000

#: The volatility step vega is differenced over, either side: 0.0001 of
#: volatility, one hundredth of a volatility point.
VOLATILITY_BUMP: Final = 1e-4

#: The rate step rho is differenced over, either side: one basis point.
RATE_BUMP: Final = 1e-4

_PRICE_QUANT = Decimal("0.0001")
_DAYS_PER_YEAR = 365.25
_SECONDS_PER_YEAR = _DAYS_PER_YEAR * 86400


@dataclass(frozen=True, slots=True)
class CashDividend:
    """A known cash dividend the underlying pays.

    Attributes:
        ex_timestamp: When the underlying goes ex (Unix seconds). A holder of
            the underlying before this instant receives the dividend; from it
            on, the price is net of it.
        amount: Cash per unit of the underlying, in the currency it is priced
            in. Positive.

    Raises:
        OptionInputError: If the instant is not finite or the amount is not
            positive.
    """

    ex_timestamp: float
    amount: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.ex_timestamp, bool) or not math.isfinite(self.ex_timestamp):
            raise OptionInputError(
                f"CashDividend.ex_timestamp must be a finite instant, got {self.ex_timestamp!r}."
            )
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise OptionInputError(
                f"CashDividend.amount must be a finite Decimal, got {self.amount!r}."
            )
        if self.amount <= Decimal("0"):
            raise OptionInputError(
                f"CashDividend.amount must be positive, got {self.amount}; a dividend of "
                "nothing is no dividend, and a negative one is a capital call."
            )


@dataclass(frozen=True, slots=True)
class BinomialLattice:
    """The lattice a price is computed on, and the dividends it is computed with.

    Attributes:
        steps: Equal time steps from valuation to expiry, in ``[1, MAX_STEPS]``.
            Greeks need at least two.
        dividends: The underlying's known cash dividends, in any order.
            **Required**: a stock that pays none is ``()``, said out loud, for
            the reason :mod:`alphalab.options.carry` requires a carry -- a
            default of "pays nothing" is how an equity option comes to be
            priced without its dividend. Dividends going ex at or before the
            valuation instant, or after expiry, do not affect a price.

    Raises:
        OptionInputError: If ``steps`` is out of range or a dividend is not a
            :class:`CashDividend`.
    """

    steps: int
    dividends: tuple[CashDividend, ...]

    def __post_init__(self) -> None:
        if isinstance(self.steps, bool) or not isinstance(self.steps, int):
            raise OptionInputError(f"BinomialLattice.steps must be an int, got {self.steps!r}.")
        if not 1 <= self.steps <= MAX_STEPS:
            raise OptionInputError(
                f"BinomialLattice.steps is {self.steps}; it must lie in [1, {MAX_STEPS}]."
            )
        if not isinstance(self.dividends, tuple):
            raise OptionInputError(
                "BinomialLattice.dividends must be a tuple of CashDividend -- () for none."
            )
        for dividend in self.dividends:
            if not isinstance(dividend, CashDividend):
                raise OptionInputError(
                    f"BinomialLattice.dividends holds {dividend!r}, which is not a CashDividend."
                )
        ordered = tuple(sorted(self.dividends, key=lambda d: (d.ex_timestamp, d.amount)))
        object.__setattr__(self, "dividends", ordered)

    @property
    def assumptions(self) -> ModelAssumptions:
        """What a price on this lattice assumes, steps included."""

        return ModelAssumptions(
            model=PricingModel.BINOMIAL_CRR,
            year_basis_days=_DAYS_PER_YEAR,
            prices_early_exercise=True,
            models_dividends=True,
            models_volatility_smile=False,
            note=(
                f"Cox-Ross-Rubinstein lattice of {self.steps} steps on a lognormal underlying "
                "with a stated continuous carry and escrowed cash dividends; an American "
                "contract may exercise at every node. One volatility applies at every strike."
            ),
            models_discrete_dividends=True,
            steps=self.steps,
        )

    @property
    def identity(self) -> str:
        """A stable rendering: ``CRR/n=500/div=1700000000.0:0.5,...``."""

        rendered = ",".join(f"{d.ex_timestamp!r}:{d.amount}" for d in self.dividends)
        return f"CRR/n={self.steps}/div={rendered or 'none'}"


# --------------------------------------------------------------------------- #
# The lattice
# --------------------------------------------------------------------------- #


def _dividend_offsets(
    lattice: BinomialLattice, valuation_timestamp: float, years: float
) -> tuple[tuple[float, float], ...]:
    """``(years from valuation, amount)`` for each dividend paid before expiry."""

    offsets: list[tuple[float, float]] = []
    for dividend in lattice.dividends:
        offset = (dividend.ex_timestamp - valuation_timestamp) / _SECONDS_PER_YEAR
        if 0.0 < offset <= years:
            offsets.append((offset, float(dividend.amount)))
    return tuple(offsets)


def _require_carry(carry: Carry, dividends: tuple[tuple[float, float], ...]) -> None:
    if not isinstance(carry, Carry):
        raise OptionInputError(
            f"carry must be a Carry -- dividend_yield(q), foreign_rate(r_f) or "
            f"FUTURES_CARRY -- got {carry!r}."
        )
    if dividends and carry.kind is not CarryKind.DIVIDEND_YIELD:
        raise OptionInputError(
            f"A {carry.kind.name} underlying pays no cash dividend; a cash dividend is paid "
            "by a stock or an index, whose carry is a dividend_yield."
        )


@dataclass(frozen=True, slots=True)
class _Layers:
    """The root value and what the Greeks read from the first two layers."""

    value: float
    first: tuple[tuple[float, float], ...]  # (stock, value) at step 1, down then up
    second: tuple[tuple[float, float], ...]  # (stock, value) at step 2, down, middle, up
    step_years: float


def _roll(
    *,
    call: bool,
    american: bool,
    spot: float,
    strike: float,
    rate: float,
    carry_rate: float,
    volatility: float,
    years: float,
    steps: int,
    dividends: tuple[tuple[float, float], ...],
) -> _Layers:
    """Backward induction over the lattice; the first two layers are kept for Greeks."""

    if volatility <= 0.0 or not math.isfinite(volatility):
        raise OptionInputError(f"volatility must be positive and finite, got {volatility}.")
    dt = years / steps
    up = math.exp(volatility * math.sqrt(dt))
    down = 1.0 / up
    probability = (math.exp(carry_rate * dt) - down) / (up - down)
    if not 0.0 < probability < 1.0:
        needed = math.ceil(years * (carry_rate / volatility) ** 2) + 1
        raise OptionPricingError(
            f"A {steps}-step lattice at volatility {volatility:g} and carry {carry_rate:g} has an "
            f"up-move probability of {probability:g}, which is not a probability: the lattice "
            f"needs |b| sqrt(dt) < sigma. {needed} steps or more admit it."
        )
    discount = math.exp(-rate * dt)
    weight_up = discount * probability
    weight_down = discount * (1.0 - probability)

    def remaining(at: float) -> float:
        return sum(
            amount * math.exp(-rate * (offset - at)) for offset, amount in dividends if offset > at
        )

    escrowed = spot - remaining(0.0)
    if escrowed <= 0.0:
        raise OptionInputError(
            f"The dividends before expiry are worth {spot - escrowed:g} today, at or above the "
            f"spot of {spot:g}; the underlying net of them is worth nothing."
        )

    sign = 1.0 if call else -1.0
    growth = [1.0]
    squared = up * up
    for _ in range(steps):
        growth.append(growth[-1] * squared)
    lowest = escrowed * down**steps
    values = [max(sign * (lowest * g - strike), 0.0) for g in growth]

    kept: dict[int, tuple[tuple[float, float], ...]] = {}
    for layer in range(steps - 1, -1, -1):
        values = [
            weight_up * above + weight_down * below
            for above, below in zip(values[1:], values[:-1], strict=True)
        ]
        bottom = escrowed * down**layer
        added = remaining(layer * dt)
        if american:
            values = [
                max(held, sign * (bottom * g + added - strike))
                for held, g in zip(values, growth, strict=False)
            ]
        if layer in (1, 2):
            kept[layer] = tuple(
                (bottom * g + added, value) for value, g in zip(values, growth, strict=False)
            )
    return _Layers(value=values[0], first=kept.get(1, ()), second=kept.get(2, ()), step_years=dt)


def _lattice_value(
    contract: OptionContract,
    spot: float,
    volatility: float,
    risk_free_rate: float,
    years: float,
    carry: Carry,
    steps: int,
    dividends: tuple[tuple[float, float], ...],
) -> _Layers:
    return _roll(
        call=contract.option_type is OptionType.CALL,
        american=contract.style is ExerciseStyle.AMERICAN,
        spot=spot,
        strike=float(contract.strike),
        rate=risk_free_rate,
        carry_rate=carry.cost_of_carry(risk_free_rate),
        volatility=volatility,
        years=years,
        steps=steps,
        dividends=dividends,
    )


def _require_lattice(lattice: BinomialLattice) -> None:
    if not isinstance(lattice, BinomialLattice):
        raise OptionInputError(f"lattice must be a BinomialLattice, got {lattice!r}.")


def binomial_value(
    contract: OptionContract,
    spot: float,
    volatility: float,
    risk_free_rate: float,
    valuation_timestamp: float,
    *,
    carry: Carry,
    lattice: BinomialLattice,
) -> float:
    """The lattice value as a ``float``, before any rounding.

    :func:`binomial_price` is this quantized to four decimal places and floored
    at zero, and the implied-volatility solver inverts *this*, for the reason
    :func:`~alphalab.options.pricing.black_scholes_value` gives. Unlike that
    function it takes the valuation instant rather than a maturity, because the
    dividends are dated.

    Raises:
        OptionInputError: If the spot or volatility is not positive, the carry
            is not a :class:`~alphalab.options.carry.Carry` or names an
            underlying that pays no cash dividend while dividends are stated,
            the dividends are worth the whole spot, or the contract has
            expired.
        OptionPricingError: If the lattice has too few steps for the carry and
            volatility to give a probability.
    """

    _require_lattice(lattice)
    if not spot > 0.0:
        raise OptionInputError(f"spot must be positive, got {spot}.")
    years = time_to_expiry_years(contract, valuation_timestamp)
    dividends = _dividend_offsets(lattice, valuation_timestamp, years)
    _require_carry(carry, dividends)
    return _lattice_value(
        contract, spot, volatility, risk_free_rate, years, carry, lattice.steps, dividends
    ).value


def binomial_price(
    contract: OptionContract,
    spot: Decimal,
    volatility: float,
    risk_free_rate: float,
    valuation_timestamp: float,
    *,
    carry: Carry,
    lattice: BinomialLattice,
) -> Decimal:
    """The price of one unit of the contract's underlying, on ``lattice``.

    Multiply by ``contract.multiplier`` for a whole contract's premium. Rounded
    to four decimal places, half to even, as
    :func:`~alphalab.options.pricing.black_scholes_price` is.

    Raises:
        OptionInputError: As :func:`binomial_value`.
        OptionPricingError: As :func:`binomial_value`.
    """

    if spot <= Decimal("0"):
        raise OptionInputError(f"spot must be positive, got {spot}.")
    value = binomial_value(
        contract,
        float(spot),
        volatility,
        risk_free_rate,
        valuation_timestamp,
        carry=carry,
        lattice=lattice,
    )
    return Decimal(str(max(value, 0.0))).quantize(_PRICE_QUANT, rounding=ROUND_HALF_EVEN)


def binomial_greeks(
    contract: OptionContract,
    spot: Decimal,
    volatility: float,
    risk_free_rate: float,
    valuation_timestamp: float,
    *,
    carry: Carry,
    lattice: BinomialLattice,
) -> Greeks:
    """The lattice's own sensitivities, in the units :class:`Greeks` states.

    Delta and gamma are read from the first two layers of the lattice the price
    is computed on, theta from the middle node two steps on (the same escrowed
    price, ``2 dt`` later) and stated per calendar day over 365.25. Vega and rho
    are central differences of :data:`VOLATILITY_BUMP` and :data:`RATE_BUMP`;
    rho moves the rate with the carry's yield held fixed, as
    :func:`~alphalab.options.pricing.black_scholes_greeks` does, so a futures
    contract's carry stays zero.

    Raises:
        OptionInputError: As :func:`binomial_value`, and if the lattice has
            fewer than two steps.
        OptionPricingError: As :func:`binomial_value`, including at a bumped
            volatility.
    """

    _require_lattice(lattice)
    if lattice.steps < 2:
        raise OptionInputError(
            f"Greeks read a lattice's first two layers; a {lattice.steps}-step lattice has one."
        )
    if spot <= Decimal("0"):
        raise OptionInputError(f"spot must be positive, got {spot}.")
    if volatility <= VOLATILITY_BUMP:
        raise OptionInputError(
            f"volatility {volatility} is within the vega step {VOLATILITY_BUMP} of zero."
        )
    years = time_to_expiry_years(contract, valuation_timestamp)
    dividends = _dividend_offsets(lattice, valuation_timestamp, years)
    _require_carry(carry, dividends)
    spot_f = float(spot)

    def at(sigma: float, rate: float) -> _Layers:
        return _lattice_value(contract, spot_f, sigma, rate, years, carry, lattice.steps, dividends)

    base = at(volatility, risk_free_rate)
    (s_down, v_down), (s_up, v_up) = base.first
    delta = (v_up - v_down) / (s_up - s_down)
    (s_dd, v_dd), (s_ud, v_ud), (s_uu, v_uu) = base.second
    gamma = ((v_uu - v_ud) / (s_uu - s_ud) - (v_ud - v_dd) / (s_ud - s_dd)) / (0.5 * (s_uu - s_dd))
    theta_per_year = (v_ud - base.value) / (2.0 * base.step_years)
    vega = (
        at(volatility + VOLATILITY_BUMP, risk_free_rate).value
        - at(volatility - VOLATILITY_BUMP, risk_free_rate).value
    ) / (2.0 * VOLATILITY_BUMP)
    rho = (
        at(volatility, risk_free_rate + RATE_BUMP).value
        - at(volatility, risk_free_rate - RATE_BUMP).value
    ) / (2.0 * RATE_BUMP)
    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta_per_year / _DAYS_PER_YEAR,
        vega=vega,
        rho=rho,
    )


def _lattice_bounds(
    contract: OptionContract,
    spot: float,
    risk_free_rate: float,
    years: float,
    carry: Carry,
    dividends: tuple[tuple[float, float], ...],
) -> tuple[float, float]:
    """The no-arbitrage floor and ceiling of a price on the lattice.

    The floor is the European one on the underlying net of its dividends, and
    for an American contract also what exercising now pays; the ceiling is what
    the price approaches as volatility grows without bound -- the carried,
    escrowed spot for a call (the spot itself for an American one), the
    discounted strike for a put (the strike itself for an American one).
    """

    strike = float(contract.strike)
    present = sum(amount * math.exp(-risk_free_rate * offset) for offset, amount in dividends)
    carried = (spot - present) * math.exp(
        (carry.cost_of_carry(risk_free_rate) - risk_free_rate) * years
    )
    discounted = strike * math.exp(-risk_free_rate * years)
    american = contract.style is ExerciseStyle.AMERICAN
    if contract.option_type is OptionType.CALL:
        floor = max(carried - discounted, 0.0)
        if american:
            floor = max(floor, spot - strike)
        return floor, (spot if american else carried)
    floor = max(discounted - carried, 0.0)
    if american:
        floor = max(floor, strike - spot)
    return floor, (strike if american else discounted)


def _lowest_volatility(risk_free_rate: float, years: float, carry: Carry, steps: int) -> float:
    """``|b| sqrt(dt)``: below it the lattice refuses, so a solver starts above it."""

    return abs(carry.cost_of_carry(risk_free_rate)) * math.sqrt(years / steps)


def _solver_inputs(
    contract: OptionContract,
    spot: float,
    valuation_timestamp: float,
    carry: Carry,
    lattice: BinomialLattice,
) -> tuple[tuple[tuple[float, float], ...], float]:
    """The dividend offsets and the maturity a solver needs, validated once."""

    _require_lattice(lattice)
    if not spot > 0.0:
        raise OptionInputError(f"spot must be positive, got {spot}.")
    years = time_to_expiry_years(contract, valuation_timestamp)
    dividends = _dividend_offsets(lattice, valuation_timestamp, years)
    _require_carry(carry, dividends)
    return dividends, years
