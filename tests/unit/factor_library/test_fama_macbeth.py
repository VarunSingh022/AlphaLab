"""Fama and MacBeth's two passes (v4.3, WS-25).

* **Each cross-section is least squares**, held to the exact rational solution,
  and the premium is the mean of the coefficients, with Newey and West's
  standard error, held to its formula in rationals.
* **Known premiums are recovered** within three standard errors from a panel
  whose premiums vary over time about a known mean.
* **What is skipped is counted**: a cross-section too small, and one whose
  design is singular, each by its reason; neither is padded.
* **The identity is the data's**: every value read enters it, so one value
  changed changes it, and the estimate, class 2, covers its values.
* **What it cannot take is refused.**
"""

from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from alphalab.common.estimate import NumericalClass
from alphalab.common.statistics import newey_west_standard_error
from alphalab.factor_library import (
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    ForwardReturnPanel,
)
from alphalab.factor_library.fama_macbeth import FAMA_MACBETH_METHOD, fama_macbeth

DAY = 86400.0
START = 1_735_689_600.0
CONDITION = 1e8


def _panel(rows: dict[float, dict[str, float]], name: str) -> FeaturePanel:
    definition = FeatureDefinition(name, FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=1)
    return FeaturePanel(
        definition=definition,
        dataset_version="ds@v1",
        timezone_name="UTC",
        rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
        symbols=tuple(sorted({asset for row in rows.values() for asset in row})),
    )


def _returns(rows: dict[float, dict[str, float]], horizon: int = 1) -> ForwardReturnPanel:
    return ForwardReturnPanel(
        horizon=horizon,
        rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
        dataset_version="ds@v1",
        timezone_name="UTC",
        symbols=tuple(sorted({asset for row in rows.values() for asset in row})),
        unrealized_instants=0,
        lag=1,
        delisted_instants=0,
        unenterable_instants=0,
        delistings=(),
    )


def _exact_least_squares(design: list[list[float]], values: list[float]) -> list[Fraction]:
    """``(X'X)⁻¹ X'y`` in rationals, by Gauss-Jordan."""

    width = len(design[0])
    xs = [[Fraction(v) for v in row] for row in design]
    ys = [Fraction(v) for v in values]
    gram = [
        [sum((row[i] * row[k] for row in xs), Fraction(0)) for k in range(width)]
        + [sum((row[i] * y for row, y in zip(xs, ys, strict=True)), Fraction(0))]
        for i in range(width)
    ]
    for column in range(width):
        pivot = next(r for r in range(column, width) if gram[r][column] != 0)
        gram[column], gram[pivot] = gram[pivot], gram[column]
        for r in range(width):
            if r != column and gram[r][column] != 0:
                ratio = gram[r][column] / gram[column][column]
                gram[r] = [a - ratio * b for a, b in zip(gram[r], gram[column], strict=True)]
    return [gram[i][width] / gram[i][i] for i in range(width)]


SMALL_FACTOR = {
    START: {"A": 0.5, "B": -1.0, "C": 1.5, "D": 0.25, "E": -0.75},
    START + DAY: {"A": 1.0, "B": 0.5, "C": -0.5, "D": 2.0, "E": 0.0},
    START + 2 * DAY: {"A": -0.25, "B": 0.75, "C": 1.25, "D": -1.5, "E": 0.5},
}
SMALL_RETURNS = {
    START: {"A": 0.0625, "B": -0.125, "C": 0.1875, "D": 0.0, "E": -0.0625},
    START + DAY: {"A": 0.125, "B": 0.0625, "C": -0.03125, "D": 0.25, "E": 0.015625},
    START + 2 * DAY: {"A": -0.03125, "B": 0.09375, "C": 0.125, "D": -0.1875, "E": 0.03125},
}


def test_each_cross_section_is_least_squares_and_the_premium_its_mean() -> None:
    factor = _panel(SMALL_FACTOR, "momentum")
    result = fama_macbeth(
        [factor],
        _returns(SMALL_RETURNS),
        intercept=True,
        minimum_assets=3,
        newey_west_lag=0,
        maximum_condition=CONDITION,
    )

    exact = []
    for instant in sorted(SMALL_FACTOR):
        assets = sorted(SMALL_FACTOR[instant])
        design = [[1.0, SMALL_FACTOR[instant][a]] for a in assets]
        coefficients = _exact_least_squares(design, [SMALL_RETURNS[instant][a] for a in assets])
        exact.append(coefficients)
    for (_instant, got), expected in zip(result.per_instant, exact, strict=True):
        assert all(
            abs(Fraction(g) - e) <= Fraction(1, 10**15) for g, e in zip(got, expected, strict=True)
        )
    for position, name in enumerate(("intercept", "momentum")):
        series = [row[position] for row in exact]
        mean = sum(series, Fraction(0)) / 3
        variance = sum(((s - mean) ** 2 for s in series), Fraction(0)) / 3
        assert abs(Fraction(result.estimate.parameters[name]) - mean) <= Fraction(1, 10**15)
        assert result.estimate.standard_errors[name] == pytest.approx(
            math.sqrt(float(variance / 3)), rel=1e-12
        )
        assert result.t_statistics[name] == pytest.approx(
            result.estimate.parameters[name] / result.estimate.standard_errors[name], rel=1e-15
        )
    assert (result.instants_measured, result.observations) == (3, 15)


