"""The v3.8 risk model: covariance, correlation, loadings, classification, Euler.

Every value here is checked against an independent computation -- a hand
calculation, :mod:`alphalab.common.statistics` called directly, or a numerical
derivative -- rather than against the implementation it tests.
"""

from __future__ import annotations

import math
import random

import pytest

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import (
    Classification,
    CovarianceMatrix,
    DefinitenessKind,
    FactorLoadings,
    euler_decomposition,
    herfindahl_index,
    portfolio_factor_exposures,
)
from alphalab.common.statistics import pearson_correlation, sample_covariance, sample_variance

ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))


def covariance(**overrides: object) -> CovarianceMatrix:
    arguments: dict[str, object] = {
        "currency": "USD",
        "period": "1M",
        "source": "unit test",
        "observations": None,
    }
    arguments.update(overrides)
    return CovarianceMatrix.from_rows(("A", "B", "C"), ROWS, **arguments)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Construction and canonical order
# --------------------------------------------------------------------------- #


def test_from_rows_puts_assets_in_canonical_order() -> None:
    shuffled = CovarianceMatrix.from_rows(
        ("C", "A", "B"),
        ((0.16, 0.0, 0.02), (0.0, 0.04, 0.01), (0.02, 0.01, 0.09)),
        currency="USD",
        period="1M",
        source="unit test",
        observations=None,
    )

    assert shuffled.assets == ("A", "B", "C")
    assert shuffled.values == ROWS
    assert shuffled.covariance_id == covariance().covariance_id


def test_of_reads_a_complete_nested_mapping() -> None:
    nested = {
        "A": {"A": 0.04, "B": 0.01, "C": 0.0},
        "B": {"A": 0.01, "B": 0.09, "C": 0.02},
        "C": {"A": 0.0, "B": 0.02, "C": 0.16},
    }
    matrix = CovarianceMatrix.of(
        nested, currency="USD", period="1M", source="unit test", observations=None
    )

    assert matrix.values == ROWS
    assert matrix.as_mapping()["B"]["C"] == 0.02


def test_a_missing_cell_is_refused_rather_than_read_as_zero() -> None:
    nested = {"A": {"A": 0.04, "B": 0.01}, "B": {"B": 0.09}}
    with pytest.raises(AnalyticsValidationError, match="A missing covariance is not zero"):
        CovarianceMatrix.of(nested, currency="USD", period="1M", source="t", observations=None)


def test_columns_without_rows_are_refused() -> None:
    nested = {"A": {"A": 0.04, "Z": 0.0}}
    with pytest.raises(AnalyticsValidationError, match="must be square"):
        CovarianceMatrix.of(nested, currency="USD", period="1M", source="t", observations=None)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (((0.04, 0.01), (0.02, 0.09)), "symmetric"),
        (((-0.04, 0.0), (0.0, 0.09)), "cannot be negative"),
        (((0.04, float("nan")), (float("nan"), 0.09)), "finite"),
        (((0.04, 0.01),), "must be square"),
        (((0.04,), (0.01, 0.09)), "must be square"),
    ],
)
def test_malformed_matrices_are_refused(rows: tuple[tuple[float, ...], ...], message: str) -> None:
    with pytest.raises(AnalyticsValidationError, match=message):
        CovarianceMatrix.from_rows(
            ("A", "B"), rows, currency="USD", period="1M", source="t", observations=None
        )


def test_duplicated_and_blank_assets_are_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="more than once"):
        CovarianceMatrix.from_rows(
            ("A", "A"), ((1.0, 0.0), (0.0, 1.0)), currency="USD", period="1M", source="t",
            observations=None,
        )  # fmt: skip
    with pytest.raises(AnalyticsValidationError, match="blank"):
        CovarianceMatrix.from_rows(
            (" ",), ((1.0,),), currency="USD", period="1M", source="t", observations=None
        )
    with pytest.raises(AnalyticsValidationError, match="no assets"):
        CovarianceMatrix.from_rows(
            (), (), currency="USD", period="1M", source="t", observations=None
        )


