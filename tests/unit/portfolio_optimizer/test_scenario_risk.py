"""Scenario risk through :func:`construct`: CVaR and drawdown, lifted and certified (ADR-0063).

* **A scenario set** is supplied, kept in its order, and identified by its
  values. Its CVaR and drawdown are held to exact evaluations: CVaR as the
  least of Rockafellar and Uryasev's function over every candidate threshold,
  in rational arithmetic; drawdown on a hand-computed path.
* **Each lifting is held to a program that shares nothing with it**, solved by
  the exact vertex enumeration:
  * a CVaR limit at ``k`` whole scenarios as one row per set of ``k``
    scenarios;
  * a maximum drawdown as one row per pair of steps;
  * minimum CVaR as the least threshold over those rows.
  At a level with a fractional share, the answer is held to exact evaluation
  and to a grid of portfolios, none of which does better.
* **Construction checks a limit at its own value**: a researcher's limit whose
  value disagrees with its lifting is caught, not trusted.
* **Identity, diagnostics and refusals**: limits enter the identity in any
  order and only when present, their values are reported, and what no
  certified method takes is refused.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.portfolio_optimizer import (
    CDaRLimit,
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    Convexity,
    CVaRLimit,
    ExpectedReturns,
    ExposureRange,
    Lifting,
    LinearLimit,
    LinearRow,
    LinearVariable,
    MaximumReturn,
    MeanVariance,
    MinimumCVaR,
    MinimumVariance,
    RiskParity,
    ScenarioSet,
    SolverSettings,
    WeightBounds,
    construct,
    weight_ranges,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError
from tests.unit.portfolio_optimizer.lp_reference import Row, exact_vertex_optimum

ASSETS = ("A", "B", "C")
COVARIANCE_ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
FULL = ExposureRange.exactly(1.0)

#: Eight scenarios of dyadic returns, so every sum below is exact in binary.
ROWS = (
    {"A": 0.0625, "B": -0.125, "C": 0.03125},
    {"A": -0.09375, "B": 0.0625, "C": 0.015625},
    {"A": 0.03125, "B": 0.09375, "C": -0.0625},
    {"A": -0.03125, "B": -0.046875, "C": 0.078125},
    {"A": 0.015625, "B": 0.03125, "C": -0.109375},
    {"A": -0.0625, "B": 0.0, "C": 0.046875},
    {"A": 0.078125, "B": -0.015625, "C": -0.03125},
    {"A": 0.0, "B": 0.046875, "C": 0.0625},
)
SCENARIOS = ScenarioSet.of(ROWS, currency="USD", period="1M", source="dyadic unit scenarios")
REVERSED = ScenarioSet.of(ROWS[::-1], currency="USD", period="1M", source="dyadic unit scenarios")


def covariance() -> CovarianceMatrix:
    return CovarianceMatrix.from_rows(
        ASSETS, COVARIANCE_ROWS, currency="USD", period="1M", source="unit", observations=None
    )


def returns(values: Mapping[str, float]) -> ExpectedReturns:
    return ExpectedReturns(dict(values), "USD", "1M", "seeded")


def problem(objective: object, constraints: ConstraintSet) -> ConstructionProblem:
    return ConstructionProblem(covariance(), objective, constraints, SETTINGS)  # type: ignore[arg-type]


def capped(cap: float, *limits: LinearLimit) -> ConstraintSet:
    return ConstraintSet(FULL, WeightBounds(0.0, cap), limits=limits)


# --------------------------------------------------------------------------- #
# The scenario set
# --------------------------------------------------------------------------- #


def test_a_scenario_set_is_sorted_by_asset_and_kept_in_its_order() -> None:
    direct = ScenarioSet(("C", "A", "B"), ((3.0, 1.0, 2.0), (6.0, 4.0, 5.0)), "USD", "1M", "unit")
    assert direct.assets == ("A", "B", "C")
    assert direct.returns == ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
    assert direct == ScenarioSet.of(
        [{"A": 1.0, "B": 2.0, "C": 3.0}, {"A": 4.0, "B": 5.0, "C": 6.0}],
        currency="USD",
        period="1M",
        source="unit",
    )
    assert direct.count == 2


def test_its_identity_is_its_values_in_their_order() -> None:
    again = ScenarioSet.of(ROWS, currency="USD", period="1M", source="dyadic unit scenarios")
    reversed_ = ScenarioSet.of(
        ROWS[::-1], currency="USD", period="1M", source="dyadic unit scenarios"
    )
    elsewhere = ScenarioSet.of(ROWS, currency="USD", period="1M", source="another source")
    moved = ScenarioSet.of(
        [{**ROWS[0], "A": 0.5}, *ROWS[1:]],
        currency="USD",
        period="1M",
        source="dyadic unit scenarios",
    )
    assert SCENARIOS.scenarios_id == again.scenarios_id
    assert (
        len(
            {
                SCENARIOS.scenarios_id,
                reversed_.scenarios_id,
                elsewhere.scenarios_id,
                moved.scenarios_id,
            }
        )
        == 4
    )


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: ScenarioSet((), ((),), "USD", "1M", "x"), "over no asset"),
        (lambda: ScenarioSet(("A", "A"), ((1.0, 2.0),), "USD", "1M", "x"), "an asset twice"),
        (lambda: ScenarioSet(("A",), (), "USD", "1M", "x"), "with no scenario"),
        (lambda: ScenarioSet(("A", "B"), ((1.0,),), "USD", "1M", "x"), "holds 1 returns"),
        (lambda: ScenarioSet(("A",), ((float("nan"),),), "USD", "1M", "x"), "must be finite"),
        (lambda: ScenarioSet(("A",), ((1.0,),), " ", "1M", "x"), "non-blank"),
        (
            lambda: ScenarioSet.of(
                [{"A": 1.0}, {"B": 1.0}], currency="USD", period="1M", source="x"
            ),
            "every scenario holds a return for every asset",
        ),
    ],
    ids=["no-asset", "twice", "no-scenario", "short", "nan", "blank", "mismatched"],
)
def test_a_malformed_scenario_set_is_refused(build: Callable[[], object], reason: str) -> None:
    with pytest.raises(ConstructionInputError, match=reason):
        build()


def exact_tail_mean(values: Sequence[float], level: float) -> Fraction:
    """``min over z of z + sum max(0, v - z) / ((1 - level) n)``, over every ``v`` as ``z``.

    The function is convex and piecewise linear, with its minimum at one of the
    values, so trying each is exact. The share is the float ``(1 - level) * n``
    the definition and the lifting both use.
    """

    share = Fraction((1.0 - level) * len(values))
    exact = [Fraction(value) for value in values]
    return min(
        z + sum((max(v - z, Fraction(0)) for v in exact), Fraction(0)) / share for z in exact
    )


EQUAL = {"A": 0.25, "B": 0.5, "C": 0.25}


@pytest.mark.parametrize("level", [0.0, 0.5, 0.75, 0.8, 0.875, 0.95])
def test_cvar_is_the_least_of_rockafellar_and_uryasevs_function(level: float) -> None:
    losses = SCENARIOS.losses(EQUAL)
    assert losses[0] == -(0.25 * 0.0625 + 0.5 * -0.125 + 0.25 * 0.03125)
    expected = exact_tail_mean(losses, level)
    assert abs(Fraction(SCENARIOS.cvar(EQUAL, level)) - expected) <= Fraction(1, 10**15)


def test_cvar_at_a_level_beyond_the_last_scenario_is_the_largest_loss() -> None:
    assert SCENARIOS.cvar(EQUAL, 0.95) == max(SCENARIOS.losses(EQUAL))
    assert SCENARIOS.cvar(EQUAL, 0.0) == pytest.approx(sum(SCENARIOS.losses(EQUAL)) / 8, abs=1e-18)


def test_drawdown_is_read_along_the_path_from_zero() -> None:
    # c: 0.125, -0.125, -0.0625, -0.1875, 0.1875; peaks 0.125 four times, then
    # 0.1875; drawdowns 0, 0.25, 0.1875, 0.3125, 0.
    path = ScenarioSet(
        ("A",), ((0.125,), (-0.25,), (0.0625,), (-0.125,), (0.375,)), "USD", "1M", "hand"
    )
    weights = {"A": 1.0}
    assert path.cdar(weights, 1.0) == 0.3125
    assert path.cdar(weights, 0.6) == (0.3125 + 0.25) / 2
    assert path.cdar(weights, 0.0) == pytest.approx((0.25 + 0.1875 + 0.3125) / 5, abs=1e-18)
    # A path that starts by falling draws down from the start, c_0 = 0.
    falling = ScenarioSet(("A",), ((-0.25,), (0.125,)), "USD", "1M", "hand")
    assert falling.cdar(weights, 1.0) == 0.25


def test_evaluation_refuses_weights_that_miss_the_assets() -> None:
    with pytest.raises(ConstructionInputError, match=r"missing \['C'\]"):
        SCENARIOS.cvar({"A": 0.5, "B": 0.5}, 0.5)
    with pytest.raises(ConstructionInputError, match=r"must lie in \[0, 1\)"):
        SCENARIOS.cvar(EQUAL, 1.0)


# --------------------------------------------------------------------------- #
# Exact references, stated without any lifting
# --------------------------------------------------------------------------- #


def _cumulative() -> list[list[float]]:
    running = [0.0] * len(ASSETS)
    path = []
    for row in ROWS:
        running = [total + row[asset] for total, asset in zip(running, ASSETS, strict=True)]
        path.append(running)
    return path


def worst_set_rows(worst: int, limit: float) -> list[Row]:
    """CVaR at ``worst`` whole scenarios ``<= limit``: every set's mean loss within it."""

    rows: list[Row] = []
    for chosen in itertools.combinations(ROWS, worst):
        # -sum_T r_s' w <= worst * limit, as sum_T r_s' w >= -worst * limit.
        rows.append(([sum(row[a] for row in chosen) for a in ASSETS], -worst * limit, False))
    return rows


