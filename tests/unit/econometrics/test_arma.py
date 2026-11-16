"""ARMA models by conditional least squares and the exact Gaussian likelihood (v4.3, ADR-0074).

* **The region is the coordinates'**: Durbin and Levinson's recursion and its
  inverse are each other's, and refuse a polynomial outside the region.
* **The exact likelihood is the joint density**: for an ARMA(1, 1) and an
  MA(2), the filter's likelihood equals the Gaussian density of the
  observations under their closed-form autocovariances.
* **Least squares is the recursion**: the residuals are the recursion written
  out, and the conditional likelihood is their Gaussian one.
* **Known parameters are recovered** within three standard errors by both
  methods, with a mean and without.
* **An information criterion chooses the order**, every candidate scored on
  one sample, and the chosen order refitted on all the data it leaves.
* **Every choice is stated, and what cannot be fitted is refused.**
"""

from __future__ import annotations

import math
import random

import pytest

from alphalab.common.estimate import NumericalClass
from alphalab.common.linalg import cholesky
from alphalab.econometrics import Deterministic, LagCriterion
from alphalab.econometrics.arma import (
    MINIMUM_ARMA_OBSERVATIONS,
    ArmaMethod,
    ArmaStart,
    _conditional_log_likelihood,
    _exact,
    _from_partial,
    _residuals,
    _to_partial,
    fit_arma,
    select_arma,
)
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import series_identity

TOLERANCE = 1e-6
ITERATIONS = 200
STEP = 1e-6


def test_the_recursion_and_its_inverse_are_each_others_and_keep_to_the_region() -> None:
    partial = [0.5, -0.3, 0.2, 0.9]
    assert _to_partial(_from_partial(partial)) == pytest.approx(partial, abs=1e-15)
    assert _from_partial([0.5]) == [0.5]
    # AR(2) by hand: phi_1 = r_1 (1 - r_2), phi_2 = r_2.
    assert _from_partial([0.5, -0.3]) == pytest.approx([0.5 * 1.3, -0.3], abs=1e-16)
    assert _to_partial([1.0]) is None
    assert _to_partial([-0.5, 0.6]) is None  # phi_2 - phi_1 > 1
    assert _to_partial([0.5, 0.6]) is None  # phi_1 + phi_2 > 1
    assert _to_partial([]) == []


def _joint_log_density(values: list[float], autocovariance: list[float]) -> float:
    count = len(values)
    matrix = [[autocovariance[abs(i - k)] for k in range(count)] for i in range(count)]
    factor = cholesky(matrix)
    weighted = factor.solve(values)
    return -0.5 * (
        count * math.log(2.0 * math.pi)
        + math.fsum(math.log(pivot) for pivot in factor.pivots)
        + math.fsum(a * b for a, b in zip(values, weighted, strict=True))
    )


def _unconcentrated(values: list[float], ar: list[float], ma: list[float]) -> float:
    """The likelihood at ``sigma² = 1``, from the concentrated one's two parts."""

    count = len(values)
    concentrated, _, variance = _exact(values, 0.0, ar, ma)
    log_determinant = -2.0 * concentrated - count * (
        math.log(2.0 * math.pi) + 1.0 + math.log(variance)
    )
    return -0.5 * (count * math.log(2.0 * math.pi) + log_determinant + count * variance)


VALUES = [0.3, -0.5, 1.2, 0.8, -0.1, 0.4, -0.9, 0.2, 0.6, -0.3]


def test_the_exact_likelihood_of_an_arma_1_1_is_its_joint_density() -> None:
    phi, theta = 0.6, 0.3
    gamma = [
        (1 + 2 * phi * theta + theta**2) / (1 - phi**2),
        (1 + phi * theta) * (phi + theta) / (1 - phi**2),
    ]
    for _ in range(len(VALUES)):
        gamma.append(phi * gamma[-1])
    assert _unconcentrated(VALUES, [phi], [theta]) == pytest.approx(
        _joint_log_density(VALUES, gamma), rel=1e-13
    )


def test_the_exact_likelihood_of_an_ma_2_is_its_joint_density() -> None:
    theta1, theta2 = 0.4, -0.25
    gamma = [1 + theta1**2 + theta2**2, theta1 + theta1 * theta2, theta2] + [0.0] * len(VALUES)
    assert _unconcentrated(VALUES, [], [theta1, theta2]) == pytest.approx(
        _joint_log_density(VALUES, gamma), rel=1e-13
    )


def test_least_squares_residuals_are_the_recursion_and_the_likelihood_theirs() -> None:
    mean, phi, theta = 0.25, [0.5, -0.2], [0.3]
    errors = [0.0, 0.0]
    for t in range(2, len(VALUES)):
        centered = [v - mean for v in VALUES]
        errors.append(
            centered[t]
            - phi[0] * centered[t - 1]
            - phi[1] * centered[t - 2]
            - theta[0] * errors[-1]
        )
    got = _residuals(VALUES, mean, phi, theta, 2)
    assert got == pytest.approx(errors[2:], abs=1e-15)
    total = math.fsum(e * e for e in got)
    count = len(got)
    assert _conditional_log_likelihood(total, count) == pytest.approx(
        math.fsum(
            -0.5 * (math.log(2 * math.pi) + math.log(total / count) + e * e / (total / count))
            for e in got
        ),
        rel=1e-13,
    )