@pytest.mark.parametrize("field", ["currency", "period", "source"])
def test_unit_and_provenance_are_required(field: str) -> None:
    with pytest.raises(AnalyticsValidationError, match=field):
        covariance(**{field: ""})


def test_direct_construction_must_already_be_canonical() -> None:
    with pytest.raises(AnalyticsValidationError, match="sorted order"):
        CovarianceMatrix(("B", "A"), ((1.0, 0.0), (0.0, 1.0)), "USD", "1M", "t", None)


@pytest.mark.parametrize("observations", [0, 1, True, 2.5])
def test_observations_must_be_a_real_sample_size(observations: object) -> None:
    with pytest.raises(AnalyticsValidationError, match="observations"):
        covariance(observations=observations)


def test_a_derivation_needs_its_parent_and_the_reverse() -> None:
    with pytest.raises(AnalyticsValidationError, match="parent"):
        CovarianceMatrix(("A",), ((1.0,),), "USD", "1M", "t", None, parent_id="x", derivation=None)
    with pytest.raises(AnalyticsValidationError, match="parent"):
        CovarianceMatrix(("A",), ((1.0,),), "USD", "1M", "t", None, parent_id=None, derivation="y")


# --------------------------------------------------------------------------- #
# Estimation from returns: the one estimator
# --------------------------------------------------------------------------- #


def test_sample_uses_the_repository_s_estimator_cell_by_cell() -> None:
    rng = random.Random(7)
    returns = {name: [rng.gauss(0.0, 0.02) for _ in range(40)] for name in ("X", "Y", "Z")}
    matrix = CovarianceMatrix.sample(returns, currency="EUR", period="1D", source="draws")

    assert matrix.observations == 40
    for first in ("X", "Y", "Z"):
        assert matrix.variance(first) == sample_variance(returns[first])
        for second in ("X", "Y", "Z"):
            assert matrix.covariance(first, second) == sample_covariance(
                returns[first], returns[second]
            )


def test_sample_refuses_misaligned_or_short_series() -> None:
    with pytest.raises(AnalyticsValidationError, match="differing lengths"):
        CovarianceMatrix.sample(
            {"A": [0.1, 0.2], "B": [0.1]}, currency="U", period="1D", source="s"
        )
    with pytest.raises(AnalyticsValidationError, match="at least 2"):
        CovarianceMatrix.sample({"A": [0.1]}, currency="U", period="1D", source="s")
    with pytest.raises(AnalyticsValidationError, match="at least one"):
        CovarianceMatrix.sample({}, currency="U", period="1D", source="s")
    with pytest.raises(AnalyticsValidationError, match="finite"):
        CovarianceMatrix.sample({"A": [0.1, float("inf")]}, currency="U", period="1D", source="s")


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_the_identity_names_every_fact_the_claim_rests_on() -> None:
    base = covariance().covariance_id

    assert covariance().covariance_id == base
    assert covariance(currency="EUR").covariance_id != base
    assert covariance(period="1D").covariance_id != base
    assert covariance(source="another model").covariance_id != base
    assert covariance(observations=60).covariance_id != base


def test_the_identity_keeps_a_value_s_exact_float() -> None:
    nudged = CovarianceMatrix.from_rows(
        ("A", "B", "C"),
        ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, math.nextafter(0.16, 1.0))),
        currency="USD",
        period="1M",
        source="unit test",
        observations=None,
    )

    assert nudged.covariance_id != covariance().covariance_id


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def test_reads_by_asset() -> None:
    matrix = covariance()

    assert matrix.index("B") == 1
    assert matrix.covariance("A", "B") == 0.01
    assert matrix.variance("C") == 0.16
    assert matrix.volatility("A") == math.sqrt(0.04)
    assert matrix.covers(("A", "C"))
    assert not matrix.covers(("A", "Q"))


def test_an_uncovered_asset_is_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="not covered"):
        covariance().variance("Q")


# --------------------------------------------------------------------------- #
# Derivation: recorded, exact, never silent
# --------------------------------------------------------------------------- #