def pairwise_drawdown_rows(limit: float) -> list[Row]:
    """The maximum drawdown ``<= limit``: ``c_tau - c_t <= limit`` for every pair, ``c_0 = 0``."""

    path = [[0.0] * len(ASSETS), *_cumulative()]
    rows: list[Row] = []
    for later in range(1, len(path)):
        for earlier in range(later):
            # (R_t - R_tau)' w >= -limit.
            difference = [path[later][j] - path[earlier][j] for j in range(len(ASSETS))]
            rows.append((difference, -limit, False))
    return rows


def exact_maximum_return(mu: Sequence[float], cap: float, rows: Sequence[Row]) -> Fraction | None:
    found = exact_vertex_optimum(
        [-m for m in mu],
        [([1.0, 1.0, 1.0], 1.0, True), *rows],
        [0.0, 0.0, 0.0],
        [cap, cap, cap],
    )
    return None if found is None else -found[0]


def exact_minimum_cvar(worst: int, cap: float) -> Fraction:
    rows: list[Row] = [([1.0, 1.0, 1.0, 0.0], 1.0, True)]
    for chosen in itertools.combinations(ROWS, worst):
        rows.append(([*(sum(row[a] for row in chosen) for a in ASSETS), float(worst)], 0.0, False))
    found = exact_vertex_optimum(
        [0.0, 0.0, 0.0, 1.0], rows, [0.0, 0.0, 0.0, -1.0], [cap, cap, cap, 1.0]
    )
    assert found is not None
    return found[0]


