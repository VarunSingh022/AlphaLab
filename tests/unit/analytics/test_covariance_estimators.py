"""The v3.11 covariance estimators, against references that share no code with them.

Ledoit-Wolf shrinkage is recomputed from the paper's definitions in exact
rational arithmetic -- including the numerator as the literal sum over
observations of ``||x_t x_t' - S||^2``, which the implementation evaluates by a
different, algebraically equal formula. The EWMA covariance is recomputed as
the weighted sum it is defined as, and the factor model as the matrix product
``B F B' + D`` in fractions.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from fractions import Fraction

import pytest

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import CovarianceMatrix, DefinitenessKind, FactorLoadings

RETURNS: dict[str, list[float]] = {
    "A": [0.01, -0.02, 0.015, 0.003, -0.007, 0.012],
    "B": [0.004, -0.011, 0.02, -0.001, 0.0, 0.009],
    "C": [-0.013, 0.006, 0.001, 0.017, -0.004, -0.002],
}


def _ledoit_wolf_exactly(returns: Mapping[str, Sequence[float]]) -> list[list[Fraction]]:
    zero = Fraction(0)
    names = sorted(returns)
    p = len(names)
    n = len(returns[names[0]])
    data = [[Fraction(value) for value in returns[name]] for name in names]
    means = [sum(row, zero) / n for row in data]
    x = [[data[i][t] - means[i] for i in range(p)] for t in range(n)]
    s = [[sum((x[t][i] * x[t][j] for t in range(n)), zero) / n for j in range(p)] for i in range(p)]
    m = sum((s[i][i] for i in range(p)), zero) / p
    d2 = sum(((s[i][j] - (m if i == j else zero)) ** 2 for i in range(p) for j in range(p)), zero)
    b2_bar = (
        sum(
            (
                sum(((x[t][i] * x[t][j] - s[i][j]) ** 2 for i in range(p) for j in range(p)), zero)
                for t in range(n)
            ),
            zero,
        )
        / n**2
    )
    delta = min(b2_bar, d2) / d2
    return [
        [(1 - delta) * s[i][j] + (delta * m if i == j else zero) for j in range(p)]
        for i in range(p)
    ]


def _lw(returns: Mapping[str, Sequence[float]]) -> CovarianceMatrix:
    return CovarianceMatrix.ledoit_wolf(returns, currency="USD", period="1D", source="unit")


def test_ledoit_wolf_is_the_papers_estimator_to_the_last_bits() -> None:
    estimate = _lw(RETURNS)
    reference = _ledoit_wolf_exactly(RETURNS)

    for i, row in enumerate(estimate.values):
        for j, value in enumerate(row):
            assert value == pytest.approx(float(reference[i][j]), rel=1e-13, abs=1e-18)


@pytest.mark.parametrize("seed", range(12))
def test_ledoit_wolf_matches_the_exact_reference_on_random_panels(seed: int) -> None:
    rng = random.Random(seed)
    assets = rng.randint(2, 6)
    length = rng.randint(3, 9)
    returns = {
        f"X{index}": [rng.gauss(0.0, 0.01 * (index + 1)) for _ in range(length)]
        for index in range(assets)
    }
    estimate = _lw(returns)
    reference = _ledoit_wolf_exactly(returns)

    for i, row in enumerate(estimate.values):
        for j, value in enumerate(row):
            assert value == pytest.approx(float(reference[i][j]), rel=1e-11, abs=1e-17)


def test_ledoit_wolf_is_positive_definite_with_fewer_observations_than_assets() -> None:
    rng = random.Random(3)
    returns = {f"X{index}": [rng.gauss(0.0, 0.02) for _ in range(4)] for index in range(8)}
    sample = CovarianceMatrix.sample(returns, currency="USD", period="1D", source="unit")
    shrunk = _lw(returns)

    assert sample.definiteness().kind is DefinitenessKind.SINGULAR
    assert shrunk.definiteness().kind is DefinitenessKind.POSITIVE_DEFINITE


def test_ledoit_wolf_records_its_parent_its_intensity_and_the_divisor() -> None:
    sample = CovarianceMatrix.sample(RETURNS, currency="USD", period="1D", source="unit")
    shrunk = _lw(RETURNS)

    assert shrunk.parent_id == sample.covariance_id
    assert shrunk.observations == 6
    assert shrunk.derivation is not None
    assert shrunk.derivation.startswith("ledoit-wolf 2004: (1 - ")
    assert "S = (5 / 6) * parent, intensity estimated" in shrunk.derivation
    intensity = float(shrunk.derivation.split("(1 - ")[1].split(")")[0])
    assert 0.0 <= intensity <= 1.0
    assert shrunk.covariance_id != sample.covariance_id


def test_a_sample_already_on_the_target_is_not_shrunk() -> None:
    # Two uncorrelated series of equal variance: S is exactly m I, so the
    # distance to the target is zero and the intensity is zero, not 0 / 0.
    returns = {"A": [1.0, -1.0, 1.0, -1.0], "B": [1.0, 1.0, -1.0, -1.0]}
    shrunk = _lw(returns)

    assert shrunk.values == ((1.0, 0.0), (0.0, 1.0))
    assert shrunk.derivation is not None
    assert shrunk.derivation.startswith("ledoit-wolf 2004: (1 - 0.0)")


def test_ledoit_wolf_refuses_what_a_sample_covariance_refuses() -> None:
    with pytest.raises(AnalyticsValidationError, match="differing lengths"):
        _lw({"A": [0.1, 0.2], "B": [0.1]})
    with pytest.raises(AnalyticsValidationError, match="at least 2"):
        _lw({"A": [0.1]})
    with pytest.raises(AnalyticsValidationError):
        _lw({"A": [0.1, float("nan")]})


# --------------------------------------------------------------------------- #
# EWMA
# --------------------------------------------------------------------------- #


def _ewma(returns: Mapping[str, Sequence[float]], decay: float) -> CovarianceMatrix:
    return CovarianceMatrix.ewma(returns, decay=decay, currency="USD", period="1D", source="unit")


def test_ewma_is_the_normalized_exponentially_weighted_sum() -> None:
    decay = 0.94
    estimate = _ewma(RETURNS, decay)
    names = sorted(RETURNS)
    length = len(RETURNS["A"])
    raw = [Fraction(decay) ** (length - 1 - t) for t in range(length)]
    weights = [value / sum(raw) for value in raw]

    for i, a in enumerate(names):
        for j, b in enumerate(names):
            expected = sum(
                w * Fraction(x) * Fraction(y)
                for w, x, y in zip(weights, RETURNS[a], RETURNS[b], strict=True)
            )
            assert estimate.values[i][j] == pytest.approx(float(expected), rel=1e-13)
    assert estimate.source == "unit; EWMA covariance, decay 0.94, zero mean (RiskMetrics 1996)"
    assert estimate.observations == length
    assert estimate.parent_id is None


def test_ewma_weighs_the_newest_observation_most() -> None:
    quiet = [0.0] * 20
    early = {"A": [0.05, *quiet[1:]]}
    late = {"A": [*quiet[1:], 0.05]}

    # The same shock, newest rather than oldest of 20, weighs decay ** -19 times more.
    ratio = _ewma(late, 0.9).values[0][0] / _ewma(early, 0.9).values[0][0]
    assert ratio == pytest.approx(0.9**-19, rel=1e-12)


@pytest.mark.parametrize("decay", [0.0, 1.0, -0.5, 1.5, float("nan"), float("inf")])
def test_ewma_decay_must_lie_strictly_inside_the_unit_interval(decay: float) -> None:
    with pytest.raises(AnalyticsValidationError):
        _ewma(RETURNS, decay)


# --------------------------------------------------------------------------- #
# Factor model
# --------------------------------------------------------------------------- #

LOADINGS = FactorLoadings.of(
    {
        "A": {"market": 1.1, "value": 0.3},
        "B": {"market": 0.9, "value": -0.4},
        "C": {"market": 1.0, "value": 0.0},
    },
    source="unit loadings",
    lineage={"market": "beta v1", "value": "book-to-price v1"},
    as_of=None,
)
FACTORS = CovarianceMatrix.from_rows(
    ("market", "value"),
    ((0.0004, 0.00005), (0.00005, 0.0001)),
    currency="USD",
    period="1D",
    source="unit factor covariance",
    observations=250,
)
SPECIFIC = {"A": 0.0002, "B": 0.0003, "C": 0.00015}


def test_the_factor_model_is_b_f_b_transpose_plus_d() -> None:
    implied = CovarianceMatrix.factor_model(LOADINGS, FACTORS, SPECIFIC)
    b = [[Fraction(value) for value in row] for row in LOADINGS.values]
    f = [[Fraction(value) for value in row] for row in FACTORS.values]

    for i, a in enumerate(LOADINGS.assets):
        for j, _ in enumerate(LOADINGS.assets):
            expected = sum(b[i][g] * f[g][h] * b[j][h] for g in range(2) for h in range(2))
            if i == j:
                expected += Fraction(SPECIFIC[a])
            assert implied.values[i][j] == pytest.approx(float(expected), rel=1e-14)
    assert (implied.currency, implied.period) == ("USD", "1D")
    assert implied.parent_id == FACTORS.covariance_id
    assert implied.observations is None
    assert implied.derivation is not None
    assert LOADINGS.loadings_id in implied.derivation
    assert implied.source == "factor model over unit loadings"


def test_positive_specific_variances_make_it_positive_definite_whatever_f_is() -> None:
    singular = CovarianceMatrix.from_rows(
        ("market", "value"),
        ((0.0004, 0.0002), (0.0002, 0.0001)),
        currency="USD",
        period="1D",
        source="rank one",
        observations=None,
    )

    assert singular.definiteness().kind is DefinitenessKind.SINGULAR
    implied = CovarianceMatrix.factor_model(LOADINGS, singular, SPECIFIC)
    assert implied.definiteness().kind is DefinitenessKind.POSITIVE_DEFINITE


def test_the_specific_variances_are_part_of_the_identity() -> None:
    one = CovarianceMatrix.factor_model(LOADINGS, FACTORS, SPECIFIC)
    other = CovarianceMatrix.factor_model(LOADINGS, FACTORS, {**SPECIFIC, "C": 0.00016})

    assert one.covariance_id != other.covariance_id
    assert one.derivation != other.derivation


def test_the_factor_model_refuses_mismatched_or_missing_inputs() -> None:
    wrong = CovarianceMatrix.from_rows(
        ("market", "size"),
        ((0.0004, 0.0), (0.0, 0.0001)),
        currency="USD",
        period="1D",
        source="unit",
        observations=None,
    )
    with pytest.raises(AnalyticsValidationError, match="same"):
        CovarianceMatrix.factor_model(LOADINGS, wrong, SPECIFIC)
    with pytest.raises(AnalyticsValidationError, match="missing \\['C'\\]"):
        CovarianceMatrix.factor_model(LOADINGS, FACTORS, {"A": 0.1, "B": 0.1})
    with pytest.raises(AnalyticsValidationError, match="outside \\['D'\\]"):
        CovarianceMatrix.factor_model(LOADINGS, FACTORS, {**SPECIFIC, "D": 0.1})
    with pytest.raises(AnalyticsValidationError, match="not negative"):
        CovarianceMatrix.factor_model(LOADINGS, FACTORS, {**SPECIFIC, "B": -0.1})
    with pytest.raises(AnalyticsValidationError):
        CovarianceMatrix.factor_model(LOADINGS, FACTORS, {**SPECIFIC, "B": float("inf")})


# --------------------------------------------------------------------------- #
# The factor structure kept beside the matrix (v3.12, ledger PRF-005)
# --------------------------------------------------------------------------- #


def _natural_pivots(rows: Sequence[Sequence[float]]) -> list[float]:
    """Unpivoted Cholesky pivots, in exact rational arithmetic."""

    size = len(rows)
    work = [[Fraction(value) for value in row] for row in rows]
    pivots: list[float] = []
    for column in range(size):
        pivot = work[column][column]
        pivots.append(float(pivot))
        for row in range(column + 1, size):
            factor = work[row][column] / pivot
            for other in range(column + 1, size):
                work[row][other] -= factor * work[column][other]
    return pivots


def test_the_factor_model_keeps_its_structure_without_changing_its_identity() -> None:
    implied = CovarianceMatrix.factor_model(LOADINGS, FACTORS, SPECIFIC)
    plain = CovarianceMatrix(
        implied.assets,
        implied.values,
        implied.currency,
        implied.period,
        implied.source,
        implied.observations,
        parent_id=implied.parent_id,
        derivation=implied.derivation,
    )

    assert implied.factors is not None
    assert implied.factors.loadings is LOADINGS
    assert implied.factors.specific == tuple(SPECIFIC[a] for a in LOADINGS.assets)
    assert implied.factors.derivation == implied.derivation
    assert implied.factors.rows() == implied.values
    # A solving aid, not part of the claim: the same identity, and equal.
    assert plain.factors is None
    assert plain.covariance_id == implied.covariance_id
    assert plain == implied


def test_a_structure_passed_in_is_checked_against_every_value() -> None:
    implied = CovarianceMatrix.factor_model(LOADINGS, FACTORS, SPECIFIC)
    structure = implied.factors
    assert structure is not None
    fields = (implied.assets, implied.values, "USD", "1D", implied.source, None)

    accepted = CovarianceMatrix(
        *fields, parent_id=implied.parent_id, derivation=implied.derivation, factors=structure
    )
    assert accepted.factors is structure
    with pytest.raises(AnalyticsValidationError, match="parent"):
        CovarianceMatrix(*fields, parent_id="x" * 64, derivation="d", factors=structure)
    tampered = tuple(
        tuple(value * (1.0 + 1e-12) if i == j == 0 else value for j, value in enumerate(row))
        for i, row in enumerate(implied.values)
    )
    with pytest.raises(AnalyticsValidationError, match="does not imply these values"):
        CovarianceMatrix(
            implied.assets,
            tampered,
            "USD",
            "1D",
            implied.source,
            None,
            parent_id=implied.parent_id,
            derivation=implied.derivation,
            factors=structure,
        )
    with pytest.raises(AnalyticsValidationError, match="currency and period"):
        CovarianceMatrix(
            implied.assets,
            implied.values,
            "EUR",
            "1D",
            implied.source,
            None,
            parent_id=implied.parent_id,
            derivation=implied.derivation,
            factors=structure,
        )


def test_a_copy_keeps_the_structure_and_a_changed_copy_cannot() -> None:
    from dataclasses import replace

    implied = CovarianceMatrix.factor_model(LOADINGS, FACTORS, SPECIFIC)

    assert replace(implied).factors is implied.factors
    with pytest.raises(AnalyticsValidationError, match="does not imply"):
        replace(implied, values=tuple(tuple(2.0 * v for v in row) for row in implied.values))


def test_the_structured_pivots_are_the_natural_cholesky_pivots() -> None:
    rng = random.Random(3)
    assets = [f"A{index:02d}" for index in range(25)]
    loadings = FactorLoadings.of(
        {asset: {"f": rng.gauss(0, 1), "g": rng.gauss(0, 1)} for asset in assets},
        source="test",
        lineage={"f": "x", "g": "y"},
        as_of=None,
    )
    factor = CovarianceMatrix.from_rows(
        ("f", "g"),
        ((0.04, 0.01), (0.01, 0.03)),
        currency="USD",
        period="1D",
        source="test",
        observations=None,
    )
    implied = CovarianceMatrix.factor_model(
        loadings, factor, {asset: 0.01 + 0.01 * rng.random() for asset in assets}
    )
    assert implied.factors is not None

    pivots = implied.factors.pivots()
    assert pivots is not None
    exact = _natural_pivots(implied.values)
    assert max(abs(p - e) / e for p, e in zip(pivots, exact, strict=True)) < 1e-12
    evidence = implied.factors.definiteness()
    dense = implied.definiteness()
    assert evidence is not None
    assert evidence.kind is DefinitenessKind.POSITIVE_DEFINITE
    # The largest variance is the first pivot diagonal pivoting takes.
    assert evidence.largest_pivot == dense.largest_pivot
    assert evidence.pivot_floor == dense.pivot_floor
    assert evidence.smallest_pivot == min(pivots)


def test_a_structure_that_cannot_establish_definiteness_says_none() -> None:
    from alphalab.analytics.risk_model import factor_cholesky_pivots

    assert factor_cholesky_pivots(((1.0,), (0.5,)), ((0.04,),), (0.01, 0.0)) is None
    assert factor_cholesky_pivots(((1.0, 0.0),), ((0.04, 0.05), (0.05, 0.04)), (0.01,)) is None
    # Rank-deficient but semidefinite: fine.
    singular = factor_cholesky_pivots(((1.0, 1.0),), ((0.04, 0.04), (0.04, 0.04)), (0.01,))
    assert singular == pytest.approx((0.01 + 0.16,))
    zero = CovarianceMatrix.factor_model(LOADINGS, FACTORS, {**SPECIFIC, "B": 0.0})
    assert zero.factors is not None
    assert zero.factors.definiteness() is None


def test_the_identity_is_derived_once_and_is_the_same_derivation() -> None:
    implied = CovarianceMatrix.factor_model(LOADINGS, FACTORS, SPECIFIC)

    first = implied.covariance_id
    assert implied.covariance_id is first
    assert implied._derive_identity() == first
