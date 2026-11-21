"""The objective protocol: an objective states its class, and construction certifies or refuses it.

ADR-0063 section 2. Construction stays one authority, every method behind it
certified, so an objective says what it is:

* **The package's five declare theirs** -- minimum variance and mean-variance
  are convex quadratic, maximum diversification, risk parity and the robust
  objective convex with methods of their own -- and keep every identity, pinned
  here as v4.2's tree computed them.
* **A researcher's convex quadratic objective** states its form, a positive
  multiple of the problem's covariance and a linear term, and is solved by the
  certified quadratic methods. A tracking-error objective, ``(w - b)' C (w - b)``,
  is ``scale = 2`` and ``linear = -2 C b``: with the benchmark feasible the answer
  is the benchmark, and with it excluded the answer is the exact rational
  optimum. The dense and the factor-structured methods agree on it.
* **Its identity** names its class and kind before its rendering, so two
  objectives that render alike are two problems.
* **What no certified method takes is refused** before anything is solved:
  a non-convex objective, a convex one without a method of its own, a linear
  one, a quadratic one without its form or under a volatility cap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.portfolio_optimizer import (
    BoxUncertainty,
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    Convexity,
    ExpectedReturns,
    ExposureRange,
    MaximumDiversification,
    MeanVariance,
    MinimumVariance,
    Objective,
    QuadraticForm,
    QuadraticObjective,
    RiskParity,
    RobustMeanVariance,
    SolverSettings,
    WeightBounds,
    construct,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError
from tests.unit.portfolio_optimizer import test_construction_factor_path as factor_path
from tests.unit.portfolio_optimizer.qp_reference import Row, exact_optimum

ASSETS = ("A", "B", "C")
ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
MU = ExpectedReturns({"A": 0.01, "B": 0.02, "C": 0.015}, "USD", "1M", "unit forecast")
LONG_ONLY = ConstraintSet(net_exposure=ExposureRange.exactly(1.0), bounds=WeightBounds(0.0, 1.0))


def covariance() -> CovarianceMatrix:
    return CovarianceMatrix.from_rows(
        ASSETS, ROWS, currency="USD", period="1M", source="unit", observations=None
    )


@dataclass(frozen=True, slots=True)
class TrackingError:
    """Minimize ``(w - b)' C (w - b)``: the variance of the book's return against a benchmark's."""

    benchmark: Mapping[str, float]

    @property
    def convexity(self) -> Convexity:
        return Convexity.CONVEX_QUADRATIC

    def rendering(self) -> list[str]:
        return [
            f"benchmark[{asset}]={weight!r}" for asset, weight in sorted(self.benchmark.items())
        ]

    def quadratic_form(self, problem: ConstructionProblem) -> QuadraticForm:
        assert isinstance(problem.covariance, CovarianceMatrix)
        matrix = problem.covariance
        b = [self.benchmark[asset] for asset in problem.universe]
        cb = [
            sum(value * weight for value, weight in zip(row, b, strict=True))
            for row in matrix.values
        ]
        return QuadraticForm(2.0, dict(zip(problem.universe, (-2.0 * v for v in cb), strict=True)))


def problem(objective: object, constraints: ConstraintSet = LONG_ONLY) -> ConstructionProblem:
    return ConstructionProblem(covariance(), objective, constraints, SETTINGS)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The package's objectives
# --------------------------------------------------------------------------- #

SHIPPED = {
    "minimum_variance": (MinimumVariance(), Convexity.CONVEX_QUADRATIC),
    "mean_variance": (MeanVariance(MU, 3.0), Convexity.CONVEX_QUADRATIC),
    "maximum_diversification": (MaximumDiversification(), Convexity.CONVEX),
    "risk_parity": (
        RiskParity({"A": Decimal("0.5"), "B": Decimal("0.25"), "C": Decimal("0.25")}),
        Convexity.CONVEX,
    ),
    "robust_box": (
        RobustMeanVariance(MU, 3.0, BoxUncertainty({"A": 0.001, "B": 0.002, "C": 0.001})),
        Convexity.CONVEX,
    ),
}