def test_restriction_is_exact_and_records_its_parent() -> None:
    parent = covariance()
    child = parent.restricted(("C", "A"))

    assert child.assets == ("A", "C")
    assert child.values == ((0.04, 0.0), (0.0, 0.16))
    assert child.parent_id == parent.covariance_id
    assert child.derivation is not None and "restricted to 2 of 3" in child.derivation
    assert parent.restricted(("A", "B", "C")) is parent


def test_restriction_refuses_nothing_and_unknowns() -> None:
    with pytest.raises(AnalyticsValidationError, match="no assets"):
        covariance().restricted(())
    with pytest.raises(AnalyticsValidationError, match="not covered"):
        covariance().restricted(("A", "Q"))


def test_a_ridge_adds_to_the_diagonal_only_and_is_recorded() -> None:
    parent = covariance()
    ridged = parent.with_ridge(0.001)

    for row in range(3):
        for column in range(3):
            expected = ROWS[row][column] + (0.001 if row == column else 0.0)
            assert ridged.values[row][column] == expected
    assert ridged.parent_id == parent.covariance_id
    assert ridged.derivation == "ridge: C + 0.001 * I"
    assert ridged.covariance_id != parent.covariance_id


@pytest.mark.parametrize("delta", [0.0, -1.0, float("inf")])
def test_a_ridge_must_be_positive_and_finite(delta: float) -> None:
    with pytest.raises(AnalyticsValidationError):
        covariance().with_ridge(delta)


def test_diagonal_shrinkage_keeps_variances_and_scales_covariances() -> None:
    shrunk = covariance().with_diagonal_shrinkage(0.25)

    assert shrunk.values[0][0] == 0.04
    assert shrunk.values[0][1] == 0.75 * 0.01
    assert shrunk.values[1][2] == shrunk.values[2][1]
    assert shrunk.derivation is not None and "0.25" in shrunk.derivation
    assert covariance().with_diagonal_shrinkage(1.0).values[1][2] == 0.0


@pytest.mark.parametrize("intensity", [0.0, 1.5, -0.1])
def test_shrinkage_intensity_must_lie_in_the_unit_interval(intensity: float) -> None:
    with pytest.raises(AnalyticsValidationError, match=r"\(0, 1\]"):
        covariance().with_diagonal_shrinkage(intensity)


# --------------------------------------------------------------------------- #
# Definiteness
# --------------------------------------------------------------------------- #


def test_a_positive_definite_matrix_is_classified_with_its_pivots() -> None:
    evidence = covariance().definiteness()

    assert evidence.kind is DefinitenessKind.POSITIVE_DEFINITE
    assert evidence.rank == 3
    assert evidence.largest_pivot == 0.16
    assert 0.0 < evidence.pivot_ratio <= 1.0
    assert evidence.zero_variance_assets == ()
    assert covariance().require_positive_definite("a test") == evidence


def test_a_duplicated_asset_makes_a_singular_matrix_of_rank_one_less() -> None:
    returns = [0.01, -0.02, 0.03, 0.0, 0.015]
    matrix = CovarianceMatrix.sample(
        {"A": returns, "B": returns, "C": [0.02, 0.01, -0.01, 0.0, 0.005]},
        currency="USD",
        period="1D",
        source="t",
    )
    evidence = matrix.definiteness()

    assert evidence.kind is DefinitenessKind.SINGULAR
    assert evidence.rank == 2
    with pytest.raises(AnalyticsValidationError, match=r"singular \(rank 2 of 3\)"):
        matrix.require_positive_definite("A test")


def test_more_assets_than_observations_is_singular() -> None:
    rng = random.Random(3)
    matrix = CovarianceMatrix.sample(
        {f"S{index}": [rng.gauss(0.0, 0.01) for _ in range(4)] for index in range(6)},
        currency="USD",
        period="1D",
        source="t",
    )

    assert matrix.definiteness().kind is DefinitenessKind.SINGULAR
    assert matrix.definiteness().rank <= 3


def test_a_zero_variance_asset_is_named() -> None:
    matrix = CovarianceMatrix.from_rows(
        ("A", "CASH"), ((0.04, 0.0), (0.0, 0.0)), currency="USD", period="1M", source="t",
        observations=None,
    )  # fmt: skip

    evidence = matrix.definiteness()
    assert evidence.kind is DefinitenessKind.SINGULAR
    assert evidence.zero_variance_assets == ("CASH",)
    with pytest.raises(AnalyticsValidationError, match="Zero-variance assets: \\['CASH'\\]"):
        matrix.require_positive_definite("A test")


