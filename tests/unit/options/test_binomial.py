"""American options on a Cox-Ross-Rubinstein lattice, and interpolation across expiries.

Ledger NUM-006 (v3.13): until v3.13 an American contract was priced as though it
were European. Ledger FEA-005: a volatility between two quoted expiries is read
in total variance, by name, or refused.

The lattice is pinned by properties that hold whatever its resolution -- an
American call on a stock paying nothing is the European call, the McDonald-
Schroder put-call symmetry -- by its convergence to the closed form, and by a
reference value checked against an independent method.
"""

import math
from decimal import Decimal

import pytest

from alphalab.options import (
    BLACK_SCHOLES_MERTON,
    FUTURES_CARRY,
    MAX_STEPS,
    BinomialLattice,
    CashDividend,
    ExerciseStyle,
    ExpiryInterpolation,
    ImpliedVolatilityError,
    OptionChain,
    OptionContract,
    OptionInputError,
    OptionPricingError,
    OptionType,
    PricingModel,
    VolatilitySurface,
    VolPoint,
    binomial_greeks,
    binomial_price,
    binomial_value,
    black_scholes_greeks,
    black_scholes_value,
    dividend_yield,
    foreign_rate,
    implied_vol_across_expiries,
    implied_volatility,
    occ_symbol,
    surface_from_chain,
)

YEAR = 365.25 * 86400
NOW = 1_700_000_000.0
NONE = dividend_yield(0.0)


def _contract(
    kind: OptionType,
    style: ExerciseStyle,
    strike: str = "100",
    years: float = 1.0,
) -> OptionContract:
    return OptionContract(
        underlying_asset_id="XYZ",
        strike=Decimal(strike),
        expiry=NOW + years * YEAR,
        option_type=kind,
        style=style,
        multiplier=100,
    )


def _value(
    contract: OptionContract,
    spot: float = 100.0,
    volatility: float = 0.2,
    rate: float = 0.05,
    *,
    steps: int = 400,
    carry: object = NONE,
    dividends: tuple[CashDividend, ...] = (),
) -> float:
    return binomial_value(
        contract,
        spot,
        volatility,
        rate,
        NOW,
        carry=carry,  # type: ignore[arg-type]
        lattice=BinomialLattice(steps, dividends),
    )


AMERICAN_PUT = _contract(OptionType.PUT, ExerciseStyle.AMERICAN)
EUROPEAN_PUT = _contract(OptionType.PUT, ExerciseStyle.EUROPEAN)
AMERICAN_CALL = _contract(OptionType.CALL, ExerciseStyle.AMERICAN)
EUROPEAN_CALL = _contract(OptionType.CALL, ExerciseStyle.EUROPEAN)


# --------------------------------------------------------------------------- #
# The lattice against the closed form, and against itself
# --------------------------------------------------------------------------- #


def test_a_european_contract_on_the_lattice_converges_to_the_closed_form() -> None:
    """At the money the CRR error is first order: doubling the steps halves it."""

    exact = black_scholes_value(EUROPEAN_PUT, 100.0, 0.2, 0.05, 1.0, carry=NONE)
    coarse = _value(EUROPEAN_PUT, steps=400) - exact
    fine = _value(EUROPEAN_PUT, steps=800) - exact
    assert abs(fine) < 3e-3
    assert 1.8 < coarse / fine < 2.2


def test_an_american_call_on_a_stock_paying_nothing_is_the_european_call() -> None:
    """Exercising a call early forfeits the strike's interest and gains nothing."""

    for steps in (3, 50, 401):
        american = _value(AMERICAN_CALL, steps=steps)
        european = _value(EUROPEAN_CALL, steps=steps)
        assert american == european


