"""Risk decomposition: stated methods, and a decomposition that reconciles."""

import math
from decimal import Decimal

import pytest

from alphalab.analytics.decomposition import (
    PositionRisk,
    VaRMethod,
    VaRPolicy,
    concentration,
    correlation_matrix,
    covariance_matrix,
    decompose,
    factor_exposure,
    gross_weights,
    leverage,
    liquidity_risk,
    percentile_loss,
    portfolio_beta,
    portfolio_volatility,
    risk_contributions,
    tail_ratio,
)
from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.metrics import value_at_risk
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import sample_variance

RETURNS_A = (
    0.010,
    -0.005,
    0.021,
    -0.013,
    0.004,
    0.017,
    -0.020,
    0.009,
    0.002,
    -0.008,
    0.014,
    -0.011,
)
RETURNS_B = (
    -0.004,
    0.012,
    -0.009,
    0.016,
    -0.002,
    -0.014,
    0.011,
    0.003,
    -0.007,
    0.010,
    -0.006,
    0.005,
)
RETURNS_C = (0.006, 0.002, 0.009, -0.003, 0.008, 0.001, -0.005, 0.007, 0.004, -0.002, 0.003, 0.000)

BOOK = (
    PositionRisk("AAA", Decimal("600000"), "USD", RETURNS_A),
    PositionRisk("BBB", Decimal("-250000"), "USD", RETURNS_B),
    PositionRisk("CCC", Decimal("150000"), "USD", RETURNS_C),
)
PORTFOLIO = tuple(
    0.6 * a - 0.25 * b + 0.15 * c for a, b, c in zip(RETURNS_A, RETURNS_B, RETURNS_C, strict=True)
)


def equity_curve() -> tuple[float, ...]:
    curve = [1_000_000.0]
    for value in PORTFOLIO:
        curve.append(curve[-1] * (1.0 + value))
    return tuple(curve)


# --------------------------------------------------------------------------- #
# The policy is the figure's provenance
# --------------------------------------------------------------------------- #


def test_the_three_methods_give_three_different_answers() -> None:
    """Which is why the method is carried rather than assumed."""

    figures = {
        method: VaRPolicy(method, 0.95).var(PORTFOLIO)
        for method in (VaRMethod.HISTORICAL, VaRMethod.GAUSSIAN, VaRMethod.CORNISH_FISHER)
    }

    assert len(set(figures.values())) == 3


def test_historical_var_delegates_to_the_existing_authority() -> None:
    """Not reimplemented beside it: the same function, negated once."""

    policy = VaRPolicy(VaRMethod.HISTORICAL, 0.95)

    assert policy.var(PORTFOLIO) == -value_at_risk(PORTFOLIO, 0.95)


def test_var_is_reported_as_a_positive_loss_magnitude() -> None:
    losing = (-0.05, -0.03, -0.09, -0.01, -0.07, -0.02, -0.04, -0.06)

    assert VaRPolicy(VaRMethod.HISTORICAL, 0.95).var(losing) > 0.0


def test_cvar_is_at_least_var_because_the_tail_lies_beyond_it() -> None:
    for method in (VaRMethod.HISTORICAL, VaRMethod.GAUSSIAN, VaRMethod.CORNISH_FISHER):
        policy = VaRPolicy(method, 0.95)

        assert policy.cvar(PORTFOLIO) >= policy.var(PORTFOLIO) - 1e-12


def test_a_higher_confidence_never_reports_a_smaller_loss() -> None:
    low = VaRPolicy(VaRMethod.HISTORICAL, 0.90).var(PORTFOLIO)
    high = VaRPolicy(VaRMethod.HISTORICAL, 0.99).var(PORTFOLIO)

    assert high >= low


def test_the_policy_identity_is_derived_and_stable() -> None:
    assert VaRPolicy(VaRMethod.HISTORICAL, 0.95).identity() == "HISTORICAL@0.95"
    assert VaRPolicy(VaRMethod.GAUSSIAN, 0.99).identity() == "GAUSSIAN@0.99"


