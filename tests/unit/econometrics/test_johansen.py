"""Johansen's cointegration rank tests and their published tables (v4.3, ADR-0074, WS-07).

* **The tables are the paper's.** MacKinnon, Haug and Michelis (1996)'s Tables 1
  to 5 were decoded from the working paper's own text and generated into the
  module. They are held by their structure, by the exact chi-square rows the
  theory gives for one dimension, and by an independent four-decimal
  transcription made from the authors' program, for the three cases it covers.
* **The statistics are exact.** A two-series system's eigenvalues and
  statistics are held to exact rational arithmetic and 50-digit roots.
* **Rank is found, and the null keeps its size.** A pair built cointegrated is
  found so, its relation recovered. Under each case, with data that has what
  the case assumes, the trace test rejects a true null at its nominal rate.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from decimal import Decimal, localcontext
from fractions import Fraction
from statistics import NormalDist

import pytest

from alphalab.common.linalg import IllConditionedError
from alphalab.econometrics.cointegration import (
    MINIMUM_JOHANSEN_OBSERVATIONS,
    johansen,
)
from alphalab.econometrics.critical_values import (
    JOHANSEN_LEVELS,
    MHM_1996,
    JohansenCase,
    JohansenStatistic,
    PBound,
    johansen_critical_values,
    johansen_p_value,
)
from alphalab.econometrics.exceptions import EconometricsInputError
from alphalab.econometrics.series import system_identity
from alphalab.econometrics.unit_root import FixedLags, LagCriterion, SelectedLags

TOLERANCE = 1e-15
ITERATIONS = 60
CONDITION = 1e10
CASES = list(JohansenCase)
STATISTICS = list(JohansenStatistic)


def _row(case: JohansenCase, statistic: JohansenStatistic, dimension: int) -> list[float]:
    values = johansen_critical_values(case, statistic, dimension).values
    return [values[level] for level in JOHANSEN_LEVELS]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("statistic", STATISTICS)
def test_every_table_falls_with_its_size_and_rises_with_its_dimension(
    case: JohansenCase, statistic: JohansenStatistic
) -> None:
    previous = None
    for dimension in range(1, 13):
        row = _row(case, statistic, dimension)
        assert row == sorted(row, reverse=True) and len(set(row)) == 5
        if previous is not None:
            assert all(a > b for a, b in zip(row, previous, strict=True))
        previous = row


@pytest.mark.parametrize("case", CASES)
def test_the_trace_exceeds_the_maximum_eigenvalue_beyond_one_dimension(case: JohansenCase) -> None:
    for dimension in range(2, 13):
        trace = _row(case, JohansenStatistic.TRACE, dimension)
        maximum = _row(case, JohansenStatistic.MAXIMUM_EIGENVALUE, dimension)
        assert all(a > b for a, b in zip(trace, maximum, strict=True))
    # In one dimension the two statistics are one: the paper prints them equal,
    # but for Case 0's 20 percent value, estimated twice (1.88 and 1.89).
    trace = _row(case, JohansenStatistic.TRACE, 1)
    maximum = _row(case, JohansenStatistic.MAXIMUM_EIGENVALUE, 1)
    if case is JohansenCase.NONE:
        assert trace[:4] == maximum[:4] and (maximum[4], trace[4]) == (1.88, 1.89)
    else:
        assert trace == maximum


@pytest.mark.parametrize(
    "case", [JohansenCase.UNRESTRICTED_CONSTANT, JohansenCase.UNRESTRICTED_TREND]
)
def test_with_an_unrestricted_trend_one_dimension_is_chi_square_with_one_degree(
    case: JohansenCase,
) -> None:
    """Johansen (1995): the trend the levels carry makes the last statistic chi-square(1)."""

    for level, value in zip(JOHANSEN_LEVELS, _row(case, JohansenStatistic.TRACE, 1), strict=True):
        quantile = NormalDist().inv_cdf(1.0 - level / 2.0) ** 2
        assert value == round(quantile, 2)


# The authors' program's values, as a widely used transcription prints them
# (LeSage's econometrics toolbox, as statsmodels carries it): 90, 95, 99 percent
# for p - r = 1 to 12. An independent copy, made from the program, not the tables.
_PROGRAM = {
    (JohansenCase.NONE, JohansenStatistic.MAXIMUM_EIGENVALUE): """
        2.9762 4.1296 6.9406 9.4748 11.2246 15.0923 15.7175 17.7961 22.2519 21.8370 24.1592
        29.0609 27.9160 30.4428 35.7359 33.9271 36.6301 42.2333 39.9085 42.7679 48.6606
        45.8930 48.8795 55.0335 51.8528 54.9629 61.3449 57.7954 61.0404 67.6415 63.7248
        67.0756 73.8856 69.6513 73.0946 80.0937""",
    (JohansenCase.UNRESTRICTED_CONSTANT, JohansenStatistic.MAXIMUM_EIGENVALUE): """
        2.7055 3.8415 6.6349 12.2971 14.2639 18.5200 18.8928 21.1314 25.8650 25.1236 27.5858
        32.7172 31.2379 33.8777 39.3693 37.2786 40.0763 45.8662 43.2947 46.2299 52.3069
        49.2855 52.3622 58.6634 55.2412 58.4332 64.9960 61.2041 64.5040 71.2525 67.1307
        70.5392 77.4877 73.0563 76.5734 83.7105""",
    (JohansenCase.UNRESTRICTED_TREND, JohansenStatistic.MAXIMUM_EIGENVALUE): """
        2.7055 3.8415 6.6349 15.0006 17.1481 21.7465 21.8731 24.2522 29.2631 28.2398 30.8151
        36.1930 34.4202 37.1646 42.8612 40.5244 43.4183 49.4095 46.5583 49.5875 55.8171
        52.5858 55.7302 62.1741 58.5316 61.8051 68.5030 64.5292 67.9040 74.7434 70.4630
        73.9355 81.0678 76.4081 79.9878 87.2395""",
    (JohansenCase.NONE, JohansenStatistic.TRACE): """
        2.9762 4.1296 6.9406 10.4741 12.3212 16.3640 21.7781 24.2761 29.5147 37.0339 40.1749
        46.5716 56.2839 60.0627 67.6367 79.5329 83.9383 92.7136 106.7351 111.7797 121.7375
        137.9954 143.6691 154.7977 173.2292 179.5199 191.8122 212.4721 219.4051 232.8291
        255.6732 263.2603 277.9962 302.9054 311.1288 326.9716""",
    (JohansenCase.UNRESTRICTED_CONSTANT, JohansenStatistic.TRACE): """
        2.7055 3.8415 6.6349 13.4294 15.4943 19.9349 27.0669 29.7961 35.4628 44.4929 47.8545
        54.6815 65.8202 69.8189 77.8202 91.1090 95.7542 104.9637 120.3673 125.6185 135.9825
        153.6341 159.5290 171.0905 190.8714 197.3772 210.0366 232.1030 239.2468 253.2526
        277.3740 285.1402 300.2821 326.5354 334.9795 351.2150""",
    (JohansenCase.UNRESTRICTED_TREND, JohansenStatistic.TRACE): """
        2.7055 3.8415 6.6349 16.1619 18.3985 23.1485 32.0645 35.0116 41.0815 51.6492 55.2459
        62.5202 75.1027 79.3422 87.7748 102.4674 107.3429 116.9829 133.7852 139.2780 150.0778
        169.0618 175.1584 187.1891 208.3582 215.1268 228.2226 251.6293 259.0267 273.3838
        298.8836 306.8988 322.4264 350.1125 358.7190 375.3203""",
}


@pytest.mark.parametrize(("case", "statistic"), list(_PROGRAM))
def test_the_tables_agree_with_the_authors_program_as_independently_transcribed(
    case: JohansenCase, statistic: JohansenStatistic
) -> None:
    """Every one of the 216 shared values within 0.035: the same estimates, two routes."""

    program = [Decimal(value) for value in _PROGRAM[case, statistic].split()]
    for dimension in range(1, 13):
        values = johansen_critical_values(case, statistic, dimension).values
        for level, expected in zip(
            (0.10, 0.05, 0.01), program[3 * (dimension - 1) : 3 * dimension], strict=True
        ):
            # Compared as the decimals both print, not as binary floats.
            assert abs(Decimal(repr(values[level])) - expected) <= Decimal("0.035"), dimension


def test_a_p_value_is_interpolated_within_the_table_and_a_bound_beyond_it() -> None:
    case, statistic = JohansenCase.RESTRICTED_CONSTANT, JohansenStatistic.TRACE
    values = johansen_critical_values(case, statistic, 2).values
    assert johansen_critical_values(case, statistic, 2).source == MHM_1996
    assert johansen_critical_values(case, statistic, 2).observations is None
    middle = (values[0.05] + values[0.10]) / 2
    within = johansen_p_value(middle, case, statistic, 2)
    assert within.bound is None and within.value == pytest.approx(0.075)
    assert johansen_p_value(values[0.05], case, statistic, 2).value == pytest.approx(0.05)
    below = johansen_p_value(0.5, case, statistic, 2)
    assert (below.value, below.bound) == (0.20, PBound.AT_LEAST)
    beyond = johansen_p_value(values[0.01] + 5, case, statistic, 2)
    assert (beyond.value, beyond.bound) == (0.01, PBound.AT_MOST)


@pytest.mark.parametrize("dimension", [0, 13, True, 2.0])
def test_a_dimension_the_tables_do_not_reach_is_refused(dimension: object) -> None:
    with pytest.raises(EconometricsInputError, match="p - r from 1 to 12"):
        johansen_critical_values(JohansenCase.NONE, JohansenStatistic.TRACE, dimension)  # type: ignore[arg-type]


def _dyadic_system(count: int) -> list[list[float]]:
    state = 7
    columns: list[list[float]] = [[0.0], [0.0]]
    for _ in range(count - 1):
        state = (1103515245 * state + 12345) % 2**31
        step = ((state >> 8) % 17 - 8) / 8
        state = (1103515245 * state + 12345) % 2**31
        noise = ((state >> 8) % 9 - 4) / 4
        columns[0].append(columns[0][-1] + step)
        columns[1].append(columns[0][-1] * 0.5 + noise)
    return columns


def _exact_eigenvalues(
    series: list[list[float]], case: JohansenCase, lags: int
) -> tuple[int, list[Decimal]]:
    """The two nonzero eigenvalues by exact rational arithmetic and a 50-digit root.

    Each residual is the exact projection off the regressors, by Gaussian
    elimination in ``Fraction``; the eigenvalues are those of the 2 x 2 matrix
    ``S00⁻¹ S01 S11⁻¹ S10``, the roots of its characteristic quadratic.
    """

    start = lags + 1
    rows = range(start, len(series[0]))
    observations = len(rows)
    xs = [[Fraction(v) for v in x] for x in series]
    changes = [[x[t] - x[t - 1] for t in rows] for x in xs]
    levels = [[x[t - 1] for t in rows] for x in xs]
    regressors = [[x[t - i] - x[t - i - 1] for t in rows] for i in range(1, lags + 1) for x in xs]
    ones = [Fraction(1)] * observations
    trend = [Fraction(k + 1, observations) for k in range(observations)]
    if case is JohansenCase.RESTRICTED_CONSTANT:
        levels.append(ones)
    elif case is JohansenCase.UNRESTRICTED_CONSTANT:
        regressors.append(ones)
    elif case is JohansenCase.RESTRICTED_TREND:
        regressors.append(ones)
        levels.append(trend)
    elif case is JohansenCase.UNRESTRICTED_TREND:
        regressors += [ones, trend]

    def dot(a: list[Fraction], b: list[Fraction]) -> Fraction:
        return sum((u * v for u, v in zip(a, b, strict=True)), Fraction(0))

    def solve(matrix: list[list[Fraction]], rhs: list[Fraction]) -> list[Fraction]:
        size = len(matrix)
        rows_ = [[*row, rhs[i]] for i, row in enumerate(matrix)]
        for column in range(size):
            pivot = next(r for r in range(column, size) if rows_[r][column] != 0)
            rows_[column], rows_[pivot] = rows_[pivot], rows_[column]
            for r in range(size):
                if r != column and rows_[r][column] != 0:
                    factor = rows_[r][column] / rows_[column][column]
                    rows_[r] = [
                        a - factor * b for a, b in zip(rows_[r], rows_[column], strict=True)
                    ]
        return [rows_[i][size] / rows_[i][i] for i in range(size)]

    def residual(target: list[Fraction]) -> list[Fraction]:
        if not regressors:
            return target
        gram = [[dot(a, b) for b in regressors] for a in regressors]
        coefficients = solve(gram, [dot(a, target) for a in regressors])
        return [
            target[t]
            - sum((c * z[t] for c, z in zip(coefficients, regressors, strict=True)), Fraction(0))
            for t in range(observations)
        ]

    r0, r1 = [residual(y) for y in changes], [residual(y) for y in levels]

    def moments(a: list[list[Fraction]], b: list[list[Fraction]]) -> list[list[Fraction]]:
        return [[dot(u, v) for v in b] for u in a]

    def inverse(matrix: list[list[Fraction]]) -> list[list[Fraction]]:
        size = len(matrix)
        columns = [solve(matrix, [Fraction(int(i == j)) for i in range(size)]) for j in range(size)]
        return [[columns[j][i] for j in range(size)] for i in range(size)]

    def product(a: list[list[Fraction]], b: list[list[Fraction]]) -> list[list[Fraction]]:
        return [
            [sum((a[i][k] * b[k][j] for k in range(len(b))), Fraction(0)) for j in range(len(b[0]))]
            for i in range(len(a))
        ]

    matrix = product(
        product(inverse(moments(r0, r0)), moments(r0, r1)),
        product(inverse(moments(r1, r1)), moments(r1, r0)),
    )
    trace = matrix[0][0] + matrix[1][1]
    determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    with localcontext() as context:
        context.prec = 50
        tr = Decimal(trace.numerator) / trace.denominator
        det = Decimal(determinant.numerator) / determinant.denominator
        root = (tr * tr - 4 * det).sqrt()
        return observations, [(tr + root) / 2, (tr - root) / 2]


@pytest.mark.parametrize("lags", [0, 1, 2])
@pytest.mark.parametrize("case", CASES)
def test_every_cases_eigenvalues_and_statistics_are_the_exact_ones(
    case: JohansenCase, lags: int
) -> None:
    series = _dyadic_system(40)
    test = johansen(
        series,
        case=case,
        lags=FixedLags(lags),
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        maximum_condition=CONDITION,
    )

    observations, exact = _exact_eigenvalues(series, case, lags)
    with localcontext() as context:
        context.prec = 50
        logs = [(1 - value).ln() for value in exact]
        traces = [-observations * (logs[0] + logs[1]), -observations * logs[1]]

    assert (test.observations, test.variables) == (observations, 2)
    for rank, value in zip(test.ranks, exact, strict=True):
        assert rank.eigenvalue == pytest.approx(float(value), rel=1e-13)
    for rank, value in zip(test.ranks, traces, strict=True):
        assert rank.trace == pytest.approx(float(value), rel=1e-12)
    assert test.ranks[0].maximum_eigenvalue == pytest.approx(
        float(-observations * logs[0]), rel=1e-12
    )
    assert test.ranks[1].maximum_eigenvalue == test.ranks[1].trace


def _walks(
    draws: random.Random, count: int, length: int, drift: Callable[[int], float]
) -> list[list[float]]:
    series = []
    for _ in range(count):
        values = [0.0]
        for t in range(1, length):
            values.append(values[-1] + drift(t) + draws.gauss(0.0, 1.0))
        series.append(values)
    return series


def test_a_cointegrated_pair_has_rank_one_and_its_relation_is_recovered() -> None:
    draws = random.Random(11)
    x = _walks(draws, 1, 600, lambda t: 0.0)[0]
    noise = [0.0]
    for _ in range(599):
        noise.append(0.4 * noise[-1] + draws.gauss(0.0, 1.0))
    y = [2.0 + 1.5 * a + b for a, b in zip(x, noise, strict=True)]

    test = johansen(
        [y, x],
        case=JohansenCase.RESTRICTED_CONSTANT,
        lags=FixedLags(1),
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        maximum_condition=CONDITION,
    )

    assert test.ranks[0].trace > test.ranks[0].trace_critical_values.values[0.01]
    assert test.ranks[1].trace < test.ranks[1].trace_critical_values.values[0.05]
    beta = test.estimate.parameters
    first = beta["beta_1_series_1"]
    assert beta["beta_1_series_2"] / first == pytest.approx(-1.5, abs=0.05)
    assert beta["beta_1_constant"] / first == pytest.approx(-2.0, abs=0.5)


#: Data with what each case assumes under the null of no cointegration.
_NULL_DRIFT: dict[JohansenCase, Callable[[int], float]] = {
    JohansenCase.NONE: lambda t: 0.0,
    JohansenCase.RESTRICTED_CONSTANT: lambda t: 0.0,
    JohansenCase.UNRESTRICTED_CONSTANT: lambda t: 0.5,
    JohansenCase.RESTRICTED_TREND: lambda t: 0.5,
    JohansenCase.UNRESTRICTED_TREND: lambda t: 0.5 + 0.01 * t,
}


@pytest.mark.parametrize("case", CASES)
def test_under_a_true_null_the_trace_test_rejects_at_its_nominal_rate(case: JohansenCase) -> None:
    """150 pairs of independent walks of 150: rejections at 5 percent within three deviations."""

    draws = random.Random(4400 + CASES.index(case))
    rejections = 0
    replications = 150
    for _ in range(replications):
        series = _walks(draws, 2, 150, _NULL_DRIFT[case])
        test = johansen(
            series,
            case=case,
            lags=FixedLags(0),
            tolerance=TOLERANCE,
            max_iterations=ITERATIONS,
            maximum_condition=CONDITION,
        )
        rejections += test.ranks[0].trace > test.ranks[0].trace_critical_values.values[0.05]
    deviation = math.sqrt(0.05 * 0.95 / replications)
    assert abs(rejections / replications - 0.05) <= 3 * deviation, rejections


def test_the_estimate_is_identified_by_the_system_in_order_and_every_choice() -> None:
    series = _dyadic_system(60)
    test = johansen(
        series,
        case=JohansenCase.RESTRICTED_TREND,
        lags=FixedLags(2),
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        maximum_condition=CONDITION,
    )
    estimate = test.estimate
    assert estimate.data_id == system_identity(series)
    assert dict(estimate.settings) == {
        "case": "restricted_trend",
        "lags": 2,
        "maximum_condition": CONDITION,
        "observations": 57,
        "tolerance": TOLERANCE,
        "max_iterations": ITERATIONS,
        "variables": 2,
    }
    assert estimate.numerical_class.identity_bearing
    assert estimate.convergence is not None
    assert {"beta_1_trend", "beta_2_trend", "eigenvalue_1", "eigenvalue_2"} <= set(
        estimate.parameters
    )
    reversed_order = johansen(
        series[::-1],
        case=JohansenCase.RESTRICTED_TREND,
        lags=FixedLags(2),
        tolerance=TOLERANCE,
        max_iterations=ITERATIONS,
        maximum_condition=CONDITION,
    )
    assert reversed_order.estimate.estimate_id != estimate.estimate_id
    assert [rank.eigenvalue for rank in reversed_order.ranks] == pytest.approx(
        [rank.eigenvalue for rank in test.ranks], rel=1e-12
    ), "the eigenvalues do not depend on the order"


def _call(series: object, **changes: object) -> object:
    arguments: dict[str, object] = {
        "case": JohansenCase.NONE,
        "lags": FixedLags(0),
        "tolerance": TOLERANCE,
        "max_iterations": ITERATIONS,
        "maximum_condition": CONDITION,
    }
    arguments.update(changes)
    return johansen(series, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("series", "changes", "error", "match"),
    [
        ("one", {}, EconometricsInputError, "2 to 12 series"),
        ("thirteen", {}, EconometricsInputError, "2 to 12 series"),
        ("ragged", {}, EconometricsInputError, "one set of instants"),
        ("nan", {}, EconometricsInputError, "not finite"),
        ("short", {}, EconometricsInputError, f"{MINIMUM_JOHANSEN_OBSERVATIONS} at least"),
        (
            "pair",
            {"lags": SelectedLags(LagCriterion.BIC, 4, None)},
            EconometricsInputError,
            "FixedLags",
        ),
        ("pair", {"case": "none"}, EconometricsInputError, "JohansenCase"),
        ("twins", {}, IllConditionedError, "combination of the others"),
    ],
)
def test_what_johansen_cannot_take_is_refused(
    series: str, changes: dict[str, object], error: type[Exception], match: str
) -> None:
    draws = random.Random(3)
    walks = _walks(draws, 13, 80, lambda t: 0.0)
    systems = {
        "one": walks[:1],
        "thirteen": walks,
        "ragged": [walks[0], walks[1][:-1]],
        "nan": [walks[0], [*walks[1][:-1], float("nan")]],
        "short": [walks[0][:20], walks[1][:20]],
        "pair": walks[:2],
        "twins": [walks[0], walks[0]],
    }
    with pytest.raises(error, match=match):
        _call(systems[series], **changes)