def test_the_american_put_carries_its_early_exercise_premium() -> None:
    american = _value(AMERICAN_PUT)
    european = _value(EUROPEAN_PUT)
    assert american > european + 0.4
    # Deep in the money with a positive rate, exercising now is optimal: the
    # put is worth its intrinsic value and no more.
    assert _value(AMERICAN_PUT, spot=40.0) == pytest.approx(60.0, abs=1e-12)
    assert _value(EUROPEAN_PUT, spot=40.0) < 60.0


def test_the_lattice_satisfies_the_mcdonald_schroder_symmetry() -> None:
    """C(S, K, r, q) = P(K, S, q, r) for American options, exactly on the lattice."""

    rate, q = 0.05, 0.03
    call = binomial_value(
        _contract(OptionType.CALL, ExerciseStyle.AMERICAN, strike="90"),
        100.0,
        0.25,
        rate,
        NOW,
        carry=dividend_yield(q),
        lattice=BinomialLattice(301, ()),
    )
    put = binomial_value(
        _contract(OptionType.PUT, ExerciseStyle.AMERICAN, strike="100"),
        90.0,
        0.25,
        q,
        NOW,
        carry=dividend_yield(rate),
        lattice=BinomialLattice(301, ()),
    )
    assert call == pytest.approx(put, rel=1e-11)


def test_the_reference_american_put() -> None:
    """S = K = 100, r = 5%, sigma = 20%, one year, no dividend: about 6.0904.

    The odd-even average of a 2,000-step lattice removes most of its
    oscillation. The figure was cross-checked before release against an
    independent Crank-Nicolson finite-difference solver with a projected SOR
    step (400 x 400 grid: 6.0875, the grid's own error).
    """

    average = (_value(AMERICAN_PUT, steps=2000) + _value(AMERICAN_PUT, steps=2001)) / 2.0
    assert average == pytest.approx(6.0904, abs=1e-3)


# --------------------------------------------------------------------------- #
# Cash dividends
# --------------------------------------------------------------------------- #


def test_an_escrowed_dividend_prices_a_european_option_as_the_spot_net_of_it() -> None:
    dividend = CashDividend(NOW + 0.5 * YEAR, Decimal("2"))
    lattice_value = _value(EUROPEAN_CALL, steps=2000, dividends=(dividend,))
    net_spot = 100.0 - 2.0 * math.exp(-0.05 * 0.5)
    closed = black_scholes_value(EUROPEAN_CALL, net_spot, 0.2, 0.05, 1.0, carry=NONE)
    assert lattice_value == pytest.approx(closed, abs=1e-3)


def test_a_dividend_just_before_expiry_makes_early_exercise_of_a_call_pay() -> None:
    dividend = (CashDividend(NOW + 0.99 * YEAR, Decimal("15")),)
    american = _value(AMERICAN_CALL, steps=1000, dividends=dividend)
    european = _value(EUROPEAN_CALL, steps=1000, dividends=dividend)
    assert american > european + 5.0
    assert american >= 0.0


def test_dividends_outside_the_contracts_life_change_nothing() -> None:
    outside = (
        CashDividend(NOW - 10.0, Decimal("5")),
        CashDividend(NOW, Decimal("5")),
        CashDividend(NOW + 2.0 * YEAR, Decimal("5")),
    )
    assert _value(AMERICAN_PUT, dividends=outside) == _value(AMERICAN_PUT)


def test_the_dividend_schedule_is_canonical_whatever_order_it_is_given_in() -> None:
    first = CashDividend(NOW + 0.25 * YEAR, Decimal("1"))
    second = CashDividend(NOW + 0.75 * YEAR, Decimal("1"))
    assert BinomialLattice(100, (second, first)) == BinomialLattice(100, (first, second))
    assert BinomialLattice(100, (second, first)).identity.startswith("CRR/n=100/div=")


# --------------------------------------------------------------------------- #
# Greeks and assumptions
# --------------------------------------------------------------------------- #