def test_gaussian_var_matches_the_hand_computation() -> None:
    """``-(mean + z * sigma)`` with z the 5% standard normal quantile."""

    policy = VaRPolicy(VaRMethod.GAUSSIAN, 0.95)
    mean = sum(PORTFOLIO) / len(PORTFOLIO)
    sigma = math.sqrt(sample_variance(PORTFOLIO))

    assert policy.var(PORTFOLIO) == pytest.approx(-(mean - 1.6448536269514722 * sigma), rel=1e-12)


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.1, 1.5])
def test_a_confidence_outside_the_open_unit_interval_is_refused(confidence: float) -> None:
    with pytest.raises(AnalyticsValidationError, match="must lie strictly"):
        VaRPolicy(VaRMethod.HISTORICAL, confidence)


def test_one_observation_is_refused_by_every_method() -> None:
    for method in VaRMethod:
        with pytest.raises(AnalyticsValidationError, match="at least 2"):
            VaRPolicy(method, 0.95).var((0.01,))


def test_cornish_fisher_refuses_a_sample_too_small_for_its_moments() -> None:
    with pytest.raises(AnalyticsValidationError, match="at least 8"):
        VaRPolicy(VaRMethod.CORNISH_FISHER, 0.95).var((0.01, -0.02, 0.03))


def test_var_is_deterministic() -> None:
    for method in (VaRMethod.HISTORICAL, VaRMethod.GAUSSIAN, VaRMethod.CORNISH_FISHER):
        policy = VaRPolicy(method, 0.95)

        assert policy.var(PORTFOLIO) == policy.var(PORTFOLIO)
        assert policy.cvar(PORTFOLIO) == policy.cvar(PORTFOLIO)


# --------------------------------------------------------------------------- #
# The decomposition reconciles
# --------------------------------------------------------------------------- #


def test_risk_contributions_sum_to_portfolio_volatility() -> None:
    """The Euler property, which is what makes this a decomposition."""

    total = portfolio_volatility(BOOK)
    parts = risk_contributions(BOOK)

    assert sum(parts.values()) == pytest.approx(total, rel=1e-12)


def test_a_hedging_position_contributes_negative_risk_and_that_is_kept() -> None:
    """A short leg correlated with the book removes risk, and keeps its sign."""

    hedged = (
        PositionRisk("LONG", Decimal("1000"), "USD", RETURNS_A),
        PositionRisk("HEDGE", Decimal("-300"), "USD", RETURNS_A),
        PositionRisk("OTHER", Decimal("200"), "USD", RETURNS_C),
    )
    parts = risk_contributions(hedged)

    assert parts["HEDGE"] < 0.0
    assert sum(parts.values()) == pytest.approx(portfolio_volatility(hedged), rel=1e-12)


def test_a_perfectly_hedged_book_refuses_rather_than_splitting_zero() -> None:
    """Exactly offsetting legs have no risk, so there is nothing to apportion."""

    opposed = (
        PositionRisk("LONG", Decimal("100"), "USD", RETURNS_A),
        PositionRisk("SHORT", Decimal("-100"), "USD", RETURNS_A),
    )

    assert portfolio_volatility(opposed) == 0.0
    with pytest.raises(AnalyticsValidationError, match="no risk to decompose"):
        risk_contributions(opposed)


def test_gross_weights_are_signed_and_absolutely_sum_to_one() -> None:
    weights = gross_weights(BOOK)

    assert sum(abs(weight) for weight in weights.values()) == pytest.approx(1.0, rel=1e-12)
    assert weights["BBB"] < 0.0


def test_the_covariance_diagonal_is_exactly_the_repository_s_sample_variance() -> None:
    matrix = covariance_matrix(BOOK)

    assert matrix["AAA"]["AAA"] == sample_variance(RETURNS_A)


