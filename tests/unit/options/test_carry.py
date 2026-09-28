"""The generalized Black-Scholes-Merton pricer, under each carry (ledger NUM-005).

Reference values are Haug, *The Complete Guide to Option Pricing Formulas*
(2nd ed., ch. 1), which prints each to four decimal places. The remaining
tests check what a closed form owes whatever its inputs: put-call parity under
the carry, Greeks that are the derivatives of the value they describe, and an
implied volatility that inverts the price it came from under the same carry.
"""

import dataclasses
import math
from decimal import Decimal

import pytest

from alphalab.options import (
    FUTURES_CARRY,
    Carry,
    CarryKind,
    ExerciseStyle,
    OptionChain,
    OptionContract,
    OptionInputError,
    OptionType,
    black_scholes_greeks,
    black_scholes_price,
    black_scholes_value,
    dividend_yield,
    foreign_rate,
    implied_volatility,
    occ_symbol,
    surface_from_chain,
)

YEAR = 365.25 * 86400

CARRIES = (
    pytest.param(dividend_yield(0.0), id="no-yield"),
    pytest.param(dividend_yield(0.03), id="dividend-yield"),
    pytest.param(dividend_yield(-0.01), id="negative-yield"),
    pytest.param(foreign_rate(0.05), id="foreign-rate"),
    pytest.param(FUTURES_CARRY, id="futures"),
)


def _contract(
    option_type: OptionType, strike: str, years: float = 1.0, underlying: str = "IDX"
) -> OptionContract:
    return OptionContract(
        underlying_asset_id=underlying,
        strike=Decimal(strike),
        expiry=years * YEAR,
        option_type=option_type,
        style=ExerciseStyle.EUROPEAN,
        multiplier=100,
    )


# --------------------------------------------------------------------------- #
# Reference values
# --------------------------------------------------------------------------- #


def test_black_scholes_1973_reference() -> None:
    """Haug 1.1.1: S=60, K=65, T=0.25, r=8%, sigma=30% -> call 2.1334."""

    call = _contract(OptionType.CALL, "65", 0.25)
    value = black_scholes_value(call, 60.0, 0.30, 0.08, 0.25, carry=dividend_yield(0.0))
    assert round(value, 4) == 2.1334


def test_merton_1973_dividend_yield_reference() -> None:
    """Haug 1.1.2: an index put, S=100, K=95, T=0.5, r=10%, q=5%, sigma=20% -> 2.4648."""

    put = _contract(OptionType.PUT, "95", 0.5)
    value = black_scholes_value(put, 100.0, 0.20, 0.10, 0.5, carry=dividend_yield(0.05))
    assert round(value, 4) == 2.4648


def test_black_1976_futures_reference() -> None:
    """Haug 1.1.3: F=K=19, T=0.75, r=10%, sigma=28% -> call = put = 1.7011."""

    call = _contract(OptionType.CALL, "19", 0.75)
    put = _contract(OptionType.PUT, "19", 0.75)
    call_value = black_scholes_value(call, 19.0, 0.28, 0.10, 0.75, carry=FUTURES_CARRY)
    put_value = black_scholes_value(put, 19.0, 0.28, 0.10, 0.75, carry=FUTURES_CARRY)
    assert round(call_value, 4) == 1.7011
    assert round(put_value, 4) == 1.7011


def test_garman_kohlhagen_currency_reference() -> None:
    """Haug 1.1.6: S=1.56, K=1.60, T=0.5, r=6%, r_f=8%, sigma=12% -> call 0.0291."""

    call = _contract(OptionType.CALL, "1.60", 0.5, underlying="USDEUR")
    value = black_scholes_value(call, 1.56, 0.12, 0.06, 0.5, carry=foreign_rate(0.08))
    assert round(value, 4) == 0.0291


def test_a_zero_yield_reproduces_the_v310_formula_bit_for_bit() -> None:
    """``dividend_yield(0.0)`` is the formula v3.10 computed, to the last bit.

    The v3.10 closed form is restated here rather than imported, since it no
    longer exists in the library: with ``b = r`` the generalized formula's extra
    factor is ``exp(0.0) == 1.0`` and its extra theta term is ``0.0``, so the
    two agree exactly rather than to a tolerance.
    """

    def cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def v310(
        option_type: OptionType, spot: float, strike: float, sigma: float, r: float, t: float
    ) -> float:
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)
        discount = math.exp(-r * t)
        if option_type is OptionType.CALL:
            return spot * cdf(d1) - strike * discount * cdf(d2)
        return strike * discount * cdf(-d2) - spot * cdf(-d1)

    for option_type in OptionType:
        for strike in ("80", "100", "125.5"):
            for spot, sigma, rate, years in ((100.0, 0.2, 0.05, 1.0), (97.3, 0.45, -0.01, 0.1)):
                contract = _contract(option_type, strike, years)
                generalized = black_scholes_value(
                    contract, spot, sigma, rate, years, carry=dividend_yield(0.0)
                )
                assert generalized == v310(option_type, spot, float(strike), sigma, rate, years)