def test_the_lattice_greeks_agree_with_the_closed_form_for_a_european_contract() -> None:
    lattice = BinomialLattice(1000, ())
    on_lattice = binomial_greeks(
        EUROPEAN_CALL, Decimal("100"), 0.2, 0.05, NOW, carry=NONE, lattice=lattice
    )
    closed = black_scholes_greeks(EUROPEAN_CALL, Decimal("100"), 0.2, 0.05, NOW, carry=NONE)
    assert on_lattice.delta == pytest.approx(closed.delta, rel=1e-3)
    assert on_lattice.gamma == pytest.approx(closed.gamma, rel=3e-3)
    assert on_lattice.theta == pytest.approx(closed.theta, rel=3e-3)
    assert on_lattice.vega == pytest.approx(closed.vega, rel=1e-3)
    assert on_lattice.rho == pytest.approx(closed.rho, rel=1e-3)


def test_an_american_puts_delta_is_minus_one_where_it_is_exercised() -> None:
    greeks = binomial_greeks(
        AMERICAN_PUT, Decimal("40"), 0.2, 0.05, NOW, carry=NONE, lattice=BinomialLattice(200, ())
    )
    assert greeks.delta == pytest.approx(-1.0, abs=1e-12)
    assert greeks.gamma == pytest.approx(0.0, abs=1e-12)


def test_a_lattice_price_names_its_model_and_its_steps() -> None:
    assumptions = BinomialLattice(250, ()).assumptions
    assert assumptions.model is PricingModel.BINOMIAL_CRR
    assert assumptions.prices_early_exercise
    assert assumptions.models_discrete_dividends
    assert assumptions.steps == 250
    assert assumptions.identity == "BINOMIAL_CRR/365.25d/EDC/n=250"
    # The closed form's identity is what it was.
    assert BLACK_SCHOLES_MERTON.identity == "BLACK_SCHOLES/365.25d/D"
    assert BLACK_SCHOLES_MERTON.steps is None


def test_a_price_is_rounded_as_the_closed_forms_is() -> None:
    lattice = BinomialLattice(300, ())
    price = binomial_price(
        AMERICAN_PUT, Decimal("100"), 0.2, 0.05, NOW, carry=NONE, lattice=lattice
    )
    assert price == Decimal(str(_value(AMERICAN_PUT, steps=300))).quantize(Decimal("0.0001"))


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("steps", [0, -1, MAX_STEPS + 1])
def test_a_step_count_outside_its_range_is_refused(steps: int) -> None:
    with pytest.raises(OptionInputError, match="steps"):
        BinomialLattice(steps, ())


def test_a_step_count_must_be_an_int() -> None:
    with pytest.raises(OptionInputError, match="int"):
        BinomialLattice(True, ())  # type: ignore[arg-type]


def test_a_dividend_must_be_a_positive_finite_amount_on_a_finite_instant() -> None:
    with pytest.raises(OptionInputError, match="positive"):
        CashDividend(NOW, Decimal("0"))
    with pytest.raises(OptionInputError, match="finite"):
        CashDividend(math.inf, Decimal("1"))
    with pytest.raises(OptionInputError, match="tuple"):
        BinomialLattice(10, [CashDividend(NOW, Decimal("1"))])  # type: ignore[arg-type]


def test_an_underlying_that_pays_no_cash_dividend_cannot_be_given_one() -> None:
    dividend = (CashDividend(NOW + 0.5 * YEAR, Decimal("1")),)
    for carry in (FUTURES_CARRY, foreign_rate(0.01)):
        with pytest.raises(OptionInputError, match="pays no cash dividend"):
            _value(AMERICAN_PUT, carry=carry, dividends=dividend)


def test_dividends_worth_the_whole_spot_are_refused() -> None:
    dividend = (CashDividend(NOW + 0.1 * YEAR, Decimal("150")),)
    with pytest.raises(OptionInputError, match="worth nothing"):
        _value(AMERICAN_PUT, dividends=dividend)


