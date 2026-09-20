"""Implied volatility, model assumptions, expiry resolution and leg arithmetic."""

import math
from decimal import Decimal

import pytest

from alphalab.core.enums import Side
from alphalab.options import (
    BLACK_SCHOLES_MERTON,
    ExerciseStyle,
    ExpirationOutcome,
    ExpirationPolicy,
    Greeks,
    ImpliedVolatilityError,
    Moneyness,
    OptionChain,
    OptionContract,
    OptionInputError,
    OptionLeg,
    OptionStrategy,
    OptionType,
    PricingModel,
    SettlementStyle,
    VolatilitySurface,
    VolPoint,
    black_scholes_greeks,
    black_scholes_price,
    black_scholes_value,
    implied_volatility,
    intrinsic_value,
    moneyness,
    net_greeks,
    net_premium,
    occ_symbol,
    resolve_expiration,
    resolve_strategy_expiration,
    signed_quantity,
    surface_expiries,
    surface_from_chain,
    surface_slice,
    term_structure,
)

YEAR = 365.25 * 86400


def _contract(
    option_type: OptionType = OptionType.CALL,
    strike: str = "150",
    expiry: float = YEAR,
    multiplier: int = 100,
) -> OptionContract:
    return OptionContract(
        underlying_asset_id="AAPL",
        strike=Decimal(strike),
        expiry=expiry,
        option_type=option_type,
        style=ExerciseStyle.EUROPEAN,
        multiplier=multiplier,
    )


# --------------------------------------------------------------------------- #
# Model assumptions
# --------------------------------------------------------------------------- #


def test_the_assumptions_name_the_four_things_the_model_does_not_do() -> None:
    assert BLACK_SCHOLES_MERTON.model is PricingModel.BLACK_SCHOLES
    assert not BLACK_SCHOLES_MERTON.prices_early_exercise
    assert not BLACK_SCHOLES_MERTON.models_dividends
    assert not BLACK_SCHOLES_MERTON.models_volatility_smile


def test_the_year_basis_matches_what_the_pricer_actually_uses() -> None:
    """A docstring can drift from the code; this reads the code."""

    from alphalab.options import pricing

    assert BLACK_SCHOLES_MERTON.year_basis_days * 86400 == pricing._SECONDS_PER_YEAR


def test_the_identity_is_deterministic_and_readable() -> None:
    assert BLACK_SCHOLES_MERTON.identity == "BLACK_SCHOLES/365.25d/none"
    assert BLACK_SCHOLES_MERTON.identity == BLACK_SCHOLES_MERTON.identity


# --------------------------------------------------------------------------- #
# One formula, inverted
# --------------------------------------------------------------------------- #


def test_the_unrounded_value_is_the_price_the_pricer_rounds() -> None:
    """The solver and the pricer are one formula, not two."""

    contract = _contract()
    for volatility in (0.05, 0.25, 0.8):
        raw = black_scholes_value(contract, 150.0, volatility, 0.05, 1.0)
        rounded = black_scholes_price(contract, Decimal("150"), volatility, 0.05, 0.0)
        assert Decimal(str(round(raw, 4))) == rounded


@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
@pytest.mark.parametrize("volatility", [0.05, 0.2, 0.45, 1.2])
@pytest.mark.parametrize("strike", ["120", "150", "185"])
def test_a_round_trip_recovers_the_volatility_it_was_priced_at(
    option_type: OptionType, volatility: float, strike: str
) -> None:
    contract = _contract(option_type, strike)
    price = black_scholes_value(contract, 150.0, volatility, 0.04, 1.0)
    recovered = implied_volatility(contract, Decimal(str(price)), Decimal("150"), 0.04, 0.0)
    assert recovered.value == pytest.approx(volatility, abs=1e-6)
    assert abs(recovered.residual) <= 1e-9


def test_the_result_carries_the_model_it_was_inverted_under() -> None:
    contract = _contract()
    price = black_scholes_price(contract, Decimal("150"), 0.25, 0.04, 0.0)
    assert implied_volatility(contract, price, Decimal("150"), 0.04, 0.0).assumptions is (
        BLACK_SCHOLES_MERTON
    )


def test_the_inversion_is_deterministic_including_the_iteration_count() -> None:
    contract = _contract()
    price = black_scholes_price(contract, Decimal("150"), 0.25, 0.04, 0.0)
    first = implied_volatility(contract, price, Decimal("150"), 0.04, 0.0)
    second = implied_volatility(contract, price, Decimal("150"), 0.04, 0.0)
    assert first == second


# --------------------------------------------------------------------------- #
# The four refusals
# --------------------------------------------------------------------------- #