# --------------------------------------------------------------------------- #
# Minimum CVaR
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("level", "worst"), [(0.75, 2), (0.875, 1)])
@pytest.mark.parametrize("cap", [1.0, 0.45])
def test_minimum_cvar_is_the_exact_minimum(level: float, worst: int, cap: float) -> None:
    result = construct(problem(MinimumCVaR(SCENARIOS, level), capped(cap)))
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    weights = result.require_weights()
    assert SCENARIOS.cvar(weights, level) == pytest.approx(
        float(exact_minimum_cvar(worst, cap)), abs=1e-9
    )
    assert result.diagnostics.method == "MinimumCVaR"


GRID = [
    {"A": a / 20, "B": b / 20, "C": (20 - a - b) / 20} for a in range(21) for b in range(21 - a)
]


def test_minimum_cvar_at_a_fractional_share_beats_every_portfolio_of_a_grid() -> None:
    # (1 - 0.8) * 8 = 1.6 scenarios: the worst in full and the next at 0.6.
    result = construct(problem(MinimumCVaR(SCENARIOS, 0.8), capped(1.0)))
    found = SCENARIOS.cvar(result.require_weights(), 0.8)
    assert all(found <= SCENARIOS.cvar(weights, 0.8) + 1e-12 for weights in GRID)