def test_the_covariance_matrix_is_symmetric() -> None:
    matrix = covariance_matrix(BOOK)

    assert matrix["AAA"]["BBB"] == matrix["BBB"]["AAA"]


def test_correlations_lie_in_the_unit_interval_and_are_one_on_the_diagonal() -> None:
    matrix = correlation_matrix(BOOK)

    for first, row in matrix.items():
        assert row[first] == pytest.approx(1.0, rel=1e-12)
        assert all(-1.0 - 1e-12 <= value <= 1.0 + 1e-12 for value in row.values())


def test_a_constant_series_refuses_a_correlation_rather_than_reporting_zero() -> None:
    flat = (
        PositionRisk("FLAT", Decimal("100"), "USD", (0.01,) * len(RETURNS_A)),
        PositionRisk("AAA", Decimal("100"), "USD", RETURNS_A),
    )

    with pytest.raises(AnalyticsValidationError, match="constant return series"):
        correlation_matrix(flat)


# --------------------------------------------------------------------------- #
# The other measurements
# --------------------------------------------------------------------------- #


def test_concentration_reports_effective_positions() -> None:
    equal = tuple(
        PositionRisk(name, Decimal("100"), "USD", RETURNS_A) for name in ("A", "B", "C", "D")
    )
    metrics = concentration(equal)

    assert metrics.herfindahl == pytest.approx(0.25, rel=1e-12)
    assert metrics.effective_positions == pytest.approx(4.0, rel=1e-12)


def test_concentration_names_the_largest_position() -> None:
    assert concentration(BOOK).largest_asset_id == "AAA"


def test_leverage_separates_gross_from_net() -> None:
    metrics = leverage(BOOK, Decimal("1000000"))

    assert metrics.gross_exposure == Decimal("1000000")
    assert metrics.net_exposure == Decimal("500000")
    assert metrics.gross_leverage == Decimal("1")
    assert metrics.net_leverage == Decimal("0.5")
    assert metrics.short_exposure == Decimal("250000")


def test_leverage_against_no_capital_is_refused_rather_than_called_large() -> None:
    with pytest.raises(AnalyticsValidationError, match="no capital"):
        leverage(BOOK, Decimal("0"))


def test_beta_uses_the_repository_s_one_regression() -> None:
    from alphalab.common.statistics import linear_regression

    assert (
        portfolio_beta(PORTFOLIO, RETURNS_A)
        == linear_regression(list(PORTFOLIO), list(RETURNS_A)).slope
    )


def test_beta_against_a_constant_benchmark_is_refused() -> None:
    with pytest.raises(AlphaLabValidationError, match="constant"):
        portfolio_beta(PORTFOLIO, (0.01,) * len(PORTFOLIO))


def test_factor_exposure_is_the_weighted_loading() -> None:
    exposure = factor_exposure(BOOK, {"AAA": {"value": 0.8}, "BBB": {"value": -0.4}})

    assert exposure["value"] == pytest.approx(0.6 * 0.8 + (-0.25) * (-0.4), rel=1e-12)


def test_factor_loadings_for_an_asset_not_held_are_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="not in the book"):
        factor_exposure(BOOK, {"ZZZ": {"value": 1.0}})


def test_liquidity_risk_names_the_worst_position() -> None:
    risk = liquidity_risk(
        BOOK,
        units={"AAA": Decimal("12000"), "BBB": Decimal("5000"), "CCC": Decimal("3000")},
        average_daily_volume={
            "AAA": Decimal("900000"),
            "BBB": Decimal("40000"),
            "CCC": Decimal("250000"),
        },
        participation_limit=Decimal("0.10"),
    )

    assert risk.worst_asset_id == "BBB"
    assert risk.worst_days == pytest.approx(1.25, rel=1e-12)
    assert risk.participation_limit == Decimal("0.10")