def _simulated(
    seed: int, instants: int, assets: int
) -> tuple[FeaturePanel, FeaturePanel, ForwardReturnPanel]:
    draws = random.Random(seed)
    names = [f"S{i:03d}" for i in range(assets)]
    first, second, returns = {}, {}, {}
    for t in range(instants):
        stamp = START + t * DAY
        g1 = 0.02 + draws.gauss(0.0, 0.01)
        g2 = -0.01 + draws.gauss(0.0, 0.01)
        first[stamp] = {n: draws.gauss(0.0, 1.0) for n in names}
        second[stamp] = {n: draws.gauss(0.0, 1.0) for n in names}
        returns[stamp] = {
            n: 0.001 + g1 * first[stamp][n] + g2 * second[stamp][n] + draws.gauss(0.0, 0.05)
            for n in names
        }
    return _panel(first, "momentum"), _panel(second, "value"), _returns(returns)


def test_known_premiums_are_recovered_within_their_standard_errors() -> None:
    momentum, value, returns = _simulated(3, 240, 60)
    result = fama_macbeth(
        [momentum, value],
        returns,
        intercept=True,
        minimum_assets=10,
        newey_west_lag=0,
        maximum_condition=CONDITION,
    )

    for name, truth in (("intercept", 0.001), ("momentum", 0.02), ("value", -0.01)):
        estimate = result.estimate.parameters[name]
        error = result.estimate.standard_errors[name]
        assert abs(estimate - truth) < 3.0 * error, (name, estimate, truth, error)
    assert result.t_statistics["momentum"] > 10.0
    assert result.factor_lineage == (momentum.lineage, value.lineage)


def test_the_standard_error_is_newey_and_wests_at_the_stated_lag() -> None:
    momentum, value, returns = _simulated(5, 60, 20)
    result = fama_macbeth(
        [momentum, value],
        returns,
        intercept=True,
        minimum_assets=10,
        newey_west_lag=3,
        maximum_condition=CONDITION,
    )

    for position, name in enumerate(("intercept", "momentum", "value")):
        series = [coefficients[position] for _, coefficients in result.per_instant]
        assert result.estimate.standard_errors[name] == newey_west_standard_error(series, 3)
        assert result.estimate.standard_errors[name] != newey_west_standard_error(series, 0)


def test_a_cross_section_too_small_or_singular_is_skipped_and_counted() -> None:
    factor = dict(SMALL_FACTOR)
    returns = dict(SMALL_RETURNS)
    factor[START + 3 * DAY] = {"A": 1.0, "B": 2.0}
    returns[START + 3 * DAY] = {"A": 0.1, "B": 0.2, "C": 0.3}
    factor[START + 4 * DAY] = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}
    returns[START + 4 * DAY] = {"A": 0.1, "B": 0.2, "C": 0.3, "D": 0.0}
    result = fama_macbeth(
        [_panel(factor, "momentum")],
        _returns(returns),
        intercept=True,
        minimum_assets=3,
        newey_west_lag=0,
        maximum_condition=CONDITION,
    )

    assert (
        result.instants_measured,
        result.instants_too_small,
        result.instants_ill_conditioned,
    ) == (
        3,
        1,
        1,
    )
    assert [instant for instant, _ in result.per_instant] == sorted(SMALL_FACTOR)


def test_the_data_identity_covers_every_value_read_and_the_estimate_its_values() -> None:
    def run(returns: dict[float, dict[str, float]]) -> str:
        result = fama_macbeth(
            [_panel(SMALL_FACTOR, "momentum")],
            _returns(returns),
            intercept=True,
            minimum_assets=3,
            newey_west_lag=1,
            maximum_condition=CONDITION,
        )
        assert result.estimate.method == FAMA_MACBETH_METHOD
        assert result.estimate.numerical_class is NumericalClass.CORRECTLY_ROUNDED
        assert dict(result.estimate.settings) == {
            "horizon": 1,
            "intercept": True,
            "lag": 1,
            "maximum_condition": CONDITION,
            "minimum_assets": 3,
            "newey_west_lag": 1,
        }
        return result.estimate.data_id

    moved = {stamp: dict(row) for stamp, row in SMALL_RETURNS.items()}
    moved[START]["E"] = -0.0625 + 2.0**-40
    assert run(SMALL_RETURNS) == run(dict(SMALL_RETURNS))
    assert run(SMALL_RETURNS) != run(moved)


FACTOR = _panel(SMALL_FACTOR, "momentum")
RETURNS = _returns(SMALL_RETURNS)


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"factors": []}, "one factor at least"),
        ({"factors": ["momentum"]}, "not a FeaturePanel"),
        ({"factors": [FACTOR, FACTOR]}, "repeat"),
        ({"factors": [_panel(SMALL_FACTOR, "intercept")]}, "repeat"),
        ({"returns": SMALL_RETURNS}, "ForwardReturnPanel"),
        ({"intercept": 1}, "intercept is a bool"),
        ({"minimum_assets": 2}, "minimum_assets is an integer of 3"),
        ({"newey_west_lag": -1}, "newey_west_lag"),
        ({"maximum_condition": 0.5}, "maximum_condition"),
        ({"newey_west_lag": 2}, "needs 4 at least"),
        ({"minimum_assets": 6}, "0 cross-sections were measured"),
    ],
)
def test_what_fama_macbeth_cannot_take_is_refused(arguments: dict[str, object], match: str) -> None:
    call: dict[str, object] = {
        "factors": [FACTOR],
        "returns": RETURNS,
        "intercept": True,
        "minimum_assets": 3,
        "newey_west_lag": 0,
        "maximum_condition": CONDITION,
        **arguments,
    }
    with pytest.raises(FactorInputError, match=match):
        fama_macbeth(**call)  # type: ignore[arg-type]
