"""The VECM of a stated cointegrating rank, by Johansen's two steps (v4.3, ADR-0074, WS-07).

* **The relation is recovered, and it corrects.** A pair built cointegrated
  gives back its hedge ratio and its constant, normalized on the first series.
  The series that deviates corrects toward the relation (its ``alpha`` is
  significantly negative), and the walk it is tied to does not.
* **The second step is least squares, exactly.** Given the relation, each
  equation's coefficients are the exact rational least-squares solution on the
  same design.
* **What a VECM cannot be is refused**: rank 0 is a VAR in differences, and
  full rank a stationary VAR.
"""

from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from alphalab.econometrics.cointegration import _inverse, fit_vecm
from alphalab.econometrics.critical_values import JohansenCase
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import system_identity
from alphalab.econometrics.unit_root import FixedLags

TOLERANCE = 1e-15
ITERATIONS = 60
CONDITION = 1e10


def _pair(seed: int, count: int) -> list[list[float]]:
    """``y = 2 + 1.5 x + u``: ``x`` a walk, ``u`` an AR(1) with coefficient 0.4."""

    draws = random.Random(seed)
    x, u = [0.0], [0.0]
    for _ in range(count - 1):
        x.append(x[-1] + draws.gauss(0.0, 1.0))
        u.append(0.4 * u[-1] + draws.gauss(0.0, 1.0))
    return [[2.0 + 1.5 * a + b for a, b in zip(x, u, strict=True)], x]


def _fit(series: list[list[float]], **changes: object) -> object:
    arguments: dict[str, object] = {
        "rank": 1,
        "case": JohansenCase.RESTRICTED_CONSTANT,
        "lags": FixedLags(1),
        "tolerance": TOLERANCE,
        "max_iterations": ITERATIONS,
        "maximum_condition": CONDITION,
    }
    arguments.update(changes)
    return fit_vecm(series, **arguments)  # type: ignore[arg-type]


def test_a_pairs_relation_is_recovered_and_the_deviating_series_corrects() -> None:
    fit = fit_vecm(
        _pair(5, 800),
        rank=1,
        case=JohansenCase.RESTRICTED_CONSTANT,
        lags=FixedLags(1),
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        maximum_condition=CONDITION,
    )

    beta = fit.relations.parameters
    assert beta["beta_1_series_1"] == 1.0, "normalized on the first series, exactly"
    assert beta["beta_1_series_2"] == pytest.approx(-1.5, abs=0.03)
    assert beta["beta_1_constant"] == pytest.approx(-2.0, abs=0.3)

    y, x = fit.equations
    t_y = y.parameters["alpha_1"] / y.standard_errors["alpha_1"]
    t_x = x.parameters["alpha_1"] / x.standard_errors["alpha_1"]
    assert t_y < -5.0, "y deviates from the relation and corrects toward it"
    assert abs(t_x) < 3.0, "x is the walk y is tied to: it does not correct"
    assert {"gamma_1_series_1", "gamma_1_series_2"} <= set(y.parameters)
    assert "constant" not in y.parameters, "a restricted constant is in the relation alone"


def test_each_equation_is_the_exact_least_squares_solution_given_the_relation() -> None:
    series = _pair(9, 60)
    fit = _fit(series, case=JohansenCase.UNRESTRICTED_CONSTANT, lags=FixedLags(1))
    beta = fit.relations.parameters  # type: ignore[attr-defined]

    rows = range(2, 60)
    ect = [
        math.fsum(beta[f"beta_1_series_{k + 1}"] * series[k][t - 1] for k in range(2)) for t in rows
    ]
    design = [
        [
            Fraction(e),
            *(Fraction(series[k][t - 1] - series[k][t - 2]) for k in range(2)),
            Fraction(1),
        ]
        for e, t in zip(ect, rows, strict=True)
    ]
    for equation, target_series in zip(fit.equations, series, strict=True):  # type: ignore[attr-defined]
        target = [Fraction(target_series[t] - target_series[t - 1]) for t in rows]
        width = len(design[0])
        gram = [
            [sum((row[i] * row[j] for row in design), Fraction(0)) for j in range(width)]
            for i in range(width)
        ]
        rhs = [
            sum((row[i] * y for row, y in zip(design, target, strict=True)), Fraction(0))
            for i in range(width)
        ]
        augmented = [[*gram[i], rhs[i]] for i in range(width)]
        for c in range(width):
            pivot = next(r for r in range(c, width) if augmented[r][c] != 0)
            augmented[c], augmented[pivot] = augmented[pivot], augmented[c]
            for r in range(width):
                if r != c:
                    factor = augmented[r][c] / augmented[c][c]
                    augmented[r] = [
                        a - factor * b for a, b in zip(augmented[r], augmented[c], strict=True)
                    ]
        exact = [augmented[i][width] / augmented[i][i] for i in range(width)]
        names = ["alpha_1", "gamma_1_series_1", "gamma_1_series_2", "constant"]
        for name, value in zip(names, exact, strict=True):
            assert equation.parameters[name] == pytest.approx(float(value), rel=1e-10, abs=1e-14)


def test_the_covariance_is_the_residuals_cross_products_over_t() -> None:
    fit = _fit(_pair(3, 300))
    sigma = fit.covariance.parameters  # type: ignore[attr-defined]
    assert sigma["sigma_1_2"] == sigma["sigma_2_1"]
    assert sigma["sigma_1_1"] > 0.0 and sigma["sigma_2_2"] > 0.0
    assert sigma["sigma_1_2"] ** 2 < sigma["sigma_1_1"] * sigma["sigma_2_2"]


def test_the_fit_is_identified_by_its_system_and_every_choice() -> None:
    series = _pair(4, 120)
    fit = _fit(series)
    for estimate in (fit.relations, *fit.equations, fit.covariance):  # type: ignore[attr-defined]
        assert estimate.data_id == system_identity(series)
        assert estimate.settings["rank"] == 1
        assert estimate.settings["case"] == "restricted_constant"
        assert estimate.numerical_class.identity_bearing
    assert fit.relations.convergence is not None  # type: ignore[attr-defined]
    assert [e.settings["equation"] for e in fit.equations] == [1, 2]  # type: ignore[attr-defined]
    other = _fit(series, lags=FixedLags(2))
    assert other.relations.estimate_id != fit.relations.estimate_id  # type: ignore[attr-defined]


@pytest.mark.parametrize("rank", [0, 2, True, 1.0])
def test_a_rank_a_vecm_cannot_have_is_refused(rank: object) -> None:
    with pytest.raises(EconometricsInputError, match="rank is 1 to 1 for 2 series"):
        _fit(_pair(2, 100), rank=rank)


def test_a_block_the_first_series_cannot_normalize_is_refused() -> None:
    with pytest.raises(EconometricsInputError, match="do not identify the relations"):
        _inverse([[1.0, 2.0], [2.0, 4.0]], CONDITION)
    assert _inverse([[2.0, 0.0], [0.0, 4.0]], CONDITION) == [[0.5, 0.0], [0.0, 0.25]]