# --------------------------------------------------------------------------- #
# Put-call parity under the carry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("carry", CARRIES)
@pytest.mark.parametrize("strike", ["70", "100", "140"])
def test_put_call_parity_holds_under_every_carry(carry: Carry, strike: str) -> None:
    """``C - P = S e^((b-r)T) - K e^(-rT)``: the forward, discounted."""

    spot, sigma, rate, years = 100.0, 0.25, 0.04, 0.75
    call = black_scholes_value(
        _contract(OptionType.CALL, strike, years), spot, sigma, rate, years, carry=carry
    )
    put = black_scholes_value(
        _contract(OptionType.PUT, strike, years), spot, sigma, rate, years, carry=carry
    )
    forward_leg = spot * math.exp((carry.cost_of_carry(rate) - rate) * years)
    assert call - put == pytest.approx(
        forward_leg - float(strike) * math.exp(-rate * years), abs=1e-12
    )


def test_a_yield_lowers_a_call_and_raises_a_put() -> None:
    """A dividend the holder of the stock earns is one the option holder forgoes."""

    call = _contract(OptionType.CALL, "100")
    put = _contract(OptionType.PUT, "100")
    args = (100.0, 0.2, 0.05, 1.0)
    assert black_scholes_value(call, *args, carry=dividend_yield(0.03)) < black_scholes_value(
        call, *args, carry=dividend_yield(0.0)
    )
    assert black_scholes_value(put, *args, carry=dividend_yield(0.03)) > black_scholes_value(
        put, *args, carry=dividend_yield(0.0)
    )


# --------------------------------------------------------------------------- #
# Greeks are the derivatives of the value
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("carry", CARRIES)
@pytest.mark.parametrize("option_type", list(OptionType))
@pytest.mark.parametrize("strike", ["85", "100", "120"])
def test_greeks_match_central_differences(
    carry: Carry, option_type: OptionType, strike: str
) -> None:
    spot, sigma, rate, years = 100.0, 0.3, 0.03, 0.8
    contract = _contract(option_type, strike, years)
    greeks = black_scholes_greeks(contract, Decimal("100"), sigma, rate, 0.0, carry=carry)

    def value(s: float = spot, v: float = sigma, r: float = rate, t: float = years) -> float:
        return black_scholes_value(contract, s, v, r, t, carry=carry)

    ds, dv, dr, dt = 1e-3, 1e-5, 1e-6, 1e-6
    delta = (value(s=spot + ds) - value(s=spot - ds)) / (2 * ds)
    gamma = (value(s=spot + ds) - 2 * value() + value(s=spot - ds)) / ds**2
    vega = (value(v=sigma + dv) - value(v=sigma - dv)) / (2 * dv)
    rho = (value(r=rate + dr) - value(r=rate - dr)) / (2 * dr)
    theta_per_day = -(value(t=years + dt) - value(t=years - dt)) / (2 * dt) / 365.0

    assert greeks.delta == pytest.approx(delta, abs=1e-7)
    assert greeks.gamma == pytest.approx(gamma, rel=1e-4)
    assert greeks.vega == pytest.approx(vega, rel=1e-6)
    assert greeks.rho == pytest.approx(rho, rel=1e-5, abs=1e-7)
    assert greeks.theta == pytest.approx(theta_per_day, rel=1e-5, abs=1e-8)


def test_futures_rho_is_minus_maturity_times_value() -> None:
    """Under Black-76 the rate only discounts: ``rho = -T V`` for a call and a put."""

    for option_type in OptionType:
        contract = _contract(option_type, "100", 0.5)
        greeks = black_scholes_greeks(
            contract, Decimal("102"), 0.25, 0.05, 0.0, carry=FUTURES_CARRY
        )
        value = black_scholes_value(contract, 102.0, 0.25, 0.05, 0.5, carry=FUTURES_CARRY)
        assert greeks.rho == pytest.approx(-0.5 * value, rel=1e-15)


# --------------------------------------------------------------------------- #
# Implied volatility inverts under the same carry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("carry", CARRIES)
@pytest.mark.parametrize("option_type", list(OptionType))
def test_implied_volatility_round_trips_under_every_carry(
    carry: Carry, option_type: OptionType
) -> None:
    contract = _contract(option_type, "105", 0.6)
    price = black_scholes_price(contract, Decimal("100"), 0.32, 0.04, 0.0, carry=carry)
    implied = implied_volatility(contract, price, Decimal("100"), 0.04, 0.0, carry=carry)
    assert implied.carry == carry
    assert implied.value == pytest.approx(0.32, abs=5e-5)


