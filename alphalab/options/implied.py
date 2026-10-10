"""Implied volatility: the inversion, and the four ways it has no answer.

Given a price, find the volatility that reproduces it. The Black-Scholes value
is strictly increasing in volatility, so when an answer exists it is unique and
a bisection finds it -- which is the easy half.

The hard half is that a quoted price frequently has **no** answer, and the
failure is quiet. A price below the no-arbitrage floor, a price above the
ceiling, an expiring at-the-money contract whose vega has collapsed, a
degenerate strike: each of those makes the inversion undefined, and each is
routine in a real chain. Returning a number anyway -- a clamp at 0.001, a
fallback at "20%", the last iterate of a solver that never converged -- produces
a volatility surface with fabricated points in exactly the corners a trader
looks at.

So every one of them raises :class:`ImpliedVolatilityError`, naming which it
was.

The five refusals
-----------------

=========================== ===============================================
At or below the floor       ``max(S e^{(b-r)T} - K e^{-rT}, 0)`` for a call.
                            No positive volatility prices this low;
                            volatility only adds value.
At or above the ceiling     ``S e^{(b-r)T}`` for a call, ``K e^{-rT}`` for a
                            put. The limit as volatility grows without bound.
Not reached by              The ceiling is the limit at *infinite*
:data:`MAX_VOLATILITY`      volatility; a price the model does not reach at
                            1000% annualized is past anything a listed market
                            quotes. This fires first for a contract whose
                            reachable band has collapsed near expiry.
Vega below the threshold    The price barely moves with volatility, so the
                            inversion is not numerically identifiable: a
                            half-tick of price noise moves the answer by
                            whole volatility points. A far strike a day from
                            expiry quoted at a fraction of a cent.
Did not converge            The bracket did not close within
                            :data:`MAX_ITERATIONS`. Reported rather than
                            returning the last iterate.
=========================== ===============================================

One formula, inverted
---------------------

The solver evaluates :func:`alphalab.options.pricing.black_scholes_value` --
the same expression :func:`~alphalab.options.pricing.black_scholes_price`
rounds and returns, under the same :class:`~alphalab.options.carry.Carry`. It is
not a second implementation of Black-Scholes, and a test requires the two to
agree on the same inputs. A volatility implied under one carry is only
meaningful under that carry, so the result records it.

Or on a lattice
---------------

Given a :class:`~alphalab.options.binomial.BinomialLattice` (v3.13, ledger
NUM-006), the solver inverts :func:`~alphalab.options.binomial.binomial_value`
instead: an American contract's quote is read with its early-exercise premium,
and a stock's cash dividends are stated rather than folded into a yield. The
same refusals apply with the lattice's own bounds -- the floor includes what
exercising now pays -- and one more: a lattice gives a probability only above
``|b| sqrt(dt)``, so a quote that needs a lower volatility is refused with the
step count that would reach it. The result records the lattice, whose
assumptions name its steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from alphalab.options.binomial import (
    VOLATILITY_BUMP,
    BinomialLattice,
    _lattice_bounds,
    _lattice_value,
    _lowest_volatility,
    _solver_inputs,
)
from alphalab.options.carry import Carry
from alphalab.options.contract import OptionContract
from alphalab.options.enums import OptionType
from alphalab.options.exceptions import OptionInputError, OptionPricingError
from alphalab.options.greeks import Greeks
from alphalab.options.model import BLACK_SCHOLES_MERTON, ModelAssumptions
from alphalab.options.pricing import (
    _require_market_inputs,
    black_scholes_greeks,
    black_scholes_value,
    time_to_expiry_years,
)

__all__ = [
    "MAX_ITERATIONS",
    "MAX_VOLATILITY",
    "MIN_IDENTIFIABLE_VEGA",
    "MIN_VOLATILITY",
    "PRICE_TOLERANCE",
    "ImpliedVolatility",
    "ImpliedVolatilityError",
    "implied_volatility",
]

#: Lowest volatility the bracket starts at. Not zero: at exactly zero the value
#: is the discounted intrinsic and ``d1`` divides by zero.
MIN_VOLATILITY: Final = 1e-9

#: Highest volatility the bracket will expand to. 1000% annualized is far past
#: anything a listed market quotes; a price needing more than this is a price
#: the model cannot reach, which is a refusal rather than a wider search.
MAX_VOLATILITY: Final = 10.0

#: Bisection steps before the solver gives up. 200 halvings of the bracket is
#: about 1e-60 of its width -- far more than double precision can represent --
#: so exhausting it means the bracket never contained the answer, not that the
#: search was too short.
MAX_ITERATIONS: Final = 200

#: How close the re-priced value must come to the target, in price units.
PRICE_TOLERANCE: Final = 1e-10

#: Below this vega, the inversion is not identifiable.
#:
#: Vega is the price change per 1.0 of volatility. At 1e-8, a whole volatility
#: point moves the price by 1e-10 -- less than the last bit of a double, and
#: orders of magnitude below any real quote increment. An answer here is
#: determined by floating-point noise rather than by the price.
MIN_IDENTIFIABLE_VEGA: Final = 1e-8


class ImpliedVolatilityError(OptionPricingError):
    """Raised when no volatility reproduces the observed price.

    A subclass of :class:`~alphalab.options.exceptions.OptionPricingError` --
    which already means "a pricing computation cannot produce a defined result"
    -- rather than a new root, so a caller catching the existing exception keeps
    catching this one.
    """


@dataclass(frozen=True, slots=True)
class ImpliedVolatility:
    """A volatility that reproduces an observed price, and what it assumed.

    Attributes:
        value: Annualized volatility as a decimal fraction, e.g. ``0.25`` for
            25%. Not a percentage, matching
            :attr:`alphalab.options.volatility_surface.VolPoint.implied_vol`.
        market_price: The per-unit price that was inverted. Per unit of the
            underlying, not per contract -- the same convention
            :func:`~alphalab.options.pricing.black_scholes_price` returns, and
            dividing a per-contract premium by the multiplier is the caller's
            step.
        repriced: What :func:`~alphalab.options.pricing.black_scholes_value`
            returns at :attr:`value`. Kept so the fit can be checked rather than
            trusted.
        vega: Price sensitivity to volatility at the solution. A small vega
            means a well-fitted number that a half-tick of price noise would
            move a long way, and it is reported so that can be seen.
        iterations: Bisection steps taken. Deterministic for given inputs.
        assumptions: The model the inversion was performed under. An implied
            volatility is only meaningful against the model that implied it.
        carry: The underlying's carry the inversion assumed -- an index option's
            volatility implied without its dividend yield is a different number.
        lattice: The lattice the inversion ran on, with its steps and cash
            dividends, or ``None`` for the closed form.
    """

    value: float
    market_price: float
    repriced: float
    vega: float
    iterations: int
    assumptions: ModelAssumptions
    carry: Carry
    lattice: BinomialLattice | None = None

    @property
    def residual(self) -> float:
        """``repriced - market_price``. Zero to within :data:`PRICE_TOLERANCE`."""

        return self.repriced - self.market_price


def _bounds(
    contract: OptionContract, spot: float, rate: float, years: float, carry: Carry
) -> tuple[float, float]:
    """The no-arbitrage floor and ceiling of a European price under ``carry``."""

    strike = float(contract.strike)
    discounted_strike = strike * math.exp(-rate * years)
    carried_spot = spot * math.exp((carry.cost_of_carry(rate) - rate) * years)
    if contract.option_type is OptionType.CALL:
        return max(carried_spot - discounted_strike, 0.0), carried_spot
    return max(discounted_strike - carried_spot, 0.0), discounted_strike


def implied_volatility(
    contract: OptionContract,
    market_price: Decimal,
    spot: Decimal,
    risk_free_rate: float,
    valuation_timestamp: float,
    *,
    carry: Carry,
    lattice: BinomialLattice | None = None,
) -> ImpliedVolatility:
    """The volatility under which the model reproduces ``market_price``.

    The model is Black-Scholes-Merton unless ``lattice`` is given, and then the
    lattice, with early exercise wherever the contract's style allows it.

    Args:
        contract: The contract quoted.
        market_price: Observed price per unit of the underlying. For a contract
            quoted per contract, divide by ``contract.multiplier`` first --
            doing it here would require assuming which of the two was meant.
        spot: The underlying's price at the same instant.
        risk_free_rate: Continuously-compounded annual rate as a decimal
            fraction. Required: a rate is a market observation and
            :mod:`alphalab.options` invents none.
        valuation_timestamp: When the price was observed.
        carry: The underlying's carry. Required, and recorded on the result.
        lattice: The lattice to invert on, or ``None`` for the closed form.

    Raises:
        OptionInputError: If the spot, the price or the rate is not a finite
            number, the contract has expired, the spot is not positive, or the
            carry is not a :class:`~alphalab.options.carry.Carry`
            (or, on a lattice, names an underlying that pays no cash dividend
            while dividends are stated).
        ImpliedVolatilityError: If the price is at or outside the no-arbitrage
            bounds, if vega at the solution is below
            :data:`MIN_IDENTIFIABLE_VEGA`, if the bracket did not close, or if
            the lattice cannot represent a volatility low enough to reach it.
    """

    _require_market_inputs(spot, risk_free_rate, market_price=market_price)
    if lattice is not None:
        return _implied_on_lattice(
            contract, market_price, spot, risk_free_rate, valuation_timestamp, carry, lattice
        )
    if spot <= Decimal("0"):
        raise OptionInputError(f"spot must be positive, got {spot}.")
    if not isinstance(carry, Carry):
        raise OptionInputError(f"carry must be a Carry, got {carry!r}.")
    years = time_to_expiry_years(contract, valuation_timestamp)
    spot_f, target = float(spot), float(market_price)

    floor, ceiling = _bounds(contract, spot_f, risk_free_rate, years, carry)
    if target <= floor:
        raise ImpliedVolatilityError(
            f"{contract.option_type.name} at strike {contract.strike} is quoted {target}, at "
            f"or below its no-arbitrage floor of {floor:.10g}. Volatility only adds value to "
            "an option, so no positive volatility prices it this low -- the quote, the spot "
            "or the rate disagree, and picking one to bend would fabricate the answer."
        )
    if target >= ceiling:
        raise ImpliedVolatilityError(
            f"{contract.option_type.name} at strike {contract.strike} is quoted {target}, at "
            f"or above its ceiling of {ceiling:.10g} -- the limit the model approaches as "
            "volatility grows without bound. No finite volatility reaches it."
        )

    def price_at(volatility: float) -> float:
        return black_scholes_value(contract, spot_f, volatility, risk_free_rate, years, carry=carry)

    low, high = MIN_VOLATILITY, MIN_VOLATILITY
    for _ in range(64):
        high = min(high * 4.0 if high > MIN_VOLATILITY else 0.01, MAX_VOLATILITY)
        if price_at(high) >= target:
            break
        if high >= MAX_VOLATILITY:
            raise ImpliedVolatilityError(
                f"{contract.option_type.name} at strike {contract.strike} is quoted {target}, "
                f"which the model does not reach at {MAX_VOLATILITY:.0%} volatility. That is "
                "past anything a listed market quotes, so the input is wrong rather than the "
                "search too narrow."
            )

    iterations = 0
    volatility = (low + high) / 2.0
    for iterations in range(1, MAX_ITERATIONS + 1):  # noqa: B007 - the count is reported
        volatility = (low + high) / 2.0
        value = price_at(volatility)
        if abs(value - target) <= PRICE_TOLERANCE or (high - low) <= 1e-15:
            break
        if value < target:
            low = volatility
        else:
            high = volatility
    else:
        raise ImpliedVolatilityError(
            f"The bracket for {contract.option_type.name} at strike {contract.strike} did not "
            f"close within {MAX_ITERATIONS} steps. Returning the last iterate would present a "
            "number the solver itself did not accept."
        )

    greeks: Greeks = black_scholes_greeks(
        contract, spot, volatility, risk_free_rate, valuation_timestamp, carry=carry
    )
    if abs(greeks.vega) < MIN_IDENTIFIABLE_VEGA:
        raise ImpliedVolatilityError(
            f"Vega at the solution is {greeks.vega:.3g}, below {MIN_IDENTIFIABLE_VEGA:g}: a "
            "whole volatility point barely moves this price, so the inversion is decided by "
            "floating-point noise rather than by the quote. A number here would look like a "
            "measurement and be one only of the arithmetic."
        )

    return ImpliedVolatility(
        value=volatility,
        market_price=target,
        repriced=price_at(volatility),
        vega=greeks.vega,
        iterations=iterations,
        assumptions=BLACK_SCHOLES_MERTON,
        carry=carry,
    )


def _implied_on_lattice(
    contract: OptionContract,
    market_price: Decimal,
    spot: Decimal,
    risk_free_rate: float,
    valuation_timestamp: float,
    carry: Carry,
    lattice: BinomialLattice,
) -> ImpliedVolatility:
    """:func:`implied_volatility` on a lattice: the same refusals, the lattice's bounds."""

    if spot <= Decimal("0"):
        raise OptionInputError(f"spot must be positive, got {spot}.")
    if not isinstance(carry, Carry):
        raise OptionInputError(f"carry must be a Carry, got {carry!r}.")
    spot_f, target = float(spot), float(market_price)
    dividends, years = _solver_inputs(contract, spot_f, valuation_timestamp, carry, lattice)
    name = f"{contract.style.name} {contract.option_type.name} at strike {contract.strike}"

    floor, ceiling = _lattice_bounds(contract, spot_f, risk_free_rate, years, carry, dividends)
    if target <= floor:
        raise ImpliedVolatilityError(
            f"{name} is quoted {target}, at or below its no-arbitrage floor of {floor:.10g} on "
            "this lattice -- what exercising or holding is worth at no volatility. No "
            "positive volatility prices it this low."
        )
    if target >= ceiling:
        raise ImpliedVolatilityError(
            f"{name} is quoted {target}, at or above its ceiling of {ceiling:.10g} -- the "
            "limit the price approaches as volatility grows without bound."
        )

    def price_at(volatility: float) -> float:
        return _lattice_value(
            contract, spot_f, volatility, risk_free_rate, years, carry, lattice.steps, dividends
        ).value

    lowest = max(
        MIN_VOLATILITY,
        _lowest_volatility(risk_free_rate, years, carry, lattice.steps) * (1.0 + 1e-9),
    )
    if lowest >= MAX_VOLATILITY:
        raise ImpliedVolatilityError(
            f"A {lattice.steps}-step lattice cannot represent any volatility up to "
            f"{MAX_VOLATILITY:.0%} at this carry; add steps."
        )
    if price_at(lowest) >= target:
        raise ImpliedVolatilityError(
            f"{name} is quoted {target}, at or below what the lattice prices at the lowest "
            f"volatility it can represent at this carry ({lowest:.6g}), where the price is "
            "what holding and exercising are worth with no volatility at all. Either the "
            "quote is below that floor -- exercising before a dividend can be worth more "
            "than exercising now -- and no volatility reproduces it, or it needs a "
            "volatility below what this many steps resolve; more steps lower that bound."
        )

    low, high = lowest, lowest
    for _ in range(64):
        high = min(max(high * 4.0, 0.01), MAX_VOLATILITY)
        if price_at(high) >= target:
            break
        if high >= MAX_VOLATILITY:
            raise ImpliedVolatilityError(
                f"{name} is quoted {target}, which the lattice does not reach at "
                f"{MAX_VOLATILITY:.0%} volatility. That is past anything a listed market "
                "quotes, so the input is wrong rather than the search too narrow."
            )
        low = high

    iterations = 0
    volatility = (low + high) / 2.0
    for iterations in range(1, MAX_ITERATIONS + 1):  # noqa: B007 - the count is reported
        volatility = (low + high) / 2.0
        value = price_at(volatility)
        if abs(value - target) <= PRICE_TOLERANCE or (high - low) <= 1e-15:
            break
        if value < target:
            low = volatility
        else:
            high = volatility
    else:
        raise ImpliedVolatilityError(
            f"The bracket for {name} did not close within {MAX_ITERATIONS} steps. Returning "
            "the last iterate would present a number the solver itself did not accept."
        )

    below = max(volatility - VOLATILITY_BUMP, lowest)
    above = volatility + VOLATILITY_BUMP
    vega = (price_at(above) - price_at(below)) / (above - below)
    if abs(vega) < MIN_IDENTIFIABLE_VEGA:
        raise ImpliedVolatilityError(
            f"Vega at the solution is {vega:.3g}, below {MIN_IDENTIFIABLE_VEGA:g}: a whole "
            "volatility point barely moves this price, so the inversion is decided by "
            "floating-point noise rather than by the quote."
        )

    return ImpliedVolatility(
        value=volatility,
        market_price=target,
        repriced=price_at(volatility),
        vega=vega,
        iterations=iterations,
        assumptions=lattice.assumptions,
        carry=carry,
        lattice=lattice,
    )
