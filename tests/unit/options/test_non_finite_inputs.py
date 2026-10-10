"""Every option-pricing entry point refuses an input that is not a finite number (v4.0, NUM-015).

Until v4.0 the closed form compared ``volatility <= 0`` and nothing else: a
``NaN`` is not less than zero, so a ``NaN`` or infinite volatility -- or any
non-finite rate, which nothing checked -- came back as a ``Decimal('NaN')``
price and ``NaN`` Greeks, with no error at all. A ``NaN`` spot raised
``decimal.InvalidOperation`` from the comparison rather than naming the input.
The lattice refused a ``NaN`` rate only by accident, with a message about its
up-move probability. Each now names the input that is not a number.

The verification is independent of the fix: each case is fed in turn to every
public pricing function, and the refusal must be an ``OptionInputError`` whose
message names the input.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from alphalab.options import ExerciseStyle, OptionContract, OptionType
from alphalab.options.binomial import (
    BinomialLattice,
    binomial_greeks,
    binomial_price,
    binomial_value,
)
from alphalab.options.carry import dividend_yield
from alphalab.options.exceptions import OptionInputError
from alphalab.options.implied import implied_volatility
from alphalab.options.pricing import black_scholes_greeks, black_scholes_price

NOW = 1_700_000_000.0
EXPIRY = NOW + 365.25 * 86_400
CALL = OptionContract("U", Decimal("100"), EXPIRY, OptionType.CALL, ExerciseStyle.AMERICAN, 100)
CARRY = dividend_yield(0.0)
LATTICE = BinomialLattice(50, ())

GOOD: dict[str, Any] = {
    "spot": Decimal("100"),
    "volatility": 0.2,
    "risk_free_rate": 0.05,
}

NOT_FINITE: list[tuple[str, Any]] = [
    ("volatility", float("nan")),
    ("volatility", float("inf")),
    ("risk_free_rate", float("nan")),
    ("risk_free_rate", float("-inf")),
    ("spot", Decimal("NaN")),
    ("spot", Decimal("Infinity")),
]


def _closed_price(spot: Decimal, volatility: float, risk_free_rate: float) -> object:
    return black_scholes_price(CALL, spot, volatility, risk_free_rate, NOW, carry=CARRY)


def _closed_greeks(spot: Decimal, volatility: float, risk_free_rate: float) -> object:
    return black_scholes_greeks(CALL, spot, volatility, risk_free_rate, NOW, carry=CARRY)


def _lattice_price(spot: Decimal, volatility: float, risk_free_rate: float) -> object:
    return binomial_price(CALL, spot, volatility, risk_free_rate, NOW, carry=CARRY, lattice=LATTICE)


def _lattice_value(spot: Decimal, volatility: float, risk_free_rate: float) -> object:
    return binomial_value(
        CALL, float(spot), volatility, risk_free_rate, NOW, carry=CARRY, lattice=LATTICE
    )


def _lattice_greeks(spot: Decimal, volatility: float, risk_free_rate: float) -> object:
    return binomial_greeks(
        CALL, spot, volatility, risk_free_rate, NOW, carry=CARRY, lattice=LATTICE
    )


PRICERS: dict[str, Callable[..., object]] = {
    "black_scholes_price": _closed_price,
    "black_scholes_greeks": _closed_greeks,
    "binomial_price": _lattice_price,
    "binomial_value": _lattice_value,
    "binomial_greeks": _lattice_greeks,
}


@pytest.mark.parametrize("pricer", sorted(PRICERS))
def test_the_finite_inputs_price(pricer: str) -> None:
    """The guard on the guard: the same call prices when every input is a number."""

    assert PRICERS[pricer](**GOOD) is not None


@pytest.mark.parametrize("pricer", sorted(PRICERS))
@pytest.mark.parametrize(("name", "value"), NOT_FINITE)
def test_a_non_finite_input_is_refused_by_name(pricer: str, name: str, value: Any) -> None:
    if pricer == "binomial_value" and name == "spot":
        value = float(value)  # this one takes the spot as a float

    with pytest.raises(OptionInputError, match=f"^{name} must be a finite number"):
        PRICERS[pricer](**{**GOOD, name: value})


@pytest.mark.parametrize("lattice", [None, LATTICE])
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("market_price", Decimal("NaN")),
        ("market_price", Decimal("Infinity")),
        ("risk_free_rate", float("nan")),
        ("spot", Decimal("NaN")),
    ],
)
def test_implied_volatility_refuses_a_non_finite_input_by_name(
    lattice: BinomialLattice | None, name: str, value: Any
) -> None:
    inputs: dict[str, Any] = {
        "market_price": Decimal("10.45"),
        "spot": Decimal("100"),
        "risk_free_rate": 0.05,
        **{name: value},
    }

    with pytest.raises(OptionInputError, match=f"^{name} must be a finite number"):
        implied_volatility(
            CALL,
            inputs["market_price"],
            inputs["spot"],
            inputs["risk_free_rate"],
            NOW,
            carry=CARRY,
            lattice=lattice,
        )


def test_a_boolean_rate_is_not_a_number() -> None:
    """``True`` is an ``int`` to Python and a mistake to a pricer."""

    with pytest.raises(OptionInputError, match=r"^risk_free_rate must be a finite number"):
        _closed_price(Decimal("100"), 0.2, True)