# --------------------------------------------------------------------------- #
# Limits under maximum return
# --------------------------------------------------------------------------- #


def _seeded(seed: int) -> tuple[list[float], float, float]:
    rng = random.Random(seed)
    mu = [rng.randint(-2, 6) / 100 for _ in ASSETS]
    return mu, rng.choice([1.0, 0.6, 0.45]), rng.choice([0.005, 0.0125, 0.025, 0.05, 0.1])


@pytest.mark.parametrize("seed", range(16))
def test_a_cvar_limit_agrees_with_every_set_of_worst_scenarios(seed: int) -> None:
    mu, cap, limit = _seeded(seed)
    bound = CVaRLimit(SCENARIOS, 0.75, limit)
    result = construct(
        problem(MaximumReturn(returns(dict(zip(ASSETS, mu, strict=True)))), capped(cap, bound))
    )
    expected = exact_maximum_return(mu, cap, worst_set_rows(2, limit))
    if expected is None:
        assert result.status is ConstructionStatus.INFEASIBLE
        assert bound.label in result.diagnostics.conflict
        return
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    weights = result.require_weights()
    value = sum(weights[a] * m for a, m in zip(ASSETS, mu, strict=True))
    assert value == pytest.approx(float(expected), abs=1e-9)
    measured = SCENARIOS.cvar(weights, 0.75)
    assert measured <= limit + SETTINGS.feasibility_tolerance
    assert result.diagnostics.limit_values == {bound.label: measured}


@pytest.mark.parametrize("seed", range(16))
def test_a_maximum_drawdown_limit_agrees_with_every_pair_of_steps(seed: int) -> None:
    mu, cap, limit = _seeded(seed)
    bound = CDaRLimit(SCENARIOS, 1.0, limit)
    result = construct(
        problem(MaximumReturn(returns(dict(zip(ASSETS, mu, strict=True)))), capped(cap, bound))
    )
    expected = exact_maximum_return(mu, cap, pairwise_drawdown_rows(limit))
    if expected is None:
        assert result.status is ConstructionStatus.INFEASIBLE
        assert bound.label in result.diagnostics.conflict
        return
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    weights = result.require_weights()
    value = sum(weights[a] * m for a, m in zip(ASSETS, mu, strict=True))
    assert value == pytest.approx(float(expected), abs=1e-9)
    assert SCENARIOS.cdar(weights, 1.0) <= limit + SETTINGS.feasibility_tolerance


def test_the_seeds_reach_both_outcomes_for_each_limit() -> None:
    cvar = {
        exact_maximum_return(m, c, worst_set_rows(2, lim)) is None
        for m, c, lim in map(_seeded, range(16))
    }
    drawdown = {
        exact_maximum_return(m, c, pairwise_drawdown_rows(lim)) is None
        for m, c, lim in map(_seeded, range(16))
    }
    assert cvar == {True, False} and drawdown == {True, False}