#: Each problem's identity as the tree before the protocol (ad7d049) computed it.
PINNED = {
    "minimum_variance": "a92df07058a4cf041e7ad4166a4e28f43e766de3294681d43f7236892aac1c1d",
    "mean_variance": "8ba51913f5e6872b26d91de2ccab2975488f226c07768333efeef254e5424a76",
    "maximum_diversification": "043cbb27342c83e055d871fa9d6b27a5192479401f4cbb9783084c2328c6e1ca",
    "risk_parity": "bb95d6571e9d77ebd9ae2ae5fcfa48b4cf0f67a1b82271b2332796ae6b388522",
    "robust_box": "8d420c8d291d596f55aebe5ba6c0dbc42a496b5a478008719db2b4eac69a3dff",
}


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_the_package_objectives_declare_their_class_and_keep_their_identities(name: str) -> None:
    objective, kind = SHIPPED[name]
    assert isinstance(objective, Objective)
    assert objective.convexity is kind
    assert problem(objective).problem_id == PINNED[name]


def test_only_the_convex_quadratic_ones_could_be_stated_as_a_form() -> None:
    quadratic = [n for n, (_, kind) in SHIPPED.items() if kind is Convexity.CONVEX_QUADRATIC]
    assert quadratic == ["minimum_variance", "mean_variance"]


# --------------------------------------------------------------------------- #
# A researcher's convex quadratic objective
# --------------------------------------------------------------------------- #


def test_a_feasible_benchmark_is_its_own_least_tracking_error() -> None:
    benchmark = {"A": 0.5, "B": 0.3, "C": 0.2}
    objective = TrackingError(benchmark)
    assert isinstance(objective, QuadraticObjective)
    result = construct(problem(objective))
    assert result.status is ConstructionStatus.OPTIMAL
    assert result.weights is not None
    for asset, weight in benchmark.items():
        assert result.weights[asset] == pytest.approx(weight, abs=1e-9)


def test_an_excluded_benchmark_gives_the_exact_rational_optimum() -> None:
    # A is capped at 0.3 where the benchmark holds 0.6: the least tracking error
    # under the cap, held to the exhaustive exact enumeration.
    benchmark = {"A": 0.6, "B": 0.1, "C": 0.3}
    capped = ConstraintSet(
        net_exposure=ExposureRange.exactly(1.0),
        bounds=WeightBounds(0.0, 1.0, overrides={"A": (0.0, 0.3)}),
    )
    result = construct(problem(TrackingError(benchmark), capped))
    assert result.status is ConstructionStatus.OPTIMAL and result.weights is not None

    hessian = [[2.0 * value for value in row] for row in ROWS]
    b = [benchmark[asset] for asset in ASSETS]
    linear = [-2.0 * sum(v * w for v, w in zip(row, b, strict=True)) for row in ROWS]
    rows: Sequence[Row] = (
        ([1.0, 1.0, 1.0], 1.0, True),
        ([1.0, 0.0, 0.0], 0.0, False),
        ([0.0, 1.0, 0.0], 0.0, False),
        ([0.0, 0.0, 1.0], 0.0, False),
        ([-1.0, 0.0, 0.0], -0.3, False),
        ([0.0, -1.0, 0.0], -1.0, False),
        ([0.0, 0.0, -1.0], -1.0, False),
    )
    expected = exact_optimum(hessian, linear, rows)
    assert expected is not None
    assert [result.weights[asset] for asset in ASSETS] == pytest.approx(expected, abs=1e-8)
    assert result.weights["A"] == pytest.approx(0.3, abs=1e-9)


