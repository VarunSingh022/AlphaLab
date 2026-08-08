"""Black-Scholes European option pricing and Greeks.

Uses only the standard library (`math.erf` for the normal CDF) since AlphaLab has
zero runtime dependencies -- no numpy or scipy. This is a European-style closed-form
model; it does not account for early exercise premium on American contracts. Callers
pricing American contracts should treat this as an approximation, consistent with
`OptionContract.style` being informational rather than affecting this model.
"""

import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from alphalab.options.contract import OptionContract
from alphalab.options.enums import OptionType
from alphalab.options.exceptions import OptionInputError
from alphalab.options.greeks import Greeks

_PRICE_QUANT = Decimal("0.0001")
_SECONDS_PER_YEAR = 365.25 * 86400
_DAYS_PER_YEAR = 365.0


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


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
    spot: float, strike: float, rate: float, volatility: float, years: float
) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility**2) * years) / (
        volatility * math.sqrt(years)
    )
    d2 = d1 - volatility * math.sqrt(years)
    return d1, d2


def _validate_pricing_inputs(spot: Decimal, volatility: float) -> None:
    if spot <= Decimal("0"):
        raise OptionInputError(f"spot must be positive, got {spot}.")
    if volatility <= 0.0:
        raise OptionInputError(f"volatility must be positive, got {volatility}.")


def black_scholes_price(
    contract: OptionContract,
    spot: Decimal,
    volatility: float,
    risk_free_rate: float,
    valuation_timestamp: float,
) -> Decimal:
    """Computes the Black-Scholes theoretical price of one contract's underlying share.

    Multiply the result by `contract.multiplier` for the total per-contract premium.

    Raises:
        OptionInputError: If spot or volatility are not positive, or the contract
            has already expired as of valuation_timestamp.
    """
    _validate_pricing_inputs(spot, volatility)
    years = time_to_expiry_years(contract, valuation_timestamp)

    spot_f, strike_f = float(spot), float(contract.strike)
    d1, d2 = _d1_d2(spot_f, strike_f, risk_free_rate, volatility, years)
    discount = math.exp(-risk_free_rate * years)

    if contract.option_type is OptionType.CALL:
        price = spot_f * _norm_cdf(d1) - strike_f * discount * _norm_cdf(d2)
    else:
        price = strike_f * discount * _norm_cdf(-d2) - spot_f * _norm_cdf(-d1)

    return Decimal(str(max(price, 0.0))).quantize(_PRICE_QUANT, rounding=ROUND_HALF_EVEN)


def black_scholes_greeks(
    contract: OptionContract,
    spot: Decimal,
    volatility: float,
    risk_free_rate: float,
    valuation_timestamp: float,
) -> Greeks:
    """Computes Black-Scholes Greeks for one contract's underlying share.

    theta is expressed per calendar day; delta, gamma, vega, and rho are expressed
    per unit (i.e. per $1 of underlying, per 1.0 of volatility, per 1.0 of rate).

    Raises:
        OptionInputError: If spot or volatility are not positive, or the contract
            has already expired as of valuation_timestamp.
    """
    _validate_pricing_inputs(spot, volatility)
    years = time_to_expiry_years(contract, valuation_timestamp)

    spot_f, strike_f = float(spot), float(contract.strike)
    d1, d2 = _d1_d2(spot_f, strike_f, risk_free_rate, volatility, years)
    discount = math.exp(-risk_free_rate * years)
    pdf_d1 = _norm_pdf(d1)

    gamma = pdf_d1 / (spot_f * volatility * math.sqrt(years))
    vega = spot_f * pdf_d1 * math.sqrt(years)

    if contract.option_type is OptionType.CALL:
        delta = _norm_cdf(d1)
        theta_per_year = -(spot_f * pdf_d1 * volatility) / (
            2.0 * math.sqrt(years)
        ) - risk_free_rate * strike_f * discount * _norm_cdf(d2)
        rho = strike_f * years * discount * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_per_year = -(spot_f * pdf_d1 * volatility) / (
            2.0 * math.sqrt(years)
        ) + risk_free_rate * strike_f * discount * _norm_cdf(-d2)
        rho = -strike_f * years * discount * _norm_cdf(-d2)

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta_per_year / _DAYS_PER_YEAR,
        vega=vega,
        rho=rho,
    )