@pytest.mark.parametrize("level", [0.0, 0.5, 0.75])
def test_a_conditional_drawdown_limit_holds_and_no_grid_portfolio_does_better(level: float) -> None:
    mu = [0.01, 0.03, 0.02]
    # The least conditional drawdown at these levels is 0.010 to 0.022; 0.025 binds or not.
    bound = CDaRLimit(SCENARIOS, level, 0.025)
    result = construct(
        problem(MaximumReturn(returns(dict(zip(ASSETS, mu, strict=True)))), capped(1.0, bound))
    )
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    weights = result.require_weights()
    assert SCENARIOS.cdar(weights, level) <= 0.025 + SETTINGS.feasibility_tolerance
    found = sum(weights[a] * m for a, m in zip(ASSETS, mu, strict=True))
    for candidate in GRID:
        if SCENARIOS.cdar(candidate, level) <= 0.025:
            assert sum(candidate[a] * m for a, m in zip(ASSETS, mu, strict=True)) <= found + 1e-12


def test_limits_combine_and_what_binds_is_named() -> None:
    mu = returns({"A": 0.01, "B": 0.02, "C": 0.015})
    cvar = CVaRLimit(SCENARIOS, 0.75, 0.02)
    drawdown = CDaRLimit(SCENARIOS, 1.0, 0.5)  # generous: it does not bind
    result = construct(problem(MaximumReturn(mu), capped(1.0, cvar, drawdown)))
    assert result.status is ConstructionStatus.OPTIMAL
    diagnostics = result.diagnostics
    assert cvar.label in diagnostics.binding and drawdown.label not in diagnostics.binding
    assert set(diagnostics.limit_values) == {cvar.label, drawdown.label}
    assert diagnostics.limit_values[cvar.label] == pytest.approx(0.02, abs=1e-12)
    assert diagnostics.limit_values[drawdown.label] < 0.5


def test_minimum_cvar_takes_a_drawdown_limit() -> None:
    bound = CDaRLimit(SCENARIOS, 1.0, 0.03)
    result = construct(problem(MinimumCVaR(SCENARIOS, 0.75), capped(1.0, bound)))
    assert result.status is ConstructionStatus.OPTIMAL
    weights = result.require_weights()
    assert SCENARIOS.cdar(weights, 1.0) <= 0.03 + SETTINGS.feasibility_tolerance
    free = construct(problem(MinimumCVaR(SCENARIOS, 0.75), capped(1.0))).require_weights()
    assert SCENARIOS.cvar(weights, 0.75) >= SCENARIOS.cvar(free, 0.75) - 1e-12


# --------------------------------------------------------------------------- #
# A researcher's limit, checked at its own value
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WorstLoss:
    """No scenario's loss above ``limit``: ``r_s' w >= -limit`` for every scenario."""

    scenarios: ScenarioSet
    limit: float
    overstate: float = 0.0  # added to the value: a limit whose value disagrees with its rows

    @property
    def convexity(self) -> Convexity:
        return Convexity.LINEAR

    @property
    def label(self) -> str:
        return f"worst loss <= {self.limit!r}"

    def rendering(self) -> list[str]:
        return [f"scenarios={self.scenarios.scenarios_id}", f"limit={self.limit!r}"]

    def lifting(self, problem: ConstructionProblem) -> Lifting:
        rows = tuple(
            LinearRow(
                f"loss[{s}]",
                dict(zip(self.scenarios.assets, row, strict=True)),
                {},
                -self.limit,
                None,
            )
            for s, row in enumerate(self.scenarios.returns)
        )
        return Lifting((), rows)

    def value(self, problem: ConstructionProblem, weights: Mapping[str, float]) -> float:
        return max(self.scenarios.losses(weights)) + self.overstate


