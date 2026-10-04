"""A box uncertainty set on a portfolio that may short (v3.13).

Until v3.13 a box set was refused unless every weight was bounded below by
zero: for a long-only book the worst case is the shifted returns, an identity,
and for a book that may short it is an ``L1`` penalty on the weights, which was
not offered (ADR-0043's known limitations). It is now solved as linear costs
are, with the held weights at zero.

Every answer here is checked against references that share no code with the
solver: the optimum against the best exact orthant optimum in rational
arithmetic (:func:`tests.unit.portfolio_optimizer.qp_reference.exact_optimum`),
and the worst case the result reports against the minimum of ``mu' w`` over
every vertex of the box -- where a linear function on a box attains its
minimum.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Mapping, Sequence
from fractions import Fraction

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.portfolio_optimizer.construction import (
    BoxUncertainty,
    ConstraintSet,
    ConstructionProblem,
    ConstructionResult,
    ConstructionStatus,
    ExpectedReturns,
    ExposureRange,
    MeanVariance,
    RobustMeanVariance,
    SolverSettings,
    WeightBounds,
    construct,
)
from tests.unit.portfolio_optimizer.qp_reference import Row, exact_optimum

ASSETS = ("A", "B", "C")
ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
COVARIANCE = CovarianceMatrix.from_rows(
    ASSETS, ROWS, currency="USD", period="1M", source="unit", observations=None
)
BUDGET = ExposureRange.exactly(1.0)


def _returns(values: Sequence[float]) -> ExpectedReturns:
    return ExpectedReturns(dict(zip(ASSETS, values, strict=True)), "USD", "1M", "unit forecast")


def _bounds(lower: float, upper: float) -> WeightBounds:
    return WeightBounds.uniform(lower, upper)


def _box(widths: Sequence[float]) -> BoxUncertainty:
    return BoxUncertainty(dict(zip(ASSETS, widths, strict=True)))


def _solve(objective: object, constraints: ConstraintSet) -> ConstructionResult:
    return construct(
        ConstructionProblem(COVARIANCE, objective, constraints, SETTINGS)  # type: ignore[arg-type]
    )


def _weights(result: ConstructionResult) -> list[float]:
    assert result.weights is not None, result.diagnostics.detail
    return [result.weights[asset] for asset in ASSETS]


def _brute_force(
    aversion: float, mu: Sequence[float], widths: Sequence[float], lower: float, upper: float
) -> list[float]:
    """The robust optimum: the best exact orthant optimum by the true objective.

    On each orthant ``|w_i| = s_i w_i``, so the robust objective is a quadratic
    program; the best of them by ``(lambda / 2) w' C w - mu' w + delta' |w|``,
    evaluated in rational arithmetic, is the optimum.
    """

    size = len(mu)
    hessian = [[aversion * value for value in row] for row in ROWS]
    constraints: list[Row] = [
        ([1.0] * size, 1.0, True),
        *(([1.0 if i == j else 0.0 for j in range(size)], lower, False) for i in range(size)),
        *(([-1.0 if i == j else 0.0 for j in range(size)], -upper, False) for i in range(size)),
    ]
    best: tuple[Fraction, list[float]] | None = None
    for signs in itertools.product((1.0, -1.0), repeat=size):
        sides: list[Row] = [
            ([signs[i] if j == i else 0.0 for j in range(size)], 0.0, False) for i in range(size)
        ]
        linear = [-mu[i] + widths[i] * signs[i] for i in range(size)]
        x = exact_optimum(hessian, linear, [*constraints, *sides])
        if x is None:
            continue
        exact = [Fraction(value) for value in x]
        value: Fraction = Fraction(0) + (
            sum(
                Fraction(1, 2) * exact[i] * Fraction(hessian[i][j]) * exact[j]
                for i in range(size)
                for j in range(size)
            )
            - sum(Fraction(mu[i]) * exact[i] for i in range(size))
            + sum(Fraction(widths[i]) * abs(exact[i]) for i in range(size))
        )
        if best is None or value < best[0]:
            best = (value, x)
    assert best is not None
    return best[1]


def _worst_over_vertices(
    mu: Sequence[float], widths: Sequence[float], weights: Sequence[float]
) -> float:
    """``min over mu in the box of mu' w``, by enumerating the box's vertices."""

    return min(
        math.fsum((mu[i] + corner[i] * widths[i]) * weights[i] for i in range(len(weights)))
        for corner in itertools.product((-1.0, 1.0), repeat=len(weights))
    )


@pytest.mark.parametrize("seed", range(30))
def test_the_robust_optimum_with_shorts_is_the_best_of_every_orthant(seed: int) -> None:
    rng = random.Random(seed)
    mu = [rng.uniform(-0.01, 0.03) for _ in ASSETS]
    widths = [rng.choice((0.0, rng.uniform(0.0, 0.02))) for _ in ASSETS]
    aversion = rng.choice((1.0, 3.0, 10.0))
    lower, upper = -1.0, 2.0
    result = _solve(
        RobustMeanVariance(_returns(mu), aversion, _box(widths)),
        ConstraintSet(BUDGET, _bounds(lower, upper)),
    )

    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    expected = _brute_force(aversion, mu, widths, lower, upper)
    assert _weights(result) == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize("seed", range(10))
def test_the_reported_worst_case_is_the_minimum_over_the_box(seed: int) -> None:
    rng = random.Random(100 + seed)
    mu = [rng.uniform(-0.01, 0.03) for _ in ASSETS]
    widths = [rng.uniform(0.0, 0.01) for _ in ASSETS]
    result = _solve(
        RobustMeanVariance(_returns(mu), 3.0, _box(widths)),
        ConstraintSet(BUDGET, _bounds(-1.0, 2.0)),
    )

    weights = _weights(result)
    assert result.diagnostics.worst_case_return == pytest.approx(
        _worst_over_vertices(mu, widths, weights), abs=1e-15
    )


def test_an_asset_too_uncertain_to_hold_either_way_is_held_at_exactly_zero() -> None:
    """``C`` is the asset the nominal problem shorts; a wide box removes it."""

    mu = [0.02, 0.025, -0.08]
    nominal = _solve(MeanVariance(_returns(mu), 3.0), ConstraintSet(BUDGET, _bounds(-1.0, 2.0)))
    assert _weights(nominal)[2] < 0.0

    result = _solve(
        RobustMeanVariance(_returns(mu), 3.0, BoxUncertainty({"A": 0.0, "B": 0.0, "C": 0.05})),
        ConstraintSet(BUDGET, _bounds(-1.0, 2.0)),
    )

    assert _weights(result)[2] == 0.0
    assert any("no position in C" in label for label in result.diagnostics.binding), (
        result.diagnostics.binding
    )
    assert "Box uncertainty (an L1 penalty on the weights)" in result.diagnostics.detail


def test_a_box_of_zero_width_is_the_nominal_problem_with_shorts() -> None:
    mu = [0.01, 0.02, 0.015]
    nominal = _solve(MeanVariance(_returns(mu), 3.0), ConstraintSet(BUDGET, _bounds(-1.0, 2.0)))
    robust = _solve(
        RobustMeanVariance(_returns(mu), 3.0, BoxUncertainty(dict.fromkeys(ASSETS, 0.0))),
        ConstraintSet(BUDGET, _bounds(-1.0, 2.0)),
    )

    assert _weights(robust) == pytest.approx(_weights(nominal), abs=1e-12)


def test_a_long_only_book_keeps_the_shifted_returns_identity() -> None:
    """The long-only path is unchanged: robust is mean-variance on ``mu - delta``."""

    mu = [0.01, 0.02, 0.015]
    widths = {"A": 0.004, "B": 0.012, "C": 0.001}
    shifted = [value - widths[asset] for value, asset in zip(mu, ASSETS, strict=True)]
    robust = _solve(
        RobustMeanVariance(_returns(mu), 3.0, BoxUncertainty(widths)),
        ConstraintSet(BUDGET, _bounds(0.0, 1.0)),
    )
    reference = _solve(
        MeanVariance(_returns(shifted), 3.0), ConstraintSet(BUDGET, _bounds(0.0, 1.0))
    )

    assert _weights(robust) == _weights(reference)


def test_a_volatility_cap_holds_with_shorts() -> None:
    cap = 0.2
    mu: Mapping[str, float] = {"A": 0.01, "B": 0.03, "C": 0.02}
    result = _solve(
        RobustMeanVariance(
            ExpectedReturns(mu, "USD", "1M", "unit forecast"),
            0.5,
            BoxUncertainty({"A": 0.002, "B": 0.004, "C": 0.003}),
        ),
        ConstraintSet(BUDGET, _bounds(-1.0, 2.0), max_volatility=cap),
    )

    weights = _weights(result)
    variance = math.fsum(weights[i] * ROWS[i][j] * weights[j] for i in range(3) for j in range(3))
    assert math.sqrt(variance) <= cap * (1 + 1e-9)