def test_a_price_at_or_below_the_no_arbitrage_floor_is_refused() -> None:
    contract = _contract(strike="100")
    floor = 150.0 - 100.0 * math.exp(-0.04)
    with pytest.raises(ImpliedVolatilityError, match="no-arbitrage floor"):
        implied_volatility(contract, Decimal(str(floor)), Decimal("150"), 0.04, 0.0)


def test_a_price_at_or_above_the_ceiling_is_refused() -> None:
    with pytest.raises(ImpliedVolatilityError, match="ceiling"):
        implied_volatility(_contract(), Decimal("150"), Decimal("150"), 0.04, 0.0)


def test_a_put_has_its_own_ceiling_at_the_discounted_strike() -> None:
    contract = _contract(OptionType.PUT, "150")
    discounted = 150.0 * math.exp(-0.04)
    with pytest.raises(ImpliedVolatilityError, match="ceiling"):
        implied_volatility(contract, Decimal(str(discounted)), Decimal("150"), 0.04, 0.0)


def test_a_vanishing_vega_is_refused_rather_than_fitted_to_noise() -> None:
    """A far strike one day from expiry, quoted at a fraction of a cent.

    The price is strictly inside the no-arbitrage bounds and the bracket does
    close, so this reaches the vega guard specifically rather than one of the
    bound checks -- which the message is asserted for, because a test that
    accepted any refusal would pass with the guard removed.
    """

    contract = _contract(strike="250", expiry=86400.0)
    with pytest.raises(ImpliedVolatilityError, match="Vega at the solution"):
        implied_volatility(contract, Decimal("1e-12"), Decimal("150"), 0.04, 0.0)


def test_a_price_the_model_cannot_reach_at_all_is_its_own_refusal() -> None:
    """Distinct from the ceiling: the ceiling is the limit at *infinite*
    volatility, and this is the limit at the highest one searched."""

    contract = _contract(strike="400", expiry=3600.0)
    with pytest.raises(ImpliedVolatilityError, match="does not reach"):
        implied_volatility(contract, Decimal("1e-20"), Decimal("150"), 0.04, 0.0)


def test_an_expired_contract_is_refused_before_anything_else() -> None:
    with pytest.raises(OptionInputError, match="expired"):
        implied_volatility(_contract(expiry=0.0), Decimal("5"), Decimal("150"), 0.04, 1.0)


def test_a_non_positive_spot_is_refused() -> None:
    with pytest.raises(OptionInputError, match="spot must be positive"):
        implied_volatility(_contract(), Decimal("5"), Decimal("0"), 0.04, 0.0)


# --------------------------------------------------------------------------- #
# Volatility surface
# --------------------------------------------------------------------------- #


def _chain(strikes: tuple[str, ...] = ("130", "150", "170")) -> OptionChain:
    return OptionChain(
        underlying_asset_id="AAPL",
        timestamp=0.0,
        contracts=tuple(_contract(OptionType.CALL, strike) for strike in strikes),
    )


def test_a_surface_built_from_a_chain_accounts_for_every_contract() -> None:
    chain = _chain()
    prices = {
        occ_symbol(contract): Decimal(str(black_scholes_value(contract, 150.0, 0.25, 0.04, 1.0)))
        for contract in chain.contracts
    }
    surface, refusals = surface_from_chain(chain, prices, Decimal("150"), 0.04, 0.0)
    assert len(surface.points) + len(refusals) == len(chain.contracts)
    assert refusals == ()
    assert all(point.implied_vol == pytest.approx(0.25, abs=1e-6) for point in surface.points)


def test_an_unquotable_wing_is_reported_rather_than_dropped() -> None:
    chain = _chain()
    prices = {occ_symbol(chain.contracts[0]): Decimal("0.0001")}  # below its floor
    surface, refusals = surface_from_chain(chain, prices, Decimal("150"), 0.04, 0.0)
    assert len(surface.points) + len(refusals) == 3
    assert len(refusals) == 3
    assert any("floor" in refusal.reason for refusal in refusals)
    assert sum("no price was supplied" in refusal.reason for refusal in refusals) == 2


def test_a_slice_is_one_expiry_and_an_unquoted_one_is_refused() -> None:
    surface = VolatilitySurface(
        "AAPL",
        0.0,
        (
            VolPoint(140.0, YEAR, 0.30),
            VolPoint(150.0, YEAR, 0.25),
            VolPoint(150.0, 2 * YEAR, 0.28),
        ),
    )
    assert surface_expiries(surface) == (YEAR, 2 * YEAR)
    sliced = surface_slice(surface, YEAR)
    assert sliced.strikes == (140.0, 150.0)
    assert sliced.volatilities == (0.30, 0.25)
    with pytest.raises(OptionInputError, match="variances accumulate"):
        surface_slice(surface, 3 * YEAR)