def test_a_researchers_limit_agrees_with_its_exact_program() -> None:
    mu = [0.01, 0.03, 0.02]
    limit = WorstLoss(SCENARIOS, 0.03)
    assert isinstance(limit, LinearLimit)
    result = construct(
        problem(MaximumReturn(returns(dict(zip(ASSETS, mu, strict=True)))), capped(1.0, limit))
    )
    rows: list[Row] = [([row[a] for a in ASSETS], -0.03, False) for row in ROWS]
    expected = exact_maximum_return(mu, 1.0, rows)
    assert expected is not None
    weights = result.require_weights()
    assert sum(weights[a] * m for a, m in zip(ASSETS, mu, strict=True)) == pytest.approx(
        float(expected), abs=1e-9
    )
    assert limit.label in result.diagnostics.binding


def test_a_limit_whose_value_disagrees_with_its_rows_is_caught() -> None:
    overstated = WorstLoss(SCENARIOS, 0.03, overstate=0.01)
    mu = returns({"A": 0.01, "B": 0.03, "C": 0.02})
    result = construct(problem(MaximumReturn(mu), capped(1.0, overstated)))
    assert result.status is ConstructionStatus.NUMERICAL_FAILURE and result.weights is None
    assert "misses a constraint" in result.diagnostics.detail


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

#: A maximum-return problem under a CVaR and a drawdown limit, as this tree identifies it.
PINNED_LIMITED = "7700c259f5f6eaf30e7dba991b74e7d09339f75a6a2bfde374ce2f6ff9a8f4da"


def test_limits_enter_the_identity_sorted_and_only_when_present() -> None:
    mu = returns({"A": 0.01, "B": 0.02, "C": 0.015})
    cvar = CVaRLimit(SCENARIOS, 0.75, 0.02)
    drawdown = CDaRLimit(SCENARIOS, 1.0, 0.05)
    first = problem(MaximumReturn(mu), capped(1.0, cvar, drawdown)).problem_id
    assert first == problem(MaximumReturn(mu), capped(1.0, drawdown, cvar)).problem_id
    assert first != problem(MaximumReturn(mu), capped(1.0, cvar)).problem_id
    assert (
        first
        != problem(
            MaximumReturn(mu), capped(1.0, CVaRLimit(SCENARIOS, 0.8, 0.02), drawdown)
        ).problem_id
    )
    plain = ConstraintSet(FULL, WeightBounds(0.0, 1.0))
    assert capped(1.0).rendering() == plain.rendering()
    assert not any(line.startswith("limit") for line in plain.rendering())
    assert first == PINNED_LIMITED


def test_a_researchers_limit_is_identified_by_its_class_and_kind() -> None:
    lines = capped(1.0, WorstLoss(SCENARIOS, 0.03)).rendering()
    assert any(line.endswith(".WorstLoss") and line.startswith("limit.class=") for line in lines)
    assert "limit.convexity=linear" in lines


# --------------------------------------------------------------------------- #
# Ranges
# --------------------------------------------------------------------------- #


def test_weight_ranges_are_what_the_constraints_on_each_weight_imply() -> None:
    bounds = ConstraintSet(
        FULL, WeightBounds(0.0, 0.6).with_asset("B", 0.1, 0.4), max_abs_weight=0.5
    )
    assert dict(weight_ranges(problem(MinimumVariance(), bounds))) == {
        "A": (0.0, 0.5),
        "B": (0.1, 0.4),
        "C": (0.0, 0.5),
    }
    gross = ConstraintSet(FULL, WeightBounds.unbounded(), max_gross_exposure=1.5)
    assert set(weight_ranges(problem(MinimumVariance(), gross)).values()) == {(-1.5, 1.5)}
    crossed = ConstraintSet(
        FULL, WeightBounds(0.0, 1.0).with_asset("A", 0.7, 0.7), max_abs_weight=0.5
    )
    assert weight_ranges(problem(MinimumVariance(), crossed))["A"] == (0.7, 0.5)
    with pytest.raises(
        ConstructionInputError, match=r"leave \['A', 'B', 'C'\] without a finite range"
    ):
        weight_ranges(problem(MinimumVariance(), ConstraintSet(FULL, WeightBounds.unbounded())))


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

