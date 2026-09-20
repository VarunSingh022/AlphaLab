"""The scenario contract: shocks, scopes, application, and what it refuses."""

from decimal import Decimal

import pytest

from alphalab.scenario import (
    CRISIS_2008,
    HISTORICAL_SCENARIOS,
    Scenario,
    ScenarioExposure,
    ScenarioState,
    ScenarioValidationError,
    Shock,
    ShockKind,
    UnsupportedShockError,
    apply_all,
    commodity_shock,
    everything,
    flash_crash,
    from_positions,
    fx_shock,
    of_assets,
    of_currency,
    of_sector,
    rate_shock,
    scenario,
    sector_shock,
)


def book() -> ScenarioState:
    return ScenarioState(
        exposures=(
            ScenarioExposure(
                "AAPL",
                Decimal("1000"),
                Decimal("200"),
                "USD",
                volatility=0.20,
                available_liquidity=Decimal("50000"),
                sector="Tech",
            ),
            ScenarioExposure(
                "SAP",
                Decimal("500"),
                Decimal("100"),
                "EUR",
                volatility=0.25,
                available_liquidity=Decimal("20000"),
                sector="Tech",
            ),
            ScenarioExposure(
                "XOM",
                Decimal("-300"),
                Decimal("110"),
                "USD",
                volatility=0.30,
                available_liquidity=Decimal("80000"),
                sector="Energy",
            ),
        ),
        base_currency="USD",
        rates={"EUR": Decimal("1.10")},
    )


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def test_a_price_shock_moves_the_value_by_the_stated_fraction() -> None:
    result = scenario("mild", price_shock=Decimal("-0.15")).apply(book())

    assert result.relative_change == Decimal("-0.15")


def test_the_per_asset_changes_sum_to_the_portfolio_change() -> None:
    result = scenario("mild", price_shock=Decimal("-0.15")).apply(book())

    assert sum(result.change_by_asset.values()) == result.change


def test_a_short_position_gains_when_prices_fall() -> None:
    result = scenario("mild", price_shock=Decimal("-0.15")).apply(book())

    assert result.change_by_asset["XOM"] > 0
    assert result.change_by_asset["AAPL"] < 0


def test_applying_does_not_mutate_the_base_state() -> None:
    """Scenario leakage into the base book is the failure this guards."""

    base = book()
    before = base.value()

    result = scenario("severe", price_shock=Decimal("-0.5")).apply(base)

    assert base.value() == before
    assert base.exposures[0].price == Decimal("200")
    assert result.base_state == base
    assert result.shocked_state != base


def test_the_same_base_state_can_be_stressed_by_many_scenarios_independently() -> None:
    base = book()
    results = apply_all(
        (
            scenario("a", price_shock=Decimal("-0.1")),
            scenario("b", price_shock=Decimal("-0.2")),
        ),
        base,
    )

    assert results[0].base_value == results[1].base_value == base.value()
    assert results[0].shocked_value != results[1].shocked_value


# --------------------------------------------------------------------------- #
# Scopes
# --------------------------------------------------------------------------- #


def test_a_sector_scope_reaches_only_that_sector() -> None:
    result = sector_shock(Decimal("-0.40"), "Energy").apply(book())

    assert result.reached == (1,)
    assert result.change_by_asset["AAPL"] == 0
    assert result.change_by_asset["XOM"] != 0


def test_an_asset_scope_reaches_only_the_named_assets() -> None:
    result = commodity_shock(Decimal("-0.30"), "XOM").apply(book())

    assert result.reached == (1,)


def test_an_unclassified_exposure_is_not_swept_into_a_sector_shock() -> None:
    state = ScenarioState(
        exposures=(
            ScenarioExposure("KNOWN", Decimal("10"), Decimal("100"), "USD", sector="Tech"),
            ScenarioExposure("UNKNOWN", Decimal("10"), Decimal("100"), "USD"),
        ),
        base_currency="USD",
        rates={},
    )
    result = sector_shock(Decimal("-0.5"), "Tech").apply(state)

    assert result.reached == (1,)
    assert result.change_by_asset["UNKNOWN"] == 0


def test_a_scope_that_reaches_nothing_refuses_rather_than_reporting_no_loss() -> None:
    with pytest.raises(UnsupportedShockError, match="reached no exposure"):
        sector_shock(Decimal("-0.5"), "Utilities").apply(book())


# --------------------------------------------------------------------------- #
# FX moves rates, not prices
# --------------------------------------------------------------------------- #