def _arma(
    seed: int, count: int, mean: float, ar: list[float], ma: list[float], burn: int = 300
) -> list[float]:
    draws = random.Random(seed)
    values, errors = [mean] * len(ar), [0.0] * len(ma)
    for _ in range(count + burn):
        error = draws.gauss(0.0, 1.0)
        value = (
            mean
            + sum(phi * (values[-1 - i] - mean) for i, phi in enumerate(ar))
            + error
            + sum(theta * errors[-1 - j] for j, theta in enumerate(ma))
        )
        values.append(value)
        errors.append(error)
    return values[-count:]


CASES = [
    ("arma(1,1) about a mean", (1, 1), Deterministic.CONSTANT, 0.5, [0.6], [0.3]),
    ("ar(2) about zero", (2, 0), Deterministic.NONE, 0.0, [0.5, -0.3], []),
    ("ma(1) about zero", (0, 1), Deterministic.NONE, 0.0, [], [-0.4]),
]


@pytest.mark.parametrize("method", list(ArmaMethod))
@pytest.mark.parametrize(("name", "order", "deterministic", "mean", "ar", "ma"), CASES)
def test_known_parameters_are_recovered_within_their_standard_errors(
    method: ArmaMethod,
    name: str,
    order: tuple[int, int],
    deterministic: Deterministic,
    mean: float,
    ar: list[float],
    ma: list[float],
) -> None:
    values = _arma(len(name), 1000, mean, ar, ma)
    fit = fit_arma(
        values,
        order=order,
        deterministic=deterministic,
        method=method,
        start=ArmaStart.ZEROS,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    truth = {
        **({"mean": mean} if deterministic is Deterministic.CONSTANT else {}),
        **{f"ar_{i + 1}": phi for i, phi in enumerate(ar)},
        **{f"ma_{j + 1}": theta for j, theta in enumerate(ma)},
        "variance": 1.0,
    }
    assert sorted(fit.estimate.parameters) == sorted(truth)
    for key, value in truth.items():
        estimate, error = fit.estimate.parameters[key], fit.estimate.standard_errors[key]
        assert error > 0.0
        assert abs(estimate - value) < 3.0 * error, (key, estimate, value, error)


def test_both_methods_agree_and_the_exact_likelihood_scores_every_value() -> None:
    values = _arma(7, 800, 0.0, [0.6], [0.3])
    fits = {
        method: fit_arma(
            values,
            order=(1, 1),
            deterministic=Deterministic.NONE,
            method=method,
            start=ArmaStart.ZEROS,
            tolerance=TOLERANCE,
            max_iterations=ITERATIONS,
            step=STEP,
        )
        for method in ArmaMethod
    }
    least, exact = fits[ArmaMethod.CONDITIONAL_LEAST_SQUARES], fits[ArmaMethod.EXACT_LIKELIHOOD]
    assert least.observations == 799 and len(least.residuals) == 799
    assert exact.observations == 800 and len(exact.residuals) == 800
    for key in ("ar_1", "ma_1"):
        assert least.estimate.parameters[key] == pytest.approx(
            exact.estimate.parameters[key], abs=0.02
        )
    assert least.aic == pytest.approx(-2 * least.log_likelihood + 2 * 3, rel=1e-15)
    assert exact.bic == pytest.approx(-2 * exact.log_likelihood + 3 * math.log(800), rel=1e-15)


def test_the_criterion_chooses_the_order_on_one_sample_and_refits_it() -> None:
    values = _arma(4, 1000, 0.5, [0.6], [0.3])
    selection = select_arma(
        values,
        maximum_order=(2, 2),
        criterion=LagCriterion.BIC,
        deterministic=Deterministic.CONSTANT,
        method=ArmaMethod.CONDITIONAL_LEAST_SQUARES,
        start=ArmaStart.ZEROS,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )

    assert [order for order, _ in selection.candidates] == [
        (p, q) for p in range(3) for q in range(3)
    ]
    assert selection.chosen.order == (1, 1)
    values_by_order = dict(selection.candidates)
    assert values_by_order[(1, 1)] == min(values_by_order.values())
    assert selection.chosen.observations == 999  # refitted on all the data p = 1 leaves
    assert dict(selection.chosen.estimate.settings)["conditioned"] == 1


def test_the_exact_criterion_scores_every_candidate_on_every_value() -> None:
    values = _arma(5, 300, 0.0, [0.7], [])
    selection = select_arma(
        values,
        maximum_order=(2, 1),
        criterion=LagCriterion.AIC,
        deterministic=Deterministic.NONE,
        method=ArmaMethod.EXACT_LIKELIHOOD,
        start=ArmaStart.ZEROS,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )

    assert selection.chosen.observations == 300
    assert selection.chosen.aic == min(value for _, value in selection.candidates)


def test_the_estimate_states_every_choice_and_its_class() -> None:
    values = _arma(9, 200, 1.0, [0.5], [])
    start = {"mean": 0.8, "ar_1": 0.2}
    least = fit_arma(
        values,
        order=(1, 0),
        deterministic=Deterministic.CONSTANT,
        method=ArmaMethod.CONDITIONAL_LEAST_SQUARES,
        start=start,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )
    exact = fit_arma(
        values,
        order=(1, 0),
        deterministic=Deterministic.CONSTANT,
        method=ArmaMethod.EXACT_LIKELIHOOD,
        start=ArmaStart.ZEROS,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )

    assert least.estimate.method == ArmaMethod.CONDITIONAL_LEAST_SQUARES.scheme
    assert least.estimate.numerical_class is NumericalClass.CORRECTLY_ROUNDED
    assert exact.estimate.numerical_class is NumericalClass.HOST_LIBM
    assert least.estimate.data_id == exact.estimate.data_id == series_identity(values)
    assert dict(least.estimate.settings) == {
        "ar_order": 1,
        "conditioned": 1,
        "deterministic": Deterministic.CONSTANT.value,
        "ma_order": 0,
        "max_iterations": ITERATIONS,
        "method": "conditional-least-squares",
        "observations": 200,
        "start": "explicit",
        "start_ar_1": 0.2,
        "start_mean": 0.8,
        "step": STEP,
        "tolerance": TOLERANCE,
    }
    assert least.estimate.convergence is not None
    assert least.estimate.convergence.reached <= TOLERANCE


def test_white_noise_about_zero_has_no_coefficient_and_no_iteration() -> None:
    values = _arma(3, 100, 0.0, [], [])
    fit = fit_arma(
        values,
        order=(0, 0),
        deterministic=Deterministic.NONE,
        method=ArmaMethod.EXACT_LIKELIHOOD,
        start=ArmaStart.ZEROS,
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        step=STEP,
    )

    assert dict(fit.estimate.parameters) == {
        "variance": pytest.approx(math.fsum(v * v for v in values) / 100, rel=1e-15)
    }
    assert fit.estimate.convergence is None


SERIES = _arma(1, 120, 0.0, [0.5], [])


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"order": (1,)}, "two non-negative integers"),
        ({"order": (-1, 0)}, "two non-negative integers"),
        ({"order": (True, 0)}, "two non-negative integers"),
        ({"deterministic": Deterministic.CONSTANT_TREND}, "about zero"),
        ({"method": "exact"}, "must be an ArmaMethod"),
        ({"start": {"ar_1": 1.0}}, "outside the region"),
        ({"start": {"ar_1": 0.5, "ma_1": 0.1}}, "names ar_1"),
        ({"start": {"ar_1": math.nan}}, "not a finite number"),
        ({"start": "zeros"}, "an ArmaStart or a mapping"),
        ({"values": SERIES[:MINIMUM_ARMA_OBSERVATIONS]}, "needs at least 51"),
        ({"tolerance": 0.0}, "tolerance"),
        ({"step": -1.0}, "step"),
        ({"max_iterations": 0}, "max_iterations"),
    ],
)
def test_what_a_fit_cannot_take_is_refused(arguments: dict[str, object], match: str) -> None:
    call: dict[str, object] = {
        "values": SERIES,
        "order": (1, 0),
        "deterministic": Deterministic.NONE,
        "method": ArmaMethod.CONDITIONAL_LEAST_SQUARES,
        "start": ArmaStart.ZEROS,
        "tolerance": TOLERANCE,
        "max_iterations": ITERATIONS,
        "step": STEP,
        **arguments,
    }
    with pytest.raises(EconometricsInputError, match=match):
        fit_arma(**call)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"criterion": LagCriterion.T_STATISTIC}, "AIC or BIC"),
        ({"start": {"ar_1": 0.1}}, "named rule"),
        ({"maximum_order": (1, 1, 1)}, "two non-negative integers"),
    ],
)
def test_what_a_selection_cannot_take_is_refused(arguments: dict[str, object], match: str) -> None:
    call: dict[str, object] = {
        "values": SERIES,
        "maximum_order": (1, 1),
        "criterion": LagCriterion.AIC,
        "deterministic": Deterministic.NONE,
        "method": ArmaMethod.CONDITIONAL_LEAST_SQUARES,
        "start": ArmaStart.ZEROS,
        "tolerance": TOLERANCE,
        "max_iterations": ITERATIONS,
        "step": STEP,
        **arguments,
    }
    with pytest.raises(EconometricsInputError, match=match):
        select_arma(**call)  # type: ignore[arg-type]