LIMIT = CVaRLimit(SCENARIOS, 0.75, 0.02)


@pytest.mark.parametrize(
    "objective",
    [
        MinimumVariance(),
        MeanVariance(returns({"A": 0.01, "B": 0.02, "C": 0.015}), 2.0),
        RiskParity.equal(),
    ],
    ids=["minimum-variance", "mean-variance", "risk-parity"],
)
def test_a_limit_under_an_objective_that_is_not_linear_is_refused(objective: object) -> None:
    with pytest.raises(ConstructionInputError, match="only a linear objective takes one"):
        construct(problem(objective, capped(1.0, LIMIT)))


@dataclass(frozen=True, slots=True)
class Declared(WorstLoss):
    kind: Convexity = Convexity.CONVEX

    @property
    def convexity(self) -> Convexity:
        return self.kind


@dataclass(frozen=True, slots=True)
class Unlifted(WorstLoss):
    def lifting(self, problem: ConstructionProblem) -> Lifting:
        return "rows"  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: capped(1.0, "a limit"), "is not a limit"),  # type: ignore[arg-type]
        (lambda: capped(1.0, Declared(SCENARIOS, 0.03)), "taken only when it is LINEAR"),
        (lambda: capped(1.0, LIMIT, CVaRLimit(SCENARIOS, 0.75, 0.02)), "listed twice"),
        (
            # Two scenario sets, so two renderings, under one label.
            lambda: capped(1.0, WorstLoss(SCENARIOS, 0.03), WorstLoss(REVERSED, 0.03)),
            "share a label",
        ),
        (
            lambda: Lifting(
                (LinearVariable("x", 0.0, 1.0, 1.0),), (LinearRow("r", {}, {"x": 1.0}, 0.0, None),)
            ),
            "priced",
        ),
        (lambda: Lifting(), "no row limits nothing"),
        (lambda: MinimumCVaR(SCENARIOS, 1.0), r"must lie in \[0, 1\)"),
        (lambda: CVaRLimit(SCENARIOS, -0.1, 0.02), r"must lie in \[0, 1\)"),
        (lambda: CDaRLimit(SCENARIOS, 1.5, 0.02), r"must lie in \[0, 1\]"),
        (lambda: CDaRLimit(SCENARIOS, 1.0, -0.01), "never negative"),
        (lambda: MinimumCVaR("scenarios", 0.5), "must be a ScenarioSet"),  # type: ignore[arg-type]
    ],
    ids=[
        "not-a-limit",
        "not-linear",
        "twice",
        "label",
        "priced",
        "no-row",
        "objective-level",
        "limit-level",
        "drawdown-level",
        "negative-drawdown",
        "not-scenarios",
    ],
)
def test_a_malformed_limit_is_refused(build: Callable[[], object], reason: str) -> None:
    with pytest.raises(ConstructionInputError, match=reason):
        build()


def test_a_lifting_that_is_not_one_is_refused() -> None:
    mu = returns({"A": 0.01, "B": 0.02, "C": 0.015})
    with pytest.raises(ConstructionInputError, match="not a Lifting"):
        construct(problem(MaximumReturn(mu), capped(1.0, Unlifted(SCENARIOS, 0.03))))


@pytest.mark.parametrize(
    ("scenarios", "reason"),
    [
        (ScenarioSet.of(ROWS, currency="EUR", period="1M", source="x"), "refused as a pair"),
        (
            ScenarioSet.of([{"A": 0.01, "B": 0.02}], currency="USD", period="1M", source="x"),
            r"missing \['C'\]",
        ),
    ],
    ids=["units", "coverage"],
)
def test_scenarios_in_other_units_or_over_another_universe_are_refused(
    scenarios: ScenarioSet, reason: str
) -> None:
    with pytest.raises(ConstructionInputError, match=reason):
        construct(problem(MinimumCVaR(scenarios, 0.5), capped(1.0)))