def test_the_dense_and_the_factor_structured_methods_agree_on_it() -> None:
    fast, slow, _ = factor_path._model(120)
    assets = fast.assets
    benchmark = {asset: 1.0 / len(assets) for asset in assets}

    @dataclass(frozen=True, slots=True)
    class Tilted:
        """Minimum variance tilted toward a stated vector: ``(1/2) w' C w - t' w``."""

        tilt: Mapping[str, float]

        @property
        def convexity(self) -> Convexity:
            return Convexity.CONVEX_QUADRATIC

        def rendering(self) -> list[str]:
            return [f"tilt[{a}]={v!r}" for a, v in sorted(self.tilt.items())]

        def quadratic_form(self, problem: ConstructionProblem) -> QuadraticForm:
            return QuadraticForm(1.0, {a: -v for a, v in self.tilt.items()})

    tilt = {asset: 0.0005 * ((index % 7) - 3) for index, asset in enumerate(assets)}
    constraints = ConstraintSet(
        net_exposure=ExposureRange.exactly(1.0), bounds=WeightBounds(0.0, 0.05)
    )
    settings = factor_path.SETTINGS
    results = [
        construct(ConstructionProblem(matrix, Tilted(tilt), constraints, settings))
        for matrix in (fast, slow)
    ]
    assert [r.status for r in results] == [ConstructionStatus.OPTIMAL] * 2
    first, second = (r.weights for r in results)
    assert first is not None and second is not None
    assert max(abs(first[a] - second[a]) for a in assets) < 1e-6
    # Solved differently, so identified differently.
    ids = {
        ConstructionProblem(m, Tilted(tilt), constraints, settings).problem_id for m in (fast, slow)
    }
    assert len(ids) == 2
    assert sum(benchmark.values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OtherTrackingError(TrackingError):
    """The same rendering under another class."""


def test_an_objective_of_the_protocol_is_identified_by_its_class_and_kind() -> None:
    benchmark = {"A": 0.5, "B": 0.3, "C": 0.2}
    first = problem(TrackingError(benchmark)).problem_id
    again = problem(TrackingError(dict(benchmark))).problem_id
    other = problem(OtherTrackingError(benchmark)).problem_id
    moved = problem(TrackingError({"A": 0.4, "B": 0.4, "C": 0.2})).problem_id
    assert first == again
    assert len({first, other, moved}) == 3


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Declared:
    """An objective that declares a class and renders, nothing more."""

    kind: Convexity

    @property
    def convexity(self) -> Convexity:
        return self.kind

    def rendering(self) -> list[str]:
        return [f"declared={self.kind.value}"]


@pytest.mark.parametrize(
    ("objective", "reason"),
    [
        (Declared(Convexity.NONCONVEX), "NONCONVEX: no certified method"),
        (Declared(Convexity.CONVEX), "only the package's own convex objectives"),
        (Declared(Convexity.LINEAR), "no certified method for a linear objective"),
        (Declared(Convexity.CONVEX_QUADRATIC), "states no quadratic_form"),
        (object(), "is not a construction objective"),
    ],
    ids=["nonconvex", "convex", "linear", "no-form", "not-an-objective"],
)
def test_what_no_certified_method_takes_is_refused_before_solving(
    objective: object, reason: str
) -> None:
    with pytest.raises(ConstructionInputError, match=reason):
        construct(problem(objective))


def test_a_volatility_cap_under_an_objective_of_the_protocol_is_refused() -> None:
    capped = ConstraintSet(
        net_exposure=ExposureRange.exactly(1.0), bounds=WeightBounds(0.0, 1.0), max_volatility=0.3
    )
    with pytest.raises(ConstructionInputError, match="volatility cap"):
        construct(problem(TrackingError({"A": 0.5, "B": 0.3, "C": 0.2}), capped))


def test_a_form_that_does_not_cover_the_universe_is_refused() -> None:
    @dataclass(frozen=True, slots=True)
    class Partial(TrackingError):
        def quadratic_form(self, problem: ConstructionProblem) -> QuadraticForm:
            return QuadraticForm(2.0, {"A": 0.0, "B": 0.0})

    with pytest.raises(ConstructionInputError, match="cover exactly the universe"):
        construct(problem(Partial({"A": 0.5, "B": 0.3, "C": 0.2})))


@pytest.mark.parametrize("scale", [0.0, -1.0, float("inf"), float("nan")])
def test_a_form_without_a_positive_finite_scale_is_refused(scale: float) -> None:
    with pytest.raises(ConstructionInputError):
        QuadraticForm(scale, {"A": 0.0})
