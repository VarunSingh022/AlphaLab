"""Generalized Black-Scholes-Merton European option pricing and Greeks.

Uses only the standard library (`math.erf` for the normal CDF) since AlphaLab has
zero runtime dependencies -- no numpy or scipy. This is a European-style closed-form
model; it does not account for early exercise premium on American contracts, and
`OptionContract.style` does not affect it. An American contract, or a stock with
known cash dividends, is priced on the lattice in :mod:`alphalab.options.binomial`
(since v3.13, ledger NUM-006); this closed form priced as though it were European
is an approximation that understates a deep in-the-money American put.

Every function takes the underlying's :class:`~alphalab.options.carry.Carry` --
a dividend yield, a foreign rate or a futures contract's zero carry -- as a
required keyword, since v3.11 (ledger NUM-005). ``carry=dividend_yield(0.0)``
reproduces the v3.10 figures exactly: with ``b = r`` the generalized formula is
the one this module computed before.
"""

import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from alphalab.options.carry import Carry
from alphalab.options.contract import OptionContract
from alphalab.options.enums import OptionType
from alphalab.options.exceptions import OptionInputError
from alphalab.options.greeks import Greeks

_PRICE_QUANT = Decimal("0.0001")
#: The one year basis: a maturity is seconds over a 365.25-day year, and a theta
#: per day is the year's theta over the same 365.25 days. Until v3.12 theta was
#: divided by 365 while maturities used 365.25, so the two disagreed about how
#: long a year is (ledger NUM-004). ``BLACK_SCHOLES_MERTON.year_basis_days``
#: records it, and a test holds the two together.
_DAYS_PER_YEAR = 365.25
_SECONDS_PER_YEAR = _DAYS_PER_YEAR * 86400


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function.

    Through ``erfc``, which keeps its relative precision in the lower tail. Until
    v3.12 it was ``0.5 * (1 + erf(x / sqrt 2))``, where ``1 + erf`` cancels to
    zero below about ``x = -8.3``: a deep out-of-the-money value read as exactly
    nothing (ledger NUM-004).
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def time_to_expiry_years(contract: OptionContract, valuation_timestamp: float) -> float:
    """Computes time to expiry in years, given a valuation timestamp.

    Raises:
        OptionInputError: If the contract has already expired as of
            valuation_timestamp.
    """
    seconds_remaining = contract.expiry - valuation_timestamp
    if seconds_remaining <= 0:
        expiry_date = datetime.fromtimestamp(contract.expiry, tz=UTC).isoformat()
        raise OptionInputError(f"Contract expired at {expiry_date}; cannot price.")
    return seconds_remaining / _SECONDS_PER_YEAR


def _d1_d2(
    spot: float, strike: float, carry_rate: float, volatility: float, years: float
) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (carry_rate + 0.5 * volatility**2) * years) / (
        volatility * math.sqrt(years)
    )
    d2 = d1 - volatility * math.sqrt(years)
    return d1, d2


def black_scholes_value(
    contract: OptionContract,
    spot: float,
    volatility: float,
    risk_free_rate: float,
    years: float,
    *,
    carry: Carry,
) -> float:
    """The generalized Black-Scholes-Merton value as a ``float``, before any rounding.

    :func:`black_scholes_price` is this, quantized to four decimal places and
    floored at zero, and :mod:`alphalab.options.implied` inverts *this* rather
    than the quantized figure -- a step function is not monotonic at the step,
    and a bisection on one converges to the edge of a tick rather than to a
    volatility.

    It is public so that the identity is checkable rather than asserted:
    ``tests/unit/options/test_implied.py`` requires the rounded form of this to
    equal ``black_scholes_price`` on the same inputs, which is what keeps the
    solver and the pricer one formula.

    Takes ``years`` directly rather than a valuation timestamp, because the
    solver evaluates it thousands of times at one maturity and recomputing the
    time to expiry per evaluation would make the result depend on how many
    iterations it took.
    """

    strike_f = float(contract.strike)
    carry_rate = carry.cost_of_carry(risk_free_rate)
    d1, d2 = _d1_d2(spot, strike_f, carry_rate, volatility, years)
    discount = math.exp(-risk_free_rate * years)
    carried = spot * math.exp((carry_rate - risk_free_rate) * years)
    if contract.option_type is OptionType.CALL:
        return carried * _norm_cdf(d1) - strike_f * discount * _norm_cdf(d2)
    return strike_f * discount * _norm_cdf(-d2) - carried * _norm_cdf(-d1)