def test_a_lattice_too_coarse_for_its_carry_and_volatility_is_refused_not_clamped() -> None:
    """|b| sqrt(dt) < sigma or p is not a probability."""

    with pytest.raises(OptionPricingError, match="not a probability"):
        _value(AMERICAN_PUT, volatility=0.01, rate=0.2, steps=4)


def test_greeks_need_two_layers_and_room_for_the_vega_step() -> None:
    with pytest.raises(OptionInputError, match="two layers"):
        binomial_greeks(
            AMERICAN_PUT, Decimal("100"), 0.2, 0.05, NOW, carry=NONE, lattice=BinomialLattice(1, ())
        )
    with pytest.raises(OptionInputError, match="vega step"):
        binomial_greeks(
            AMERICAN_PUT,
            Decimal("100"),
            0.00005,
            0.05,
            NOW,
            carry=NONE,
            lattice=BinomialLattice(10, ()),
        )


# --------------------------------------------------------------------------- #
# Implied volatility on the lattice
# --------------------------------------------------------------------------- #


def test_an_american_quote_inverts_on_the_lattice_to_the_volatility_that_priced_it() -> None:
    lattice = BinomialLattice(300, ())
    quote = Decimal(
        repr(binomial_value(AMERICAN_PUT, 100.0, 0.3, 0.05, NOW, carry=NONE, lattice=lattice))
    )
    implied = implied_volatility(
        AMERICAN_PUT, quote, Decimal("100"), 0.05, NOW, carry=NONE, lattice=lattice
    )
    assert implied.value == pytest.approx(0.3, abs=1e-7)
    assert implied.assumptions.model is PricingModel.BINOMIAL_CRR
    assert implied.lattice == lattice
    assert implied.vega > 0.0


def test_the_closed_form_reads_an_early_exercise_premium_as_volatility() -> None:
    """The bias NUM-006 named: the European inversion of an American quote is too high."""

    lattice = BinomialLattice(300, ())
    quote = Decimal(
        repr(binomial_value(AMERICAN_PUT, 100.0, 0.3, 0.05, NOW, carry=NONE, lattice=lattice))
    )
    european = implied_volatility(AMERICAN_PUT, quote, Decimal("100"), 0.05, NOW, carry=NONE)
    on_lattice = implied_volatility(
        AMERICAN_PUT, quote, Decimal("100"), 0.05, NOW, carry=NONE, lattice=lattice
    )
    assert european.value > on_lattice.value + 0.005
    assert european.lattice is None


def test_a_quote_at_what_exercising_now_pays_is_refused() -> None:
    with pytest.raises(ImpliedVolatilityError, match="floor"):
        implied_volatility(
            AMERICAN_PUT,
            Decimal("60"),
            Decimal("40"),
            0.05,
            NOW,
            carry=NONE,
            lattice=BinomialLattice(100, ()),
        )


def test_a_quote_below_the_zero_volatility_value_on_the_lattice_is_refused() -> None:
    """Below what holding and exercising are worth with no volatility, nothing reaches.

    A call struck at 90 on a stock at 100 paying 15 at six months: exercising
    now pays 10, but exercising just before the stock goes ex pays about 12.2
    today even with no volatility. A quote of 11 is above the floor the bounds
    state and below every price the lattice gives, so it is refused rather than
    answered with the lattice's lowest volatility.
    """

    contract = _contract(OptionType.CALL, ExerciseStyle.AMERICAN, strike="90")
    lattice = BinomialLattice(200, (CashDividend(NOW + 0.5 * YEAR, Decimal("15")),))
    with pytest.raises(ImpliedVolatilityError, match="lowest volatility"):
        implied_volatility(
            contract, Decimal("11"), Decimal("100"), 0.05, NOW, carry=NONE, lattice=lattice
        )