def test_an_fx_shock_moves_the_rate_and_leaves_the_local_price_alone() -> None:
    result = fx_shock(Decimal("-0.20"), "EUR").apply(book())

    assert result.shocked_state.rates["EUR"] == Decimal("0.88")
    assert result.shocked_state.exposures[1].price == Decimal("100")
    assert result.change_by_asset["AAPL"] == 0
    assert result.change_by_asset["SAP"] < 0


def test_an_fx_shock_on_the_reporting_currency_is_refused() -> None:
    with pytest.raises(UnsupportedShockError, match="reporting currency"):
        fx_shock(Decimal("-0.2"), "USD").apply(book())


def test_an_fx_shock_on_a_currency_the_book_does_not_hold_is_refused() -> None:
    with pytest.raises(UnsupportedShockError, match="reaches no exposure"):
        fx_shock(Decimal("-0.2"), "JPY").apply(book())


def test_an_fx_shock_scoped_by_asset_is_refused_because_a_rate_is_per_pair() -> None:
    shock = Shock(ShockKind.FX, Decimal("-0.1"), of_assets("AAPL"))

    with pytest.raises(UnsupportedShockError, match="scoped by currency"):
        Scenario("bad", (shock,)).apply(book())


def test_a_missing_rate_refuses_rather_than_assuming_one_for_one() -> None:
    mixed = from_positions([("SAP", Decimal("100"), Decimal("100"), "EUR")], "USD")

    with pytest.raises(ScenarioValidationError, match="No rate supplied"):
        mixed.value()


# --------------------------------------------------------------------------- #
# Unsupported fields are refused, never skipped
# --------------------------------------------------------------------------- #


def test_a_volatility_shock_on_exposures_carrying_none_is_refused() -> None:
    plain = from_positions([("AAPL", Decimal("100"), Decimal("200"), "USD")], "USD")

    with pytest.raises(UnsupportedShockError, match="carries no volatility"):
        scenario("x", volatility_shock=Decimal("0.5")).apply(plain)


def test_a_liquidity_shock_on_exposures_carrying_none_is_refused() -> None:
    plain = from_positions([("AAPL", Decimal("100"), Decimal("200"), "USD")], "USD")

    with pytest.raises(UnsupportedShockError, match="no available_liquidity"):
        scenario("x", liquidity_shock=Decimal("-0.5")).apply(plain)


def test_a_volatility_shock_that_is_supported_scales_the_volatility() -> None:
    result = scenario("vol", volatility_shock=Decimal("1.0")).apply(book())

    assert result.shocked_state.exposures[0].volatility == pytest.approx(0.40)
    assert result.shocked_value == result.base_value


def test_a_liquidity_shock_that_is_supported_scales_the_depth() -> None:
    result = scenario("dry", liquidity_shock=Decimal("-0.75")).apply(book())

    assert result.shocked_state.exposures[0].available_liquidity == Decimal("12500.00")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_shock_below_minus_one_is_refused_rather_than_clamped() -> None:
    with pytest.raises(ScenarioValidationError, match="below zero"):
        Shock(ShockKind.PRICE, Decimal("-1.5"), everything())


def test_a_scenario_with_no_shocks_is_refused() -> None:
    with pytest.raises(ScenarioValidationError, match="no shock"):
        scenario("empty")

    with pytest.raises(ScenarioValidationError, match="no shocks"):
        Scenario("empty", ())


def test_an_unnamed_scenario_is_refused() -> None:
    with pytest.raises(ScenarioValidationError, match="no name"):
        Scenario("  ", (Shock(ShockKind.PRICE, Decimal("-0.1"), everything()),))


def test_a_duplicated_exposure_is_refused() -> None:
    exposure = ScenarioExposure("AAPL", Decimal("1"), Decimal("1"), "USD")

    with pytest.raises(ScenarioValidationError, match="Duplicate assets"):
        ScenarioState((exposure, exposure), "USD", {})


def test_a_named_but_empty_scope_is_refused() -> None:
    with pytest.raises(ScenarioValidationError, match="names nothing"):
        of_sector()

    with pytest.raises(ScenarioValidationError, match="names no currency"):
        fx_shock(Decimal("-0.1"))


def test_a_relative_change_against_a_worthless_book_is_refused() -> None:
    flat = from_positions([("AAPL", Decimal("0"), Decimal("200"), "USD")], "USD")
    result = scenario("x", price_shock=Decimal("-0.1")).apply(flat)

    with pytest.raises(ScenarioValidationError, match="no denominator"):
        _ = result.relative_change


# --------------------------------------------------------------------------- #
# Identity, composition and determinism
# --------------------------------------------------------------------------- #