def test_a_term_structure_reports_only_the_expiries_that_quote_the_strike() -> None:
    surface = VolatilitySurface(
        "AAPL",
        0.0,
        (VolPoint(150.0, YEAR, 0.25), VolPoint(150.0, 2 * YEAR, 0.28), VolPoint(140.0, YEAR, 0.3)),
    )
    assert term_structure(surface, 150.0) == ((YEAR, 0.25), (2 * YEAR, 0.28))
    assert term_structure(surface, 140.0) == ((YEAR, 0.3),)
    assert term_structure(surface, 999.0) == ()


# --------------------------------------------------------------------------- #
# Moneyness and expiry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("option_type", "spot", "expected"),
    [
        (OptionType.CALL, "160", Moneyness.IN_THE_MONEY),
        (OptionType.CALL, "150", Moneyness.AT_THE_MONEY),
        (OptionType.CALL, "140", Moneyness.OUT_OF_THE_MONEY),
        (OptionType.PUT, "140", Moneyness.IN_THE_MONEY),
        (OptionType.PUT, "150", Moneyness.AT_THE_MONEY),
        (OptionType.PUT, "160", Moneyness.OUT_OF_THE_MONEY),
    ],
)
def test_moneyness_reads_the_settlement_price_against_the_strike(
    option_type: OptionType, spot: str, expected: Moneyness
) -> None:
    assert moneyness(_contract(option_type), Decimal(spot)) is expected


def test_at_the_money_is_not_in_the_money_and_expires_worthless() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    result = resolve_expiration(_contract(), Decimal("1"), Decimal("150"), policy)
    assert result.moneyness is Moneyness.AT_THE_MONEY
    assert result.outcome is ExpirationOutcome.EXPIRED_WORTHLESS
    assert result.cash_flow == Decimal("0")
    assert intrinsic_value(_contract(), Decimal("150")) == Decimal("0")


def test_cash_settlement_pays_intrinsic_and_moves_no_underlying() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    result = resolve_expiration(_contract(), Decimal("2"), Decimal("160"), policy)
    assert result.outcome is ExpirationOutcome.EXERCISED
    assert result.cash_flow == Decimal("2000")  # 10 intrinsic * 100 * 2
    assert result.underlying_units == Decimal("0")


def test_a_short_cash_settled_call_is_assigned_and_pays_out() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    result = resolve_expiration(_contract(), Decimal("-2"), Decimal("160"), policy)
    assert result.outcome is ExpirationOutcome.ASSIGNED
    assert result.cash_flow == Decimal("-2000")


def test_a_long_physical_call_pays_the_strike_and_receives_the_underlying() -> None:
    policy = ExpirationPolicy(SettlementStyle.PHYSICAL, exercise_in_the_money=True)
    result = resolve_expiration(_contract(), Decimal("1"), Decimal("160"), policy)
    assert result.cash_flow == Decimal("-15000")
    assert result.underlying_units == Decimal("100")


def test_a_short_physical_call_receives_the_strike_and_delivers_the_underlying() -> None:
    policy = ExpirationPolicy(SettlementStyle.PHYSICAL, exercise_in_the_money=True)
    result = resolve_expiration(_contract(), Decimal("-1"), Decimal("160"), policy)
    assert result.outcome is ExpirationOutcome.ASSIGNED
    assert result.cash_flow == Decimal("15000")
    assert result.underlying_units == Decimal("-100")


def test_a_long_physical_put_delivers_the_underlying_and_receives_the_strike() -> None:
    policy = ExpirationPolicy(SettlementStyle.PHYSICAL, exercise_in_the_money=True)
    result = resolve_expiration(_contract(OptionType.PUT), Decimal("1"), Decimal("140"), policy)
    assert result.cash_flow == Decimal("15000")
    assert result.underlying_units == Decimal("-100")


def test_the_two_sides_of_one_exercise_net_to_zero() -> None:
    """The dimensional check: an exercise moves value between two parties."""

    policy = ExpirationPolicy(SettlementStyle.PHYSICAL, exercise_in_the_money=True)
    long = resolve_expiration(_contract(), Decimal("3"), Decimal("160"), policy)
    short = resolve_expiration(_contract(), Decimal("-3"), Decimal("160"), policy)
    assert long.cash_flow + short.cash_flow == Decimal("0")
    assert long.underlying_units + short.underlying_units == Decimal("0")


def test_abandoning_an_in_the_money_contract_is_its_own_outcome() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=False)
    result = resolve_expiration(_contract(), Decimal("1"), Decimal("160"), policy)
    assert result.outcome is ExpirationOutcome.ABANDONED
    assert result.moneyness is Moneyness.IN_THE_MONEY
    assert result.cash_flow == Decimal("0")


def test_a_zero_position_has_no_outcome() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    with pytest.raises(OptionInputError, match="never open"):
        resolve_expiration(_contract(), Decimal("0"), Decimal("160"), policy)