def test_a_surface_built_on_a_lattice_holds_the_lattices_inversions() -> None:
    lattice = BinomialLattice(200, ())
    contracts = tuple(
        _contract(OptionType.PUT, ExerciseStyle.AMERICAN, strike=strike) for strike in ("90", "100")
    )
    prices = {
        occ_symbol(c): Decimal(
            repr(binomial_value(c, 100.0, 0.25, 0.05, NOW, carry=NONE, lattice=lattice))
        )
        for c in contracts
    }
    surface, refusals = surface_from_chain(
        OptionChain("XYZ", NOW, contracts),
        prices,
        Decimal("100"),
        0.05,
        NOW,
        carry=NONE,
        lattice=lattice,
    )
    assert refusals == ()
    assert [point.implied_vol for point in surface.points] == pytest.approx([0.25, 0.25], abs=1e-7)


# --------------------------------------------------------------------------- #
# Across expiries, in total variance
# --------------------------------------------------------------------------- #


def _surface(points: list[tuple[float, float, float]]) -> VolatilitySurface:
    return VolatilitySurface(
        "XYZ",
        NOW,
        tuple(VolPoint(strike=k, expiry=NOW + t * YEAR, implied_vol=v) for k, t, v in points),
    )


TERM = _surface([(100.0, 0.25, 0.20), (110.0, 0.25, 0.22), (100.0, 1.0, 0.30), (110.0, 1.0, 0.32)])
METHOD = ExpiryInterpolation.TOTAL_VARIANCE_LINEAR


def test_a_quoted_expiry_reads_its_own_volatility() -> None:
    assert implied_vol_across_expiries(TERM, 100.0, NOW + 0.25 * YEAR, method=METHOD) == 0.20


def test_between_expiries_the_total_variance_is_linear_in_maturity() -> None:
    near, far = 0.20**2 * 0.25, 0.30**2 * 1.0
    expected = math.sqrt((near + (0.5 - 0.25) / 0.75 * (far - near)) / 0.5)
    assert implied_vol_across_expiries(TERM, 100.0, NOW + 0.5 * YEAR, method=METHOD) == (
        pytest.approx(expected, rel=1e-12)
    )
    # Not the average of the two volatilities, which is what it replaces.
    assert expected != pytest.approx(0.20 + (0.5 - 0.25) / 0.75 * 0.10, rel=1e-3)


def test_a_strike_between_quoted_strikes_is_read_at_each_expiry_first() -> None:
    at_105 = implied_vol_across_expiries(TERM, 105.0, NOW + 0.5 * YEAR, method=METHOD)
    near, far = 0.21**2 * 0.25, 0.31**2 * 1.0
    assert at_105 == pytest.approx(math.sqrt((near + (1.0 / 3.0) * (far - near)) / 0.5), rel=1e-9)


@pytest.mark.parametrize("years", [0.1, 1.5])
def test_an_expiry_outside_the_quoted_range_is_not_extrapolated(years: float) -> None:
    with pytest.raises(OptionInputError, match="extrapolate"):
        implied_vol_across_expiries(TERM, 100.0, NOW + years * YEAR, method=METHOD)


def test_falling_total_variance_is_a_calendar_arbitrage_and_is_refused() -> None:
    inverted = _surface([(100.0, 0.25, 0.60), (100.0, 1.0, 0.20)])
    with pytest.raises(OptionInputError, match="calendar arbitrage"):
        implied_vol_across_expiries(inverted, 100.0, NOW + 0.5 * YEAR, method=METHOD)


def test_interpolating_across_expiries_is_done_only_by_name() -> None:
    with pytest.raises(OptionInputError, match="named method"):
        implied_vol_across_expiries(TERM, 100.0, NOW + 0.5 * YEAR, method="linear")  # type: ignore[arg-type]


def test_a_strike_outside_a_bracketing_expiry_is_refused() -> None:
    with pytest.raises(OptionInputError, match="outside"):
        implied_vol_across_expiries(TERM, 120.0, NOW + 0.5 * YEAR, method=METHOD)