def test_a_ridge_repairs_a_singular_matrix_explicitly() -> None:
    matrix = CovarianceMatrix.from_rows(
        ("A", "CASH"), ((0.04, 0.0), (0.0, 0.0)), currency="USD", period="1M", source="t",
        observations=None,
    )  # fmt: skip

    assert matrix.with_ridge(1e-6).definiteness().kind is DefinitenessKind.POSITIVE_DEFINITE


def test_an_indefinite_matrix_is_named_as_such() -> None:
    matrix = CovarianceMatrix.from_rows(
        ("A", "B"), ((0.04, 0.05), (0.05, 0.04)), currency="USD", period="1M", source="t",
        observations=None,
    )  # fmt: skip

    assert matrix.definiteness().kind is DefinitenessKind.INDEFINITE
    with pytest.raises(AnalyticsValidationError, match="indefinite"):
        matrix.require_positive_definite("A test")


def test_a_zero_diagonal_with_off_diagonal_mass_is_indefinite() -> None:
    matrix = CovarianceMatrix.from_rows(
        ("A", "B"), ((0.0, 0.01), (0.01, 0.0)), currency="USD", period="1M", source="t",
        observations=None,
    )  # fmt: skip

    assert matrix.definiteness().kind is DefinitenessKind.INDEFINITE


def test_a_near_singular_matrix_reports_a_tiny_pivot_ratio() -> None:
    epsilon = 1e-10
    matrix = CovarianceMatrix.from_rows(
        ("A", "B"),
        ((1.0, 1.0 - epsilon), (1.0 - epsilon, 1.0)),
        currency="USD",
        period="1M",
        source="t",
        observations=None,
    )
    evidence = matrix.definiteness()

    assert evidence.kind is DefinitenessKind.POSITIVE_DEFINITE
    assert evidence.pivot_ratio < 1e-9


# --------------------------------------------------------------------------- #
# Correlation: derived, with its basis
# --------------------------------------------------------------------------- #


def test_correlation_is_derived_and_carries_its_basis() -> None:
    rng = random.Random(11)
    returns = {name: [rng.gauss(0.0, 0.02) for _ in range(30)] for name in ("X", "Y")}
    matrix = CovarianceMatrix.sample(returns, currency="GBP", period="1W", source="draws")
    correlation = matrix.correlation()

    assert correlation.values[0][0] == 1.0
    assert correlation.correlation("X", "Y") == pytest.approx(
        pearson_correlation(returns["X"], returns["Y"]), abs=1e-15
    )
    assert (correlation.currency, correlation.period, correlation.observations) == ("GBP", "1W", 30)
    assert correlation.covariance_id == matrix.covariance_id
    assert correlation.as_mapping()["Y"]["X"] == correlation.values[1][0]


def test_a_zero_variance_asset_has_no_correlation() -> None:
    matrix = CovarianceMatrix.from_rows(
        ("A", "CASH"), ((0.04, 0.0), (0.0, 0.0)), currency="USD", period="1M", source="t",
        observations=None,
    )  # fmt: skip

    with pytest.raises(AnalyticsValidationError, match="constant return series"):
        matrix.correlation()


def test_an_unknown_asset_has_no_correlation_entry() -> None:
    with pytest.raises(AnalyticsValidationError, match="not in this correlation"):
        covariance().correlation().correlation("A", "Q")


# --------------------------------------------------------------------------- #
# The Euler decomposition
# --------------------------------------------------------------------------- #


def _volatility(weights: dict[str, float]) -> float:
    names = sorted(weights)
    matrix = covariance()
    return math.sqrt(
        sum(weights[a] * matrix.covariance(a, b) * weights[b] for a in names for b in names)
    )