def _validate_pricing_inputs(spot: Decimal, volatility: float, carry: Carry) -> None:
    if spot <= Decimal("0"):
        raise OptionInputError(f"spot must be positive, got {spot}.")
    if volatility <= 0.0:
        raise OptionInputError(f"volatility must be positive, got {volatility}.")
    if not isinstance(carry, Carry):
        raise OptionInputError(
            f"carry must be a Carry -- dividend_yield(q), foreign_rate(r_f) or "
            f"FUTURES_CARRY -- got {carry!r}."
        )


def black_scholes_price(
    contract: OptionContract,
    spot: Decimal,
    volatility: float,
    risk_free_rate: float,
    valuation_timestamp: float,
    *,
    carry: Carry,
) -> Decimal:
    """Computes the theoretical price of one contract's underlying share.

    Multiply the result by `contract.multiplier` for the total per-contract premium.

    Args:
        carry: What holding the underlying earns or costs. Required: a
            dividend-free stock is ``dividend_yield(0.0)``, said out loud.

    Raises:
        OptionInputError: If spot or volatility are not positive, the carry is
            not a :class:`~alphalab.options.carry.Carry`, or the contract has
            already expired as of valuation_timestamp.
    """
    _validate_pricing_inputs(spot, volatility, carry)
    years = time_to_expiry_years(contract, valuation_timestamp)
    price = black_scholes_value(
        contract, float(spot), volatility, risk_free_rate, years, carry=carry
    )
    return Decimal(str(max(price, 0.0))).quantize(_PRICE_QUANT, rounding=ROUND_HALF_EVEN)


def black_scholes_greeks(
    contract: OptionContract,
    spot: Decimal,
    volatility: float,
    risk_free_rate: float,
    valuation_timestamp: float,
    *,
    carry: Carry,
) -> Greeks:
    """Computes generalized Black-Scholes-Merton Greeks for one contract's underlying share.

    theta is expressed per calendar day; delta, gamma, vega, and rho are expressed
    per unit (i.e. per $1 of underlying, per 1.0 of volatility, per 1.0 of rate).

    Rho is the sensitivity to the domestic rate with the carry's *yield* held
    fixed -- a dividend yield or a foreign rate stays put while the rate moves --
    and for a futures contract, whose carry is zero at any rate, it is ``-T``
    times the value.

    Raises:
        OptionInputError: If spot or volatility are not positive, the carry is
            not a :class:`~alphalab.options.carry.Carry`, or the contract has
            already expired as of valuation_timestamp.
    """
    _validate_pricing_inputs(spot, volatility, carry)
    years = time_to_expiry_years(contract, valuation_timestamp)

    spot_f, strike_f = float(spot), float(contract.strike)
    carry_rate = carry.cost_of_carry(risk_free_rate)
    d1, d2 = _d1_d2(spot_f, strike_f, carry_rate, volatility, years)
    discount = math.exp(-risk_free_rate * years)
    carry_discount = math.exp((carry_rate - risk_free_rate) * years)
    pdf_d1 = _norm_pdf(d1)

    gamma = carry_discount * pdf_d1 / (spot_f * volatility * math.sqrt(years))
    vega = spot_f * carry_discount * pdf_d1 * math.sqrt(years)
    decay = -(spot_f * carry_discount * pdf_d1 * volatility) / (2.0 * math.sqrt(years))
    yield_drag = (carry_rate - risk_free_rate) * spot_f * carry_discount
    strike_carry = risk_free_rate * strike_f * discount

    if contract.option_type is OptionType.CALL:
        delta = carry_discount * _norm_cdf(d1)
        theta_per_year = decay - yield_drag * _norm_cdf(d1) - strike_carry * _norm_cdf(d2)
        rho = strike_f * years * discount * _norm_cdf(d2)
    else:
        delta = carry_discount * (_norm_cdf(d1) - 1.0)
        theta_per_year = decay + yield_drag * _norm_cdf(-d1) + strike_carry * _norm_cdf(-d2)
        rho = -strike_f * years * discount * _norm_cdf(-d2)

    if not carry.moves_with_rate:
        rho = -years * black_scholes_value(
            contract, spot_f, volatility, risk_free_rate, years, carry=carry
        )

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta_per_year / _DAYS_PER_YEAR,
        vega=vega,
        rho=rho,
    )
