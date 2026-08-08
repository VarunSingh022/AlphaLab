"""Comprehensive tests for the Options Engine: contracts, pricing, chains, strategies."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.core.enums import Side
from alphalab.options import (
    ExerciseStyle,
    OptionChain,
    OptionContract,
    OptionInputError,
    OptionLeg,
    OptionPricingError,
    OptionsError,
    OptionStrategy,
    OptionType,
    VolatilitySurface,
    VolPoint,
    black_scholes_greeks,
    black_scholes_price,
    by_expiry,
    calls,
    compute_payoff_at_expiry,
    compute_pnl,
    expiries,
    implied_vol_at,
    occ_symbol,
    open_option_position,
    puts,
    strikes_for_expiry,
    time_to_expiry_years,
)
from alphalab.portfolio.position import Position

ONE_YEAR = 365.25 * 86400


def _call(strike: str = "150.00", expiry: float = ONE_YEAR) -> OptionContract:
    return OptionContract(
        underlying_asset_id="AAPL",
        strike=Decimal(strike),
        expiry=expiry,
        option_type=OptionType.CALL,
        style=ExerciseStyle.EUROPEAN,
    )


def _put(strike: str = "150.00", expiry: float = ONE_YEAR) -> OptionContract:
    return OptionContract(
        underlying_asset_id="AAPL",
        strike=Decimal(strike),
        expiry=expiry,
        option_type=OptionType.PUT,
        style=ExerciseStyle.EUROPEAN,
    )


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


def test_exception_hierarchy() -> None:
    assert issubclass(OptionInputError, OptionsError)
    assert issubclass(OptionPricingError, OptionsError)


# --------------------------------------------------------------------------- #
# OptionContract
# --------------------------------------------------------------------------- #


def test_contract_is_immutable() -> None:
    contract = _call()
    with pytest.raises(FrozenInstanceError):
        contract.strike = Decimal("200")  # type: ignore[misc]


def test_contract_rejects_non_positive_strike() -> None:
    with pytest.raises(OptionInputError):
        OptionContract(
            underlying_asset_id="AAPL",
            strike=Decimal("0"),
            expiry=ONE_YEAR,
            option_type=OptionType.CALL,
        )


def test_contract_rejects_non_positive_multiplier() -> None:
    with pytest.raises(OptionInputError):
        OptionContract(
            underlying_asset_id="AAPL",
            strike=Decimal("150"),
            expiry=ONE_YEAR,
            option_type=OptionType.CALL,
            multiplier=0,
        )


def test_occ_symbol_is_stable_and_unique_per_contract() -> None:
    call = _call(strike="150.00")
    put = _put(strike="150.00")
    assert occ_symbol(call) != occ_symbol(put)
    assert occ_symbol(call) == occ_symbol(_call(strike="150.00"))


def test_occ_symbol_differs_by_strike() -> None:
    assert occ_symbol(_call(strike="150.00")) != occ_symbol(_call(strike="160.00"))


# --------------------------------------------------------------------------- #
# Position bridge: the landmine this PR was designed to defuse
# --------------------------------------------------------------------------- #


def test_open_option_position_returns_unmodified_portfolio_position() -> None:
    """Proves options positions are the real Position class, not a new type."""
    contract = _call()
    position = open_option_position(contract, Decimal("1"), Decimal("6.50"), timestamp=1000.0)

    assert type(position) is Position
    assert position.asset_id == occ_symbol(contract)
    assert position.quantity == Decimal("1")
    assert position.average_cost == Decimal("6.50")


def test_option_position_supports_apply_fill_from_portfolio_package() -> None:
    """A real Position method, never touched by this PR, works on an option position."""
    contract = _call()
    position = open_option_position(contract, Decimal("1"), Decimal("6.50"), timestamp=1000.0)

    updated, realized = position.apply_fill(Decimal("-1"), Decimal("9.00"), timestamp=2000.0)

    assert updated.quantity == Decimal("0")
    assert realized == Decimal("2.50")


def test_short_option_position_side_is_short() -> None:
    contract = _call()
    position = open_option_position(contract, Decimal("-2"), Decimal("6.50"), timestamp=1000.0)
    assert position.quantity < 0


# --------------------------------------------------------------------------- #
# Black-Scholes pricing: verified against textbook reference values
# --------------------------------------------------------------------------- #


def test_black_scholes_call_matches_textbook_reference() -> None:
    """S=100, K=100, T=1yr, r=0.05, sigma=0.2 -> call ~= 10.4506 (standard reference)."""
    contract = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.CALL,
    )
    price = black_scholes_price(contract, Decimal("100"), 0.2, 0.05, 0.0)
    assert price == pytest.approx(Decimal("10.4506"), abs=Decimal("0.001"))


def test_black_scholes_put_matches_textbook_reference() -> None:
    """S=100, K=100, T=1yr, r=0.05, sigma=0.2 -> put ~= 5.5735 (standard reference)."""
    contract = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.PUT,
    )
    price = black_scholes_price(contract, Decimal("100"), 0.2, 0.05, 0.0)
    assert price == pytest.approx(Decimal("5.5735"), abs=Decimal("0.001"))


def test_put_call_parity_holds() -> None:
    """C - P = S - K*e^(-rT), a model-independent no-arbitrage identity."""
    import math

    call = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.CALL,
    )
    put = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.PUT,
    )
    call_price = black_scholes_price(call, Decimal("100"), 0.2, 0.05, 0.0)
    put_price = black_scholes_price(put, Decimal("100"), 0.2, 0.05, 0.0)

    expected_diff = Decimal("100") - Decimal("100") * Decimal(str(math.exp(-0.05 * 1.0)))
    assert (call_price - put_price) == pytest.approx(expected_diff, abs=Decimal("0.01"))


def test_black_scholes_rejects_expired_contract() -> None:
    contract = _call(expiry=500.0)
    with pytest.raises(OptionInputError):
        black_scholes_price(contract, Decimal("150"), 0.2, 0.05, valuation_timestamp=1000.0)


def test_black_scholes_rejects_non_positive_spot() -> None:
    with pytest.raises(OptionInputError):
        black_scholes_price(_call(), Decimal("0"), 0.2, 0.05, 0.0)


def test_black_scholes_rejects_non_positive_volatility() -> None:
    with pytest.raises(OptionInputError):
        black_scholes_price(_call(), Decimal("150"), 0.0, 0.05, 0.0)


def test_time_to_expiry_years_is_positive_for_future_expiry() -> None:
    assert time_to_expiry_years(_call(), valuation_timestamp=0.0) == pytest.approx(1.0, abs=0.01)


# --------------------------------------------------------------------------- #
# Greeks
# --------------------------------------------------------------------------- #


def test_call_delta_matches_textbook_reference() -> None:
    contract = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.CALL,
    )
    greeks = black_scholes_greeks(contract, Decimal("100"), 0.2, 0.05, 0.0)
    assert greeks.delta == pytest.approx(0.6368, abs=0.001)


def test_put_delta_is_call_delta_minus_one() -> None:
    call = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.CALL,
    )
    put = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.PUT,
    )
    call_greeks = black_scholes_greeks(call, Decimal("100"), 0.2, 0.05, 0.0)
    put_greeks = black_scholes_greeks(put, Decimal("100"), 0.2, 0.05, 0.0)
    assert put_greeks.delta == pytest.approx(call_greeks.delta - 1.0, abs=1e-9)


def test_gamma_is_identical_for_call_and_put() -> None:
    call = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.CALL,
    )
    put = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("100"),
        expiry=ONE_YEAR,
        option_type=OptionType.PUT,
    )
    call_greeks = black_scholes_greeks(call, Decimal("100"), 0.2, 0.05, 0.0)
    put_greeks = black_scholes_greeks(put, Decimal("100"), 0.2, 0.05, 0.0)
    assert call_greeks.gamma == pytest.approx(put_greeks.gamma, abs=1e-9)


def test_deep_itm_call_delta_approaches_one() -> None:
    contract = OptionContract(
        underlying_asset_id="TEST",
        strike=Decimal("10"),
        expiry=ONE_YEAR,
        option_type=OptionType.CALL,
    )
    greeks = black_scholes_greeks(contract, Decimal("1000"), 0.2, 0.05, 0.0)
    assert greeks.delta > 0.99


# --------------------------------------------------------------------------- #
# OptionChain
# --------------------------------------------------------------------------- #


def _sample_chain() -> OptionChain:
    contracts = (
        _call(strike="140.00", expiry=ONE_YEAR),
        _call(strike="150.00", expiry=ONE_YEAR),
        _put(strike="140.00", expiry=ONE_YEAR),
        _put(strike="150.00", expiry=ONE_YEAR),
        _call(strike="150.00", expiry=ONE_YEAR * 2),
    )
    return OptionChain(underlying_asset_id="AAPL", timestamp=0.0, contracts=contracts)


def test_calls_and_puts_partition_the_chain() -> None:
    chain = _sample_chain()
    assert len(calls(chain)) == 3
    assert len(puts(chain)) == 2
    assert len(calls(chain)) + len(puts(chain)) == len(chain.contracts)


def test_expiries_returns_distinct_sorted_values() -> None:
    chain = _sample_chain()
    result = expiries(chain)
    assert result == tuple(sorted(result))
    assert len(result) == 2


def test_by_expiry_filters_correctly() -> None:
    chain = _sample_chain()
    near_dated = by_expiry(chain, ONE_YEAR)
    assert len(near_dated) == 4


def test_strikes_for_expiry_returns_sorted_distinct_strikes() -> None:
    chain = _sample_chain()
    result = strikes_for_expiry(chain, ONE_YEAR)
    assert result == (140.0, 150.0)


# --------------------------------------------------------------------------- #
# VolatilitySurface
# --------------------------------------------------------------------------- #


def _sample_surface() -> VolatilitySurface:
    points = (
        VolPoint(strike=140.0, expiry=ONE_YEAR, implied_vol=0.22),
        VolPoint(strike=160.0, expiry=ONE_YEAR, implied_vol=0.26),
    )
    return VolatilitySurface(underlying_asset_id="AAPL", timestamp=0.0, points=points)


def test_implied_vol_exact_match() -> None:
    surface = _sample_surface()
    assert implied_vol_at(surface, 140.0, ONE_YEAR) == 0.22


def test_implied_vol_linear_interpolation() -> None:
    surface = _sample_surface()
    result = implied_vol_at(surface, 150.0, ONE_YEAR)
    assert result == pytest.approx(0.24)


def test_implied_vol_returns_none_outside_strike_range() -> None:
    surface = _sample_surface()
    assert implied_vol_at(surface, 200.0, ONE_YEAR) is None


def test_implied_vol_returns_none_for_unknown_expiry() -> None:
    surface = _sample_surface()
    assert implied_vol_at(surface, 150.0, ONE_YEAR * 5) is None


# --------------------------------------------------------------------------- #
# Strategy simulation
# --------------------------------------------------------------------------- #


def test_option_leg_rejects_non_positive_quantity() -> None:
    with pytest.raises(OptionInputError):
        OptionLeg(contract=_call(), side=Side.BUY, quantity=0)


def test_long_call_payoff_above_strike() -> None:
    leg = OptionLeg(contract=_call(strike="150.00"), side=Side.BUY, quantity=1)
    strategy = OptionStrategy(legs=(leg,))
    payoff = compute_payoff_at_expiry(strategy, spot_price=Decimal("160.00"))
    assert payoff == Decimal("1000.00")  # (160-150) * 100 multiplier * 1 contract


def test_long_call_payoff_zero_below_strike() -> None:
    leg = OptionLeg(contract=_call(strike="150.00"), side=Side.BUY, quantity=1)
    strategy = OptionStrategy(legs=(leg,))
    payoff = compute_payoff_at_expiry(strategy, spot_price=Decimal("140.00"))
    assert payoff == Decimal("0")


def test_short_call_payoff_is_negative_above_strike() -> None:
    leg = OptionLeg(contract=_call(strike="150.00"), side=Side.SELL, quantity=1)
    strategy = OptionStrategy(legs=(leg,))
    payoff = compute_payoff_at_expiry(strategy, spot_price=Decimal("160.00"))
    assert payoff == Decimal("-1000.00")


def test_bull_call_spread_payoff_is_capped() -> None:
    """Classic bull call spread: long lower strike, short higher strike."""
    long_leg = OptionLeg(contract=_call(strike="150.00"), side=Side.BUY, quantity=1)
    short_leg = OptionLeg(contract=_call(strike="160.00"), side=Side.SELL, quantity=1)
    strategy = OptionStrategy(legs=(long_leg, short_leg))

    # Above both strikes: payoff caps at (160-150)*100 = 1000
    payoff_far_itm = compute_payoff_at_expiry(strategy, spot_price=Decimal("200.00"))
    assert payoff_far_itm == Decimal("1000.00")

    # Below both strikes: payoff is zero
    payoff_otm = compute_payoff_at_expiry(strategy, spot_price=Decimal("140.00"))
    assert payoff_otm == Decimal("0")


def test_compute_pnl_long_call_profitable_when_itm_past_breakeven() -> None:
    contract = _call(strike="150.00")
    leg = OptionLeg(contract=contract, side=Side.BUY, quantity=1)
    strategy = OptionStrategy(legs=(leg,))
    entry_prices = {occ_symbol(contract): Decimal("5.00")}

    pnl = compute_pnl(strategy, entry_prices, spot_price=Decimal("160.00"))
    # intrinsic = 10, entry = 5 -> profit of 5/share * 100 multiplier = 500
    assert pnl == Decimal("500.00")


def test_compute_pnl_long_call_loses_full_premium_when_otm() -> None:
    contract = _call(strike="150.00")
    leg = OptionLeg(contract=contract, side=Side.BUY, quantity=1)
    strategy = OptionStrategy(legs=(leg,))
    entry_prices = {occ_symbol(contract): Decimal("5.00")}

    pnl = compute_pnl(strategy, entry_prices, spot_price=Decimal("140.00"))
    assert pnl == Decimal("-500.00")


def test_compute_pnl_raises_when_entry_price_missing() -> None:
    contract = _call()
    leg = OptionLeg(contract=contract, side=Side.BUY, quantity=1)
    strategy = OptionStrategy(legs=(leg,))

    with pytest.raises(OptionInputError):
        compute_pnl(strategy, entry_prices={}, spot_price=Decimal("150.00"))


def test_strategy_is_immutable() -> None:
    strategy = OptionStrategy(legs=(OptionLeg(contract=_call(), side=Side.BUY, quantity=1),))
    with pytest.raises(FrozenInstanceError):
        strategy.legs = ()  # type: ignore[misc]