def test_a_negative_settlement_price_is_refused() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    with pytest.raises(OptionInputError, match="not assumed"):
        resolve_expiration(_contract(), Decimal("1"), Decimal("-1"), policy)


def test_the_multiplier_is_applied_once_and_scales_the_answer() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    hundred = resolve_expiration(_contract(multiplier=100), Decimal("1"), Decimal("160"), policy)
    fifty = resolve_expiration(_contract(multiplier=50), Decimal("1"), Decimal("160"), policy)
    assert hundred.cash_flow == fifty.cash_flow * 2


# --------------------------------------------------------------------------- #
# Multi-leg strategies
# --------------------------------------------------------------------------- #


def _spread() -> OptionStrategy:
    return OptionStrategy(
        (
            OptionLeg(_contract(strike="150"), Side.BUY, 2),
            OptionLeg(_contract(strike="160"), Side.SELL, 2),
        )
    )


def test_signed_quantity_carries_direction_out_of_the_side() -> None:
    long_leg, short_leg = _spread().legs
    assert signed_quantity(long_leg) == Decimal("2")
    assert signed_quantity(short_leg) == Decimal("-2")


def test_net_premium_is_a_cash_flow_and_a_debit_spread_is_negative() -> None:
    prices = {
        occ_symbol(_contract(strike="150")): Decimal("8.00"),
        occ_symbol(_contract(strike="160")): Decimal("3.00"),
    }
    assert net_premium(_spread(), prices) == Decimal("-1000")  # (8 - 3) * 100 * 2 paid


def test_net_premium_refuses_a_missing_leg() -> None:
    with pytest.raises(OptionInputError, match="Missing entry price"):
        net_premium(_spread(), {})


def test_a_strategy_mixing_multipliers_sums_each_leg_with_its_own() -> None:
    strategy = OptionStrategy(
        (
            OptionLeg(_contract(strike="150", multiplier=100), Side.BUY, 1),
            OptionLeg(_contract(strike="150", multiplier=50), Side.SELL, 1),
        )
    )
    # Both legs share an occ_symbol (it carries no multiplier), so one price applies.
    prices = {occ_symbol(_contract(strike="150")): Decimal("4.00")}
    assert net_premium(strategy, prices) == Decimal("-200")  # -(4*100) + (4*50)


def test_net_greeks_scale_by_multiplier_and_sign() -> None:
    per_unit = Greeks(delta=0.6, gamma=0.02, theta=-0.05, vega=0.4, rho=0.3)
    greeks = {
        occ_symbol(_contract(strike="150")): per_unit,
        occ_symbol(_contract(strike="160")): per_unit,
    }
    # Two legs of 2 contracts, opposite sides, identical Greeks: everything nets out.
    assert net_greeks(_spread(), greeks) == Greeks(0.0, 0.0, 0.0, 0.0, 0.0)


def test_net_greeks_of_a_single_long_leg_are_the_per_unit_ones_scaled() -> None:
    per_unit = Greeks(delta=0.6, gamma=0.02, theta=-0.05, vega=0.4, rho=0.3)
    strategy = OptionStrategy((OptionLeg(_contract(strike="150"), Side.BUY, 2),))
    total = net_greeks(strategy, {occ_symbol(_contract(strike="150")): per_unit})
    assert total.delta == pytest.approx(0.6 * 100 * 2)
    assert total.theta == pytest.approx(-0.05 * 100 * 2)


def test_net_greeks_refuses_a_missing_leg() -> None:
    with pytest.raises(OptionInputError, match="Missing Greeks"):
        net_greeks(_spread(), {})


def test_resolving_a_strategy_keeps_one_result_per_leg_with_its_direction() -> None:
    """Netting would hide a spread assigned on one side and exercised on the other."""

    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    results = resolve_strategy_expiration(_spread(), Decimal("155"), policy)
    assert len(results) == 2
    assert results[0].outcome is ExpirationOutcome.EXERCISED
    assert results[0].contracts == Decimal("2")
    assert results[1].outcome is ExpirationOutcome.EXPIRED_WORTHLESS
    assert results[1].contracts == Decimal("-2")


def test_a_bull_call_spreads_payoff_is_capped_above_the_short_strike() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    for spot in ("160", "200", "1000"):
        results = resolve_strategy_expiration(_spread(), Decimal(spot), policy)
        assert sum(result.cash_flow for result in results) == Decimal("2000")


def test_the_greeks_the_pricer_returns_are_deterministic() -> None:
    contract = _contract()
    first = black_scholes_greeks(contract, Decimal("150"), 0.25, 0.04, 0.0)
    second = black_scholes_greeks(contract, Decimal("150"), 0.25, 0.04, 0.0)
    assert first == second