def test_contributions_sum_to_volatility_and_marginals_are_derivatives() -> None:
    weights = {"A": 0.5, "B": 0.3, "C": 0.2}
    risk = euler_decomposition(weights, covariance())

    assert risk.volatility == pytest.approx(_volatility(weights), rel=1e-15)
    assert math.fsum(risk.total.values()) == pytest.approx(risk.volatility, rel=1e-14)
    assert math.fsum(risk.relative.values()) == pytest.approx(1.0, rel=1e-14)
    assert abs(risk.reconciliation_residual) < 1e-15
    step = 1e-7
    for asset in weights:
        bumped = dict(weights)
        bumped[asset] += step
        lowered = dict(weights)
        lowered[asset] -= step
        derivative = (_volatility(bumped) - _volatility(lowered)) / (2 * step)
        assert risk.marginal[asset] == pytest.approx(derivative, rel=1e-6)
        assert risk.total[asset] == pytest.approx(weights[asset] * risk.marginal[asset], rel=1e-15)


def test_a_hedge_contributes_negative_risk_and_keeps_it() -> None:
    returns = [0.01, -0.02, 0.03, -0.01, 0.02]
    matrix = CovarianceMatrix.sample(
        {
            "LONG": returns,
            "HEDGE": [-0.9 * value + 0.001 * index for index, value in enumerate(returns)],
        },
        currency="USD",
        period="1D",
        source="t",
    )
    risk = euler_decomposition({"LONG": 1.0, "HEDGE": 0.5}, matrix)

    assert risk.total["HEDGE"] < 0.0
    assert math.fsum(risk.total.values()) == pytest.approx(risk.volatility, rel=1e-12)


def test_a_larger_universe_is_fine_and_its_unweighted_part_is_not_read() -> None:
    full = euler_decomposition({"A": 0.6, "C": 0.4}, covariance())
    restricted = euler_decomposition({"A": 0.6, "C": 0.4}, covariance().restricted(("A", "C")))

    assert full.volatility == restricted.volatility
    assert dict(full.total) == dict(restricted.total)


def test_zero_volatility_and_empty_weights_are_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="no risk to decompose"):
        euler_decomposition({"A": 0.0, "B": 0.0}, covariance())
    with pytest.raises(AnalyticsValidationError, match="no weights"):
        euler_decomposition({}, covariance())
    with pytest.raises(AnalyticsValidationError, match="not covered"):
        euler_decomposition({"Q": 1.0}, covariance())
    with pytest.raises(AnalyticsValidationError, match="finite"):
        euler_decomposition({"A": float("nan")}, covariance())


# --------------------------------------------------------------------------- #
# Factor loadings
# --------------------------------------------------------------------------- #


def loadings() -> FactorLoadings:
    return FactorLoadings.of(
        {
            "A": {"mkt": 0.9, "size": -0.2},
            "B": {"mkt": 1.2, "size": 0.4},
            "C": {"mkt": 1.0, "size": 0.0},
        },
        source="unit model v1",
        lineage={"mkt": "beta 60d", "size": "log cap rank"},
        as_of=100.0,
    )


def test_loadings_are_read_by_asset_and_factor() -> None:
    model = loadings()

    assert model.factors == ("mkt", "size")
    assert model.assets == ("A", "B", "C")
    assert model.loading("B", "size") == 0.4
    assert model.row("A") == (("mkt", 0.9), ("size", -0.2))
    assert model.lineage["mkt"] == "beta 60d"


def test_a_hole_in_the_loadings_is_refused_rather_than_zeroed() -> None:
    with pytest.raises(AnalyticsValidationError, match="An absent loading is not zero"):
        FactorLoadings.of(
            {"A": {"mkt": 0.9, "size": 0.1}, "B": {"mkt": 1.1}},
            source="m",
            lineage={"mkt": "x", "size": "y"},
            as_of=None,
        )


def test_every_factor_names_its_lineage_and_nothing_else() -> None:
    with pytest.raises(AnalyticsValidationError, match="lineage"):
        FactorLoadings.of({"A": {"mkt": 1.0}}, source="m", lineage={}, as_of=None)
    with pytest.raises(AnalyticsValidationError, match="lineage"):
        FactorLoadings.of(
            {"A": {"mkt": 1.0}}, source="m", lineage={"mkt": "x", "extra": "y"}, as_of=None
        )