def test_ignoring_a_dividend_yield_biases_the_implied_volatility() -> None:
    """What NUM-005 was: an index call inverted as if the index paid nothing."""

    contract = _contract(OptionType.CALL, "100")
    price = black_scholes_price(
        contract, Decimal("100"), 0.20, 0.05, 0.0, carry=dividend_yield(0.03)
    )
    stated = implied_volatility(
        contract, price, Decimal("100"), 0.05, 0.0, carry=dividend_yield(0.03)
    )
    ignored = implied_volatility(
        contract, price, Decimal("100"), 0.05, 0.0, carry=dividend_yield(0.0)
    )
    assert stated.value == pytest.approx(0.20, abs=5e-5)
    assert stated.value - ignored.value > 0.03


def test_the_surface_inverts_the_whole_chain_under_the_stated_carry() -> None:
    carry = foreign_rate(0.02)
    contracts = tuple(
        _contract(option_type, strike, years, underlying="EURUSD")
        for option_type in OptionType
        for strike in ("1.05", "1.10", "1.15")
        for years in (0.25, 1.0)
    )
    chain = OptionChain("EURUSD", 0.0, contracts)
    prices = {
        occ_symbol(contract): Decimal(
            repr(
                black_scholes_value(contract, 1.10, 0.09, 0.04, contract.expiry / YEAR, carry=carry)
            )
        )
        for contract in contracts
    }
    surface, refusals = surface_from_chain(chain, prices, Decimal("1.10"), 0.04, 0.0, carry=carry)
    assert refusals == ()
    assert len(surface.points) == len(contracts)
    assert all(point.implied_vol == pytest.approx(0.09, abs=1e-8) for point in surface.points)


# --------------------------------------------------------------------------- #
# The carry value itself
# --------------------------------------------------------------------------- #


def test_the_cost_of_carry_of_each_kind() -> None:
    assert dividend_yield(0.02).cost_of_carry(0.05) == pytest.approx(0.03)
    assert foreign_rate(0.06).cost_of_carry(0.08) == pytest.approx(0.02)
    assert FUTURES_CARRY.cost_of_carry(0.08) == 0.0
    assert FUTURES_CARRY.cost_of_carry(-0.5) == 0.0
    assert dividend_yield(0.02).moves_with_rate
    assert foreign_rate(0.06).moves_with_rate
    assert not FUTURES_CARRY.moves_with_rate


def test_a_carry_is_a_value() -> None:
    assert dividend_yield(0.02) == Carry(CarryKind.DIVIDEND_YIELD, 0.02)
    assert dividend_yield(0.02) != foreign_rate(0.02)
    assert hash(dividend_yield(0.02)) == hash(Carry(CarryKind.DIVIDEND_YIELD, 0.02))
    assert dividend_yield(1).yield_rate == 1.0
    assert isinstance(dividend_yield(1).yield_rate, float)
    assert dividend_yield(0.015).identity == "DIVIDEND_YIELD:0.015"
    assert FUTURES_CARRY.identity == "FUTURES:0.0"
    with pytest.raises(dataclasses.FrozenInstanceError):
        dividend_yield(0.02).yield_rate = 0.03  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kind", "yield_rate", "message"),
    [
        (CarryKind.DIVIDEND_YIELD, math.nan, "finite"),
        (CarryKind.FOREIGN_RATE, math.inf, "finite"),
        (CarryKind.DIVIDEND_YIELD, True, "number"),
        (CarryKind.DIVIDEND_YIELD, "0.02", "number"),
        (CarryKind.DIVIDEND_YIELD, Decimal("0.02"), "number"),
        (CarryKind.FUTURES, 0.01, "no yield"),
        ("DIVIDEND_YIELD", 0.02, "CarryKind"),
    ],
)
def test_a_malformed_carry_is_refused(kind: object, yield_rate: object, message: str) -> None:
    with pytest.raises(OptionInputError, match=message):
        Carry(kind, yield_rate)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "pricer", [black_scholes_price, black_scholes_greeks], ids=["price", "greeks"]
)
def test_the_pricer_refuses_a_bare_number_for_a_carry(pricer: object) -> None:
    """``carry=0.0`` is exactly the silent assumption the keyword exists to name."""

    contract = _contract(OptionType.CALL, "100")
    with pytest.raises(OptionInputError, match="carry must be a Carry"):
        pricer(contract, Decimal("100"), 0.2, 0.05, 0.0, carry=0.0)  # type: ignore[operator]


def test_the_carry_is_required() -> None:
    contract = _contract(OptionType.CALL, "100")
    with pytest.raises(TypeError, match="carry"):
        black_scholes_price(contract, Decimal("100"), 0.2, 0.05, 0.0)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="carry"):
        implied_volatility(contract, Decimal("5"), Decimal("100"), 0.05, 0.0)  # type: ignore[call-arg]