def test_identity_is_derived_from_content_and_stable() -> None:
    first = scenario("crash", price_shock=Decimal("-0.30"))
    second = scenario("crash", price_shock=Decimal("-0.30"))

    assert first.identity() == second.identity()
    assert len(first.identity()) == 64


def test_a_different_magnitude_is_a_different_identity() -> None:
    assert (
        scenario("crash", price_shock=Decimal("-0.30")).identity()
        != scenario("crash", price_shock=Decimal("-0.31")).identity()
    )


def test_composition_preserves_order_and_order_matters_to_identity() -> None:
    first = flash_crash(Decimal("-0.10"))
    second = scenario("dry", liquidity_shock=Decimal("-0.50"))

    assert first.then(second).identity() != second.then(first).identity()
    assert first.then(second).shocks == (*first.shocks, *second.shocks)


def test_composition_does_not_modify_either_operand() -> None:
    first = flash_crash(Decimal("-0.10"))
    second = scenario("dry", liquidity_shock=Decimal("-0.50"))
    composed = first.then(second)

    assert len(first.shocks) == 1
    assert len(second.shocks) == 1
    assert len(composed.shocks) == 2


def test_applying_the_same_scenario_twice_gives_the_same_result() -> None:
    built = scenario("crash", price_shock=Decimal("-0.3"), volatility_shock=Decimal("2.0"))
    base = book()

    assert built.apply(base) == built.apply(base)


def test_a_composed_scenario_equals_applying_the_legs_in_sequence() -> None:
    base = book()
    first = scenario("a", price_shock=Decimal("-0.10"))
    second = scenario("b", price_shock=Decimal("-0.20"))

    composed = first.then(second).apply(base)
    sequential = second.apply(first.apply(base).shocked_state)

    assert composed.shocked_value == sequential.shocked_value


def test_rate_shock_requires_a_scope_because_sensitivity_is_not_universal() -> None:
    result = rate_shock(Decimal("-0.08"), of_assets("SAP")).apply(book())

    assert result.reached == (1,)


def test_a_currency_scope_selects_by_settlement_currency() -> None:
    shock = Shock(ShockKind.PRICE, Decimal("-0.1"), of_currency("EUR"))
    result = Scenario("eur-only", (shock,)).apply(book())

    assert result.reached == (1,)


# --------------------------------------------------------------------------- #
# Historical scenarios require data
# --------------------------------------------------------------------------- #


def test_a_historical_scenario_refuses_without_the_observations_it_names() -> None:
    with pytest.raises(ScenarioValidationError, match="will not invent a historical move"):
        CRISIS_2008.realize({})


def test_the_refusal_names_every_missing_observation_and_the_window() -> None:
    with pytest.raises(ScenarioValidationError) as raised:
        CRISIS_2008.realize({"equity_price": Decimal("-0.4")})

    message = str(raised.value)
    assert "volatility" in message
    assert "liquidity" in message
    assert CRISIS_2008.window in message


def test_a_historical_scenario_applies_once_its_observations_are_supplied() -> None:
    realized = CRISIS_2008.realize(
        {
            "equity_price": Decimal("-0.40"),
            "volatility": Decimal("1.50"),
            "liquidity": Decimal("-0.60"),
        }
    )
    result = realized.apply(book())

    assert result.scenario_name == "crisis_2008"
    assert result.relative_change == Decimal("-0.40")
    assert result.shocked_state.exposures[0].volatility == pytest.approx(0.50)


def test_an_observation_the_definition_does_not_require_is_refused() -> None:
    with pytest.raises(ScenarioValidationError, match="does not define"):
        CRISIS_2008.realize(
            {
                "equity_price": Decimal("-0.4"),
                "volatility": Decimal("1.5"),
                "liquidity": Decimal("-0.6"),
                "made_up": Decimal("0.1"),
            }
        )


def test_every_shipped_historical_scenario_is_a_contract_rather_than_numbers() -> None:
    """None of them can be applied without data, and all name a window."""

    for name, definition in HISTORICAL_SCENARIOS.items():
        assert definition.name == name
        assert definition.required_keys()
        assert ".." in definition.window
        with pytest.raises(ScenarioValidationError):
            definition.realize({})


def test_a_historical_scenario_can_be_scoped_when_realized() -> None:
    realized = CRISIS_2008.realize(
        {
            "equity_price": Decimal("-0.40"),
            "volatility": Decimal("1.50"),
            "liquidity": Decimal("-0.60"),
        },
        scopes={"equity_price": of_sector("Tech")},
    )
    result = realized.apply(book())

    assert result.reached[0] == 2