def test_loadings_refuse_unknown_factors_and_uncovered_assets() -> None:
    model = loadings()
    with pytest.raises(AnalyticsValidationError, match="not in loadings"):
        model.require_factor("value")
    with pytest.raises(AnalyticsValidationError, match="not an asset with a zero loading"):
        model.require_covers(("A", "Q"), "A test")


def test_the_loadings_identity_follows_values_lineage_and_instant() -> None:
    base = loadings().loadings_id
    moved = FactorLoadings.of(
        {
            "A": {"mkt": 0.9, "size": -0.2},
            "B": {"mkt": 1.2, "size": 0.4},
            "C": {"mkt": 1.0, "size": 0.0},
        },
        source="unit model v1",
        lineage={"mkt": "beta 60d", "size": "log cap rank"},
        as_of=101.0,
    )

    assert loadings().loadings_id == base
    assert moved.loadings_id != base


def test_portfolio_factor_exposure_is_the_loading_weighted_sum() -> None:
    exposures = portfolio_factor_exposures({"A": 0.5, "B": 0.3, "C": 0.2}, loadings())

    assert exposures["mkt"] == pytest.approx(0.5 * 0.9 + 0.3 * 1.2 + 0.2 * 1.0, rel=1e-15)
    assert exposures["size"] == pytest.approx(0.5 * -0.2 + 0.3 * 0.4, rel=1e-15)
    with pytest.raises(AnalyticsValidationError, match="A factor exposure"):
        portfolio_factor_exposures({"Q": 1.0}, loadings())


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def test_a_classification_answers_buckets_and_members() -> None:
    sectors = Classification("sector", {"B": "Tech", "A": "Tech", "C": "Energy"}, "registry", None)

    assert list(sectors.labels) == ["A", "B", "C"]
    assert sectors.buckets == ("Energy", "Tech")
    assert sectors.members("Tech") == ("A", "B")
    assert sectors.members("Utilities") == ()
    assert sectors.label("C") == "Energy"


def test_an_unclassified_asset_is_named_never_bucketed() -> None:
    sectors = Classification("sector", {"A": "Tech"}, "registry", None)
    with pytest.raises(AnalyticsValidationError, match=r"needs a sector for \['B', 'C'\]"):
        sectors.require_covers(("A", "B", "C"), "A test")


@pytest.mark.parametrize("dimension", ["Sector", "1sector", "sec tor", ""])
def test_a_dimension_is_a_lowercase_identifier(dimension: str) -> None:
    with pytest.raises(AnalyticsValidationError):
        Classification(dimension, {"A": "x"}, "s", None)


def test_labels_and_source_are_real_text() -> None:
    with pytest.raises(AnalyticsValidationError):
        Classification("sector", {"A": " padded"}, "s", None)
    with pytest.raises(AnalyticsValidationError):
        Classification("sector", {"A": "x"}, "", None)
    with pytest.raises(AnalyticsValidationError, match="classifies nothing"):
        Classification("sector", {}, "s", None)
    with pytest.raises(AnalyticsValidationError, match="finite"):
        Classification("sector", {"A": "x"}, "s", float("nan"))


def test_the_classification_identity_follows_source_and_labels() -> None:
    first = Classification("country", {"A": "US"}, "vendor x", None)

    assert (
        first.classification_id
        == Classification("country", {"A": "US"}, "vendor x", None).classification_id
    )
    assert (
        first.classification_id
        != Classification("country", {"A": "DE"}, "vendor x", None).classification_id
    )
    assert (
        first.classification_id
        != Classification("country", {"A": "US"}, "vendor y", None).classification_id
    )


# --------------------------------------------------------------------------- #
# Concentration
# --------------------------------------------------------------------------- #


def test_the_herfindahl_index_of_equal_shares_is_one_over_n() -> None:
    assert herfindahl_index([0.25] * 4) == 0.25
    assert herfindahl_index([1.0]) == 1.0
    assert herfindahl_index([0.5, 0.3, 0.2]) == pytest.approx(0.38, rel=1e-15)
