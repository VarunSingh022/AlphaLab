"""Comprehensive tests for the Macro Engine: indicators, central bank, curves, rates, GDP."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.macro import (
    CentralBankEvent,
    Frequency,
    IndicatorMetadata,
    IndicatorObservation,
    MacroInputError,
    PolicyAction,
    YieldCurve,
    YieldCurvePoint,
    gdp_growth_rate,
    is_inverted,
    known_as_of,
    rate_change_bps,
    real_gdp,
    real_interest_rate_approx,
    real_interest_rate_exact,
    sorted_by_tenor,
    spread,
    surprise,
    three_month_ten_year_spread,
    two_year_ten_year_spread,
    yield_at_tenor,
)

DAY = 86400.0


# --------------------------------------------------------------------------- #
# IndicatorMetadata / IndicatorObservation
# --------------------------------------------------------------------------- #


def test_indicator_metadata_is_immutable() -> None:
    meta = IndicatorMetadata(
        indicator_id="US_CPI_YOY",
        name="US CPI YoY",
        country="US",
        frequency=Frequency.MONTHLY,
        units="%",
    )
    with pytest.raises(FrozenInstanceError):
        meta.name = "renamed"  # type: ignore[misc]


def test_observation_rejects_release_before_reference_period() -> None:
    with pytest.raises(MacroInputError):
        IndicatorObservation(
            indicator_id="X", reference_period=1000.0, release_date=500.0, value=Decimal("1.0")
        )


def test_observation_allows_release_equal_to_reference_period() -> None:
    obs = IndicatorObservation(
        indicator_id="X", reference_period=1000.0, release_date=1000.0, value=Decimal("1.0")
    )
    assert obs.release_date == obs.reference_period


def test_observation_is_immutable() -> None:
    obs = IndicatorObservation(
        indicator_id="X", reference_period=0.0, release_date=100.0, value=Decimal("1.0")
    )
    with pytest.raises(FrozenInstanceError):
        obs.value = Decimal("2.0")  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Economic surprise
# --------------------------------------------------------------------------- #


def test_surprise_computes_actual_minus_consensus() -> None:
    obs = IndicatorObservation(
        indicator_id="X",
        reference_period=0.0,
        release_date=100.0,
        value=Decimal("110"),
        consensus_estimate=Decimal("108"),
    )
    assert surprise(obs) == Decimal("2")


def test_surprise_negative_when_actual_below_consensus() -> None:
    obs = IndicatorObservation(
        indicator_id="X",
        reference_period=0.0,
        release_date=100.0,
        value=Decimal("100"),
        consensus_estimate=Decimal("108"),
    )
    assert surprise(obs) == Decimal("-8")


def test_surprise_none_without_consensus() -> None:
    obs = IndicatorObservation(
        indicator_id="X", reference_period=0.0, release_date=100.0, value=Decimal("100")
    )
    assert surprise(obs) is None


# --------------------------------------------------------------------------- #
# Point-in-time correctness: the core value-add of this package
# --------------------------------------------------------------------------- #


def _revision_scenario() -> tuple[IndicatorObservation, IndicatorObservation, IndicatorObservation]:
    jan_orig = IndicatorObservation(
        indicator_id="GDP", reference_period=0.0, release_date=31.0 * DAY, value=Decimal("100")
    )
    jan_revised = IndicatorObservation(
        indicator_id="GDP",
        reference_period=0.0,
        release_date=59.0 * DAY,
        value=Decimal("105"),
        is_revision=True,
    )
    feb_orig = IndicatorObservation(
        indicator_id="GDP",
        reference_period=31.0 * DAY,
        release_date=59.0 * DAY,
        value=Decimal("110"),
    )
    return jan_orig, jan_revised, feb_orig


def test_known_as_of_returns_none_before_any_release() -> None:
    jan_orig, jan_revised, feb_orig = _revision_scenario()
    result = known_as_of((jan_orig, jan_revised, feb_orig), as_of=1.0 * DAY)
    assert result is None


def test_known_as_of_returns_original_before_revision_exists() -> None:
    """This is the exact look-ahead-bias scenario this module exists to prevent:
    querying mid-February must not see the March revision or the February reading."""
    jan_orig, jan_revised, feb_orig = _revision_scenario()
    result = known_as_of((jan_orig, jan_revised, feb_orig), as_of=45.0 * DAY)
    assert result is not None
    assert result.value == Decimal("100")
    assert result.is_revision is False


def test_known_as_of_returns_newest_period_after_all_releases() -> None:
    jan_orig, jan_revised, feb_orig = _revision_scenario()
    result = known_as_of((jan_orig, jan_revised, feb_orig), as_of=60.0 * DAY)
    assert result is not None
    assert result.value == Decimal("110")


def test_known_as_of_with_empty_observations_returns_none() -> None:
    assert known_as_of((), as_of=1000.0) is None


# --------------------------------------------------------------------------- #
# CentralBankEvent
# --------------------------------------------------------------------------- #


def test_central_bank_event_hike_requires_higher_rate_after() -> None:
    with pytest.raises(MacroInputError):
        CentralBankEvent(
            bank_id="FED",
            event_date=0.0,
            action=PolicyAction.HIKE,
            rate_before=Decimal("0.05"),
            rate_after=Decimal("0.05"),
        )


def test_central_bank_event_cut_requires_lower_rate_after() -> None:
    with pytest.raises(MacroInputError):
        CentralBankEvent(
            bank_id="FED",
            event_date=0.0,
            action=PolicyAction.CUT,
            rate_before=Decimal("0.05"),
            rate_after=Decimal("0.06"),
        )


def test_central_bank_event_hold_requires_equal_rates() -> None:
    with pytest.raises(MacroInputError):
        CentralBankEvent(
            bank_id="FED",
            event_date=0.0,
            action=PolicyAction.HOLD,
            rate_before=Decimal("0.05"),
            rate_after=Decimal("0.06"),
        )


def test_rate_change_bps_hike() -> None:
    event = CentralBankEvent(
        bank_id="FED",
        event_date=0.0,
        action=PolicyAction.HIKE,
        rate_before=Decimal("0.0500"),
        rate_after=Decimal("0.0525"),
    )
    assert rate_change_bps(event) == 25


def test_rate_change_bps_cut_is_negative() -> None:
    event = CentralBankEvent(
        bank_id="RBI",
        event_date=0.0,
        action=PolicyAction.CUT,
        rate_before=Decimal("0.0650"),
        rate_after=Decimal("0.0625"),
    )
    assert rate_change_bps(event) == -25


def test_rate_change_bps_hold_is_zero() -> None:
    event = CentralBankEvent(
        bank_id="ECB",
        event_date=0.0,
        action=PolicyAction.HOLD,
        rate_before=Decimal("0.04"),
        rate_after=Decimal("0.04"),
    )
    assert rate_change_bps(event) == 0


# --------------------------------------------------------------------------- #
# YieldCurve: interpolation and the two named spreads
# --------------------------------------------------------------------------- #


def _flat_curve(rate: str = "0.045") -> YieldCurve:
    points = (
        YieldCurvePoint(tenor_years=Decimal("0.25"), yield_rate=Decimal(rate)),
        YieldCurvePoint(tenor_years=Decimal("2"), yield_rate=Decimal(rate)),
        YieldCurvePoint(tenor_years=Decimal("10"), yield_rate=Decimal(rate)),
    )
    return YieldCurve(country="US", currency="USD", timestamp=0.0, points=points)


def _inverted_curve() -> YieldCurve:
    points = (
        YieldCurvePoint(tenor_years=Decimal("0.25"), yield_rate=Decimal("0.052")),
        YieldCurvePoint(tenor_years=Decimal("2"), yield_rate=Decimal("0.048")),
        YieldCurvePoint(tenor_years=Decimal("10"), yield_rate=Decimal("0.042")),
    )
    return YieldCurve(country="US", currency="USD", timestamp=0.0, points=points)


def test_yield_at_tenor_exact_match() -> None:
    curve = _inverted_curve()
    assert yield_at_tenor(curve, Decimal("2")) == Decimal("0.048")


def test_yield_at_tenor_interpolates() -> None:
    points = (
        YieldCurvePoint(tenor_years=Decimal("2"), yield_rate=Decimal("0.040")),
        YieldCurvePoint(tenor_years=Decimal("10"), yield_rate=Decimal("0.050")),
    )
    curve = YieldCurve(country="US", currency="USD", timestamp=0.0, points=points)
    result = yield_at_tenor(curve, Decimal("6"))
    assert result == Decimal("0.045")


def test_yield_at_tenor_returns_none_outside_range() -> None:
    curve = _inverted_curve()
    assert yield_at_tenor(curve, Decimal("30")) is None


def test_yield_at_tenor_returns_none_for_empty_curve() -> None:
    curve = YieldCurve(country="US", currency="USD", timestamp=0.0, points=())
    assert yield_at_tenor(curve, Decimal("2")) is None


def test_sorted_by_tenor_orders_ascending() -> None:
    curve = _inverted_curve()
    ordered = sorted_by_tenor(curve)
    assert [p.tenor_years for p in ordered] == sorted(p.tenor_years for p in curve.points)


def test_spread_rejects_short_not_less_than_long() -> None:
    curve = _flat_curve()
    with pytest.raises(MacroInputError):
        spread(curve, Decimal("10"), Decimal("2"))


def test_two_year_ten_year_spread_negative_when_inverted() -> None:
    curve = _inverted_curve()
    assert two_year_ten_year_spread(curve) == Decimal("-0.006")


def test_three_month_ten_year_spread_is_different_from_2s10s() -> None:
    """The whole point of naming these separately -- they are not the same number."""
    curve = _inverted_curve()
    two_ten = two_year_ten_year_spread(curve)
    three_month_ten = three_month_ten_year_spread(curve)
    assert two_ten != three_month_ten
    assert three_month_ten == Decimal("-0.010")


def test_two_year_ten_year_spread_positive_for_normal_curve() -> None:
    points = (
        YieldCurvePoint(tenor_years=Decimal("2"), yield_rate=Decimal("0.035")),
        YieldCurvePoint(tenor_years=Decimal("10"), yield_rate=Decimal("0.045")),
    )
    curve = YieldCurve(country="US", currency="USD", timestamp=0.0, points=points)
    assert two_year_ten_year_spread(curve) == Decimal("0.010")


def test_is_inverted_true_for_inverted_curve() -> None:
    assert is_inverted(_inverted_curve()) is True


def test_is_inverted_false_for_normal_curve() -> None:
    points = (
        YieldCurvePoint(tenor_years=Decimal("2"), yield_rate=Decimal("0.035")),
        YieldCurvePoint(tenor_years=Decimal("10"), yield_rate=Decimal("0.045")),
    )
    curve = YieldCurve(country="US", currency="USD", timestamp=0.0, points=points)
    assert is_inverted(curve) is False


def test_is_inverted_returns_none_not_false_when_unavailable() -> None:
    """None (unknown) must never be conflated with False (not inverted)."""
    curve = YieldCurve(country="US", currency="USD", timestamp=0.0, points=())
    assert is_inverted(curve) is None


def test_yield_curve_point_is_immutable() -> None:
    point = YieldCurvePoint(tenor_years=Decimal("2"), yield_rate=Decimal("0.04"))
    with pytest.raises(FrozenInstanceError):
        point.yield_rate = Decimal("0.05")  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Real interest rate: exact vs. approximate Fisher equation
# --------------------------------------------------------------------------- #


def test_real_interest_rate_exact_matches_hand_computed_value() -> None:
    """nominal=5%, inflation=3% -> (1.05/1.03)-1 = 0.019417..."""
    result = real_interest_rate_exact(Decimal("0.05"), Decimal("0.03"))
    assert result == pytest.approx(Decimal("0.019417"), abs=Decimal("0.000001"))


def test_real_interest_rate_approx_is_simple_subtraction() -> None:
    result = real_interest_rate_approx(Decimal("0.05"), Decimal("0.03"))
    assert result == Decimal("0.02")


def test_exact_and_approx_diverge_at_higher_rates() -> None:
    """The whole reason both functions exist: they are not interchangeable."""
    exact = real_interest_rate_exact(Decimal("0.15"), Decimal("0.10"))
    approx = real_interest_rate_approx(Decimal("0.15"), Decimal("0.10"))
    assert exact != approx
    assert abs(exact - approx) > Decimal("0.001")


def test_real_interest_rate_exact_rejects_negative_hundred_percent_inflation() -> None:
    with pytest.raises(MacroInputError):
        real_interest_rate_exact(Decimal("0.05"), Decimal("-1.0"))


# --------------------------------------------------------------------------- #
# GDP
# --------------------------------------------------------------------------- #


def test_gdp_growth_rate_positive() -> None:
    result = gdp_growth_rate(Decimal("110"), Decimal("100"))
    assert result == Decimal("0.10")


def test_gdp_growth_rate_negative_for_contraction() -> None:
    result = gdp_growth_rate(Decimal("95"), Decimal("100"))
    assert result == Decimal("-0.05")


def test_gdp_growth_rate_rejects_non_positive_prior() -> None:
    with pytest.raises(MacroInputError):
        gdp_growth_rate(Decimal("100"), Decimal("0"))


def test_real_gdp_matches_hand_computed_value() -> None:
    """nominal=110, deflator=110 (10% price growth) -> real=100."""
    result = real_gdp(Decimal("110"), Decimal("110"))
    assert result == Decimal("100")


def test_real_gdp_equals_nominal_at_base_deflator() -> None:
    result = real_gdp(Decimal("100"), Decimal("100"))
    assert result == Decimal("100")


def test_real_gdp_rejects_non_positive_deflator() -> None:
    with pytest.raises(MacroInputError):
        real_gdp(Decimal("100"), Decimal("0"))