def test_liquidity_risk_refuses_a_position_with_no_volume_supplied() -> None:
    with pytest.raises(AnalyticsValidationError, match="unmeasured"):
        liquidity_risk(
            BOOK,
            units={"AAA": Decimal("1")},
            average_daily_volume={"AAA": Decimal("1000")},
            participation_limit=Decimal("0.1"),
        )


def test_tail_ratio_is_at_least_one() -> None:
    assert tail_ratio(VaRPolicy(VaRMethod.HISTORICAL, 0.95), PORTFOLIO) >= 1.0 - 1e-12


def test_percentile_loss_applies_the_sign_convention_once() -> None:
    from alphalab.common.statistics import percentile

    assert percentile_loss(PORTFOLIO, 0.05) == -percentile(list(PORTFOLIO), 0.05)


# --------------------------------------------------------------------------- #
# The whole picture
# --------------------------------------------------------------------------- #


def test_decompose_reports_every_measurement_and_reconciles() -> None:
    result = decompose(
        BOOK,
        PORTFOLIO,
        Decimal("1000000"),
        equity_curve(),
        VaRPolicy(VaRMethod.HISTORICAL, 0.95),
        benchmark=RETURNS_A,
        loadings={"AAA": {"value": 0.8}},
        units={"AAA": Decimal("12000"), "BBB": Decimal("5000"), "CCC": Decimal("3000")},
        average_daily_volume={
            "AAA": Decimal("900000"),
            "BBB": Decimal("40000"),
            "CCC": Decimal("250000"),
        },
        participation_limit=Decimal("0.10"),
    )

    assert sum(result.risk_contributions.values()) == pytest.approx(result.volatility, rel=1e-12)
    assert result.beta is not None
    assert result.liquidity is not None
    assert result.max_drawdown >= 0.0
    assert result.policy.identity() == "HISTORICAL@0.95"


def test_unmeasured_inputs_stay_unmeasured_rather_than_becoming_zero() -> None:
    result = decompose(
        BOOK, PORTFOLIO, Decimal("1000000"), equity_curve(), VaRPolicy(VaRMethod.HISTORICAL, 0.95)
    )

    assert result.beta is None
    assert result.factor_exposure == {}
    assert result.liquidity is None


def test_partial_liquidity_inputs_are_refused_rather_than_defaulted() -> None:
    with pytest.raises(AnalyticsValidationError, match="together"):
        decompose(
            BOOK,
            PORTFOLIO,
            Decimal("1000000"),
            equity_curve(),
            VaRPolicy(VaRMethod.HISTORICAL, 0.95),
            units={"AAA": Decimal("1")},
        )


def test_a_mixed_currency_book_is_refused_rather_than_added_up() -> None:
    mixed = (
        PositionRisk("AAA", Decimal("100"), "USD", RETURNS_A),
        PositionRisk("SAP", Decimal("100"), "EUR", RETURNS_B),
    )

    with pytest.raises(AnalyticsValidationError, match="different currencies"):
        portfolio_volatility(mixed)


def test_an_empty_book_is_refused_rather_than_called_riskless() -> None:
    with pytest.raises(AnalyticsValidationError, match="empty book"):
        portfolio_volatility(())


def test_a_duplicated_position_is_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="Duplicate assets"):
        portfolio_volatility((BOOK[0], BOOK[0]))


def test_mismatched_series_lengths_are_refused() -> None:
    ragged = (
        PositionRisk("AAA", Decimal("100"), "USD", RETURNS_A),
        PositionRisk("BBB", Decimal("100"), "USD", RETURNS_B[:5]),
    )

    with pytest.raises(AnalyticsValidationError, match="differing lengths"):
        portfolio_volatility(ragged)


def test_the_decomposition_is_deterministic() -> None:
    policy = VaRPolicy(VaRMethod.CORNISH_FISHER, 0.99)
    first = decompose(BOOK, PORTFOLIO, Decimal("1000000"), equity_curve(), policy)
    second = decompose(BOOK, PORTFOLIO, Decimal("1000000"), equity_curve(), policy)

    assert first == second
