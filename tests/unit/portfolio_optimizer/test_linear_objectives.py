"""Linear objectives through :func:`construct`: the certified linear program (ADR-0063).

* **Maximum return** is held, over seeded problems, to the exact vertex optimum
  of the same problem stated independently here: an absolute-sum limit as its
  polytope -- ``s' (w - c) <= L`` for every sign vector ``s`` -- rather than
  construction's split, and trading costs one orthant of the book at a time.
  The seeds reach optima and infeasibility under caps and a sector bound, a
  gross limit long-short, a turnover limit and costs.
* **A researcher's CVaR objective** (Rockafellar and Uryasev, 2000), lifted
  through the protocol's :class:`LinearForm`, is held to the exact minimum of a
  different program: the largest mean loss over every set of ``k`` worst
  scenarios.
* **Diagnostics** name what binds and report the return and the costs paid.
* **Infeasibility is proven and named**: crossed ranges before any solve, and
  rows and ranges by the Farkas certificate.
* **Identity**: the linear method is named, a structure is never written out
  for it, and an identity is pinned.
* **Refusals**: an unbounded weight, a volatility cap, a form that is not one or
  does not cover the universe, and malformed forms.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction

import pytest

from alphalab.analytics.risk_model import Classification, CovarianceMatrix, FactorStructure
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    Convexity,
    ExpectedReturns,
    ExposureRange,
    GroupBound,
    LinearCosts,
    LinearForm,
    LinearObjective,
    LinearRow,
    LinearVariable,
    MaximumReturn,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
    construct,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError
from tests.unit.portfolio_optimizer import test_construction_factor_path as factor_path
from tests.unit.portfolio_optimizer.lp_reference import Row, exact_vertex_optimum

ASSETS = ("A", "B", "C")
ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
MU = ExpectedReturns({"A": 0.01, "B": 0.02, "C": 0.015}, "USD", "1M", "unit forecast")
FULL = ExposureRange.exactly(1.0)
SECTORS = Classification("sector", {"A": "Tech", "B": "Tech", "C": "Energy"}, "unit", None)


def covariance() -> CovarianceMatrix:
    return CovarianceMatrix.from_rows(
        ASSETS, ROWS, currency="USD", period="1M", source="unit", observations=None
    )


def returns(values: Mapping[str, float], period: str = "1M") -> ExpectedReturns:
    return ExpectedReturns(dict(values), "USD", period, "seeded")


def problem(objective: object, constraints: ConstraintSet) -> ConstructionProblem:
    return ConstructionProblem(covariance(), objective, constraints, SETTINGS)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The independent statement of each problem, for the exact reference
# --------------------------------------------------------------------------- #

SIGNS = tuple(itertools.product((1.0, -1.0), repeat=len(ASSETS)))
KINDS = ("caps", "gross", "turnover", "costs")


@dataclass(frozen=True, slots=True)
class Stated:
    """One seeded problem, in the terms the reference reads."""

    kind: str  # "caps", "gross", "turnover" or "costs"
    mu: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    net: tuple[float, float]
    group: float | None  # the Tech bucket's upper bound
    absolute: tuple[tuple[float, ...], float] | None  # (centers, limit)
    costs: tuple[tuple[float, ...], tuple[float, ...]] | None  # (current, rates)


def _rows(stated: Stated) -> list[Row]:
    """Every constraint as ``normal . w (= | >=) bound``, an absolute sum as its polytope."""

    low, high = stated.net
    rows: list[Row] = (
        [([1.0, 1.0, 1.0], low, True)]
        if low == high
        else [([1.0, 1.0, 1.0], low, False), ([-1.0, -1.0, -1.0], -high, False)]
    )
    if stated.group is not None:
        rows.append(([-1.0, -1.0, 0.0], -stated.group, False))
    if stated.absolute is not None:
        centers, limit = stated.absolute
        for signs in SIGNS:
            shift = sum(Fraction(s) * Fraction(c) for s, c in zip(signs, centers, strict=True))
            rows.append(([-s for s in signs], float(-Fraction(limit) - shift), False))
    return rows


def exact_value(stated: Stated) -> Fraction | None:
    """The greatest ``mu' w - sum rates |w - current|``, exactly, or ``None`` if infeasible."""

    if stated.costs is None:
        found = exact_vertex_optimum(
            [-m for m in stated.mu], _rows(stated), stated.lower, stated.upper
        )
        return None if found is None else -found[0]
    current, rates = stated.costs
    best: Fraction | None = None
    for signs in SIGNS:
        # Within one orthant of the book the cost is linear: sign * (w - current) >= 0.
        rows = _rows(stated)
        rows.extend(
            ([s if j == i else 0.0 for j in range(len(ASSETS))], s * current[i], False)
            for i, s in enumerate(signs)
        )
        costs = [-m + s * r for m, s, r in zip(stated.mu, signs, rates, strict=True)]
        found = exact_vertex_optimum(costs, rows, stated.lower, stated.upper)
        if found is not None:
            constant = sum(
                Fraction(s) * Fraction(r) * Fraction(c)
                for s, r, c in zip(signs, rates, current, strict=True)
            )
            value = -(found[0] - constant)
            best = value if best is None or value > best else best
    return best


def _seeded(seed: int) -> Stated:
    rng = random.Random(seed)
    kind = KINDS[seed % len(KINDS)]
    mu = tuple(rng.randint(-3, 5) / 100 for _ in ASSETS)
    if kind == "gross":  # long-short; the range each weight has is the limit's
        limit = rng.choice([1.2, 1.5, 2.0])
        return Stated(
            kind, mu, (-limit,) * 3, (limit,) * 3, (1.0, 1.0), None, ((0.0,) * 3, limit), None
        )
    upper = tuple(rng.choice([0.2, 0.3, 0.5, 0.7, 1.0]) for _ in ASSETS)
    net = (1.0, 1.0) if rng.random() < 0.7 else (0.5, 1.0)
    group = rng.choice([None, 0.25, 0.5, 0.8])
    current = tuple(float(Fraction(rng.randint(0, 4), 8)) for _ in ASSETS)
    floor = (0.0,) * 3
    if kind == "turnover":
        limit = rng.choice([0.1, 0.25, 0.5])
        return Stated(kind, mu, floor, upper, net, group, (current, limit), None)
    if kind == "costs":
        rates = tuple(rng.choice([0.0, 0.001, 0.004, 0.01]) for _ in ASSETS)
        return Stated(kind, mu, floor, upper, net, group, None, (current, rates))
    return Stated(kind, mu, floor, upper, net, group, None, None)


def _constraints(stated: Stated) -> ConstraintSet:
    # A gross limit's problem states no weight bound: the limit is each weight's range.
    bounds = WeightBounds.unbounded()
    if stated.kind != "gross":
        for asset, low, high in zip(ASSETS, stated.lower, stated.upper, strict=True):
            bounds = bounds.with_asset(asset, low, high)
    low, high = stated.net
    absolute = stated.absolute
    return ConstraintSet(
        net_exposure=ExposureRange.exactly(low)
        if low == high
        else ExposureRange.between(low, high),
        bounds=bounds,
        max_gross_exposure=absolute[1] if stated.kind == "gross" and absolute else None,
        groups=() if stated.group is None else (GroupBound(SECTORS, "Tech", None, stated.group),),
        turnover=(
            TurnoverLimit(dict(zip(ASSETS, absolute[0], strict=True)), absolute[1])
            if stated.kind == "turnover" and absolute
            else None
        ),
    )


def _objective(stated: Stated) -> MaximumReturn:
    costs = (
        None
        if stated.costs is None
        else LinearCosts(
            dict(zip(ASSETS, stated.costs[0], strict=True)),
            dict(zip(ASSETS, stated.costs[1], strict=True)),
        )
    )
    return MaximumReturn(returns(dict(zip(ASSETS, stated.mu, strict=True))), costs)


@pytest.mark.parametrize("seed", range(40))
def test_maximum_return_agrees_with_the_exact_optimum(seed: int) -> None:
    stated = _seeded(seed)
    result = construct(problem(_objective(stated), _constraints(stated)))
    expected = exact_value(stated)
    if expected is None:
        assert result.status is ConstructionStatus.INFEASIBLE
        assert result.weights is None and result.diagnostics.conflict
        return
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    weights = result.require_weights()
    paid = result.diagnostics.transaction_cost or 0.0
    value = sum(weights[a] * m for a, m in zip(ASSETS, stated.mu, strict=True)) - paid
    assert value == pytest.approx(float(expected), abs=1e-9)
    assert result.diagnostics.max_violation <= SETTINGS.feasibility_tolerance


def test_the_seeds_reach_every_family_and_both_outcomes() -> None:
    outcomes = {(stated.kind, exact_value(stated) is None) for stated in map(_seeded, range(40))}
    assert {(kind, False) for kind in KINDS} <= outcomes
    assert any(infeasible for _, infeasible in outcomes)


# --------------------------------------------------------------------------- #
# A researcher's CVaR, through the protocol
# --------------------------------------------------------------------------- #

#: Eight scenarios of dyadic returns, so every sum below is exact in binary.
SCENARIOS = (
    {"A": 0.0625, "B": -0.125, "C": 0.03125},
    {"A": -0.09375, "B": 0.0625, "C": 0.015625},
    {"A": 0.03125, "B": 0.09375, "C": -0.0625},
    {"A": -0.03125, "B": -0.046875, "C": 0.078125},
    {"A": 0.015625, "B": 0.03125, "C": -0.109375},
    {"A": -0.0625, "B": 0.0, "C": 0.046875},
    {"A": 0.078125, "B": -0.015625, "C": -0.03125},
    {"A": 0.0, "B": 0.046875, "C": 0.0625},
)


@dataclass(frozen=True, slots=True)
class ScenarioCVaR:
    """Minimize the CVaR of the loss at ``level`` over stated scenarios (Rockafellar-Uryasev).

    ``alpha + sum_s u_s / ((1 - level) S)`` with ``u_s >= -r_s' w - alpha`` and
    ``u_s >= 0``. ``reach`` bounds every loss over the weights' range, so the
    lifting's variables have the finite ranges the objective states.
    """

    scenarios: tuple[Mapping[str, float], ...]
    level: float
    reach: float

    @property
    def convexity(self) -> Convexity:
        return Convexity.LINEAR

    def rendering(self) -> list[str]:
        return [
            f"level={self.level!r}",
            f"reach={self.reach!r}",
            *(
                f"scenario[{s}][{a}]={v!r}"
                for s, scenario in enumerate(self.scenarios)
                for a, v in sorted(scenario.items())
            ),
        ]

    def linear_form(self, problem: ConstructionProblem) -> LinearForm:
        share = 1.0 / ((1.0 - self.level) * len(self.scenarios))
        variables = (
            LinearVariable("alpha", -self.reach, self.reach, 1.0),
            *(
                LinearVariable(f"u[{s}]", 0.0, 2.0 * self.reach, share)
                for s in range(len(self.scenarios))
            ),
        )
        rows = tuple(
            LinearRow(f"excess[{s}]", scenario, {"alpha": 1.0, f"u[{s}]": 1.0}, 0.0, None)
            for s, scenario in enumerate(self.scenarios)
        )
        return LinearForm(dict.fromkeys(problem.universe, 0.0), variables, rows)


def _worst_mean(weights: Mapping[str, float], worst: int) -> float:
    losses = sorted(
        (-sum(weights[a] * r for a, r in scenario.items()) for scenario in SCENARIOS),
        reverse=True,
    )
    return sum(losses[:worst]) / worst


def _exact_cvar(worst: int, upper: float) -> Fraction:
    """``min t`` with ``worst * t >= sum of the losses of every ``worst`` scenarios``, exactly."""

    rows: list[Row] = [([1.0, 1.0, 1.0, 0.0], 1.0, True)]
    for chosen in itertools.combinations(SCENARIOS, worst):
        normal = [sum(scenario[a] for scenario in chosen) for a in ASSETS]
        rows.append(([*normal, float(worst)], 0.0, False))
    found = exact_vertex_optimum(
        [0.0, 0.0, 0.0, 1.0], rows, [0.0, 0.0, 0.0, -1.0], [upper, upper, upper, 1.0]
    )
    assert found is not None
    return found[0]


@pytest.mark.parametrize(("level", "worst"), [(0.75, 2), (0.875, 1)])
@pytest.mark.parametrize("cap", [1.0, 0.5])
def test_a_researchers_cvar_is_the_exact_minimum_of_the_worst_scenarios(
    level: float, worst: int, cap: float
) -> None:
    objective = ScenarioCVaR(SCENARIOS, level, 1.0)
    assert isinstance(objective, LinearObjective)
    result = construct(problem(objective, ConstraintSet(FULL, WeightBounds(0.0, cap))))
    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    weights = result.require_weights()
    exact = _exact_cvar(worst, cap)
    assert _worst_mean(weights, worst) == pytest.approx(float(exact), abs=1e-9)
    assert result.diagnostics.method == "ScenarioCVaR"


def test_its_identity_names_its_class_kind_and_method() -> None:
    constraints = ConstraintSet(FULL, WeightBounds(0.0, 1.0))
    first = problem(ScenarioCVaR(SCENARIOS, 0.75, 1.0), constraints).problem_id
    assert first == problem(ScenarioCVaR(SCENARIOS, 0.75, 1.0), constraints).problem_id
    assert first != problem(ScenarioCVaR(SCENARIOS, 0.875, 1.0), constraints).problem_id


# --------------------------------------------------------------------------- #
# Diagnostics, infeasibility, identity
# --------------------------------------------------------------------------- #


def test_the_answer_is_an_exact_vertex_with_what_binds_named() -> None:
    result = construct(problem(MaximumReturn(MU), ConstraintSet(FULL, WeightBounds(0.0, 0.5))))
    assert result.status is ConstructionStatus.OPTIMAL
    assert dict(result.require_weights()) == {"A": 0.0, "B": 0.5, "C": 0.5}
    diagnostics = result.diagnostics
    assert diagnostics.binding == (
        "net exposure = 1.0",
        "weight[A] >= 0.0",
        "weight[B] <= 0.5",
        "weight[C] <= 0.5",
    )
    assert diagnostics.expected_return == 0.0175 and diagnostics.stationarity == 0.0
    assert diagnostics.variance is not None and diagnostics.variance > 0.0


def test_costs_leave_a_trade_that_does_not_pay_undone() -> None:
    book = {"A": 0.5, "B": 0.0, "C": 0.5}
    cheap = LinearCosts(book, {"A": 0.001, "B": 0.001, "C": 0.003})
    dear = LinearCosts(book, {"A": 0.01, "B": 0.01, "C": 0.01})
    constraints = ConstraintSet(FULL, WeightBounds(0.0, 1.0))
    moved = construct(problem(MaximumReturn(MU, cheap), constraints))
    # Selling A for B gains 0.01 for 0.002 of cost, C for B 0.005 for 0.004: both pay.
    assert dict(moved.require_weights()) == {"A": 0.0, "B": 1.0, "C": 0.0}
    assert moved.diagnostics.transaction_cost == pytest.approx(0.003, abs=1e-15)
    kept = construct(problem(MaximumReturn(MU, dear), constraints))
    assert dict(kept.require_weights()) == book and kept.diagnostics.transaction_cost == 0.0


def test_crossed_ranges_are_infeasible_before_any_solve() -> None:
    constraints = ConstraintSet(
        FULL, WeightBounds(0.0, 1.0).with_asset("A", 0.6, 0.6), max_abs_weight=0.5
    )
    result = construct(problem(MaximumReturn(MU), constraints))
    assert result.status is ConstructionStatus.INFEASIBLE
    assert result.diagnostics.conflict == ("weight[A] = 0.6", "concentration[A] <= 0.5")
    assert result.diagnostics.iterations == 0


def test_a_farkas_certificate_names_its_rows_and_the_ranges_it_reaches() -> None:
    result = construct(problem(MaximumReturn(MU), ConstraintSet(FULL, WeightBounds(0.0, 0.3))))
    assert result.status is ConstructionStatus.INFEASIBLE and result.weights is None
    assert result.diagnostics.conflict == (
        "net exposure = 1.0",
        "weight[A] <= 0.3",
        "weight[B] <= 0.3",
        "weight[C] <= 0.3",
    )


def test_absolute_sums_are_lifted_exactly() -> None:
    gross = construct(
        problem(
            MaximumReturn(MU),
            ConstraintSet(FULL, WeightBounds.unbounded(), max_gross_exposure=1.6),
        )
    )
    weights = gross.require_weights()
    assert weights["A"] == pytest.approx(-0.3, abs=1e-12) and weights["C"] == 0.0
    assert gross.diagnostics.binding == ("net exposure = 1.0", "gross exposure <= 1.6")
    book = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    turned = construct(
        problem(
            MaximumReturn(MU),
            ConstraintSet(FULL, WeightBounds(0.0, 1.0), turnover=TurnoverLimit(book, 0.2)),
        )
    )
    assert turned.diagnostics.turnover == pytest.approx(0.2, abs=1e-12)
    assert "turnover <= 0.2" in turned.diagnostics.binding


#: The identity of the maximum-return problem above, as this tree computes it.
PINNED_MAXIMUM_RETURN = "6919c797b32bcaa1ebdeb09eddc6ca1e5ec2570502dfb7736b989e788574591e"


def test_the_identity_is_pinned_and_costs_enter_it() -> None:
    constraints = ConstraintSet(FULL, WeightBounds(0.0, 0.5))
    assert problem(MaximumReturn(MU), constraints).problem_id == PINNED_MAXIMUM_RETURN
    costed = MaximumReturn(MU, LinearCosts(dict.fromkeys(ASSETS, 0.0), dict.fromkeys(ASSETS, 0.0)))
    assert problem(costed, constraints).problem_id != PINNED_MAXIMUM_RETURN


def test_a_structure_is_never_written_out_for_a_linear_objective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structured, _, _ = factor_path._model(120)
    structure = structured.factors
    assert structure is not None
    mu = returns(
        {asset: 0.0001 * (index % 5) for index, asset in enumerate(structured.assets)}, "1D"
    )
    constraints = ConstraintSet(FULL, WeightBounds(0.0, 0.05))
    over_matrix = construct(
        ConstructionProblem(structured, MaximumReturn(mu), constraints, factor_path.SETTINGS)
    )

    def refuse(self: FactorStructure) -> object:
        raise AssertionError("the structure was written out")

    monkeypatch.setattr(FactorStructure, "_write_out", refuse)
    over_structure = construct(
        ConstructionProblem(structure, MaximumReturn(mu), constraints, factor_path.SETTINGS)
    )
    # The linear program reads no curvature, so the two statements of one
    # covariance give the same weights; their identities are the statements'.
    assert over_structure.status is ConstructionStatus.OPTIMAL
    assert over_structure.weights == over_matrix.weights
    assert over_structure.problem_id != over_matrix.problem_id


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_a_weight_without_a_finite_range_is_refused() -> None:
    with pytest.raises(ConstructionInputError, match=r"leave \['B'\] without a finite range"):
        construct(
            problem(
                MaximumReturn(MU),
                ConstraintSet(
                    FULL,
                    WeightBounds(0.0, 1.0).with_asset("B", 0.0, None),
                ),
            )
        )


def test_a_volatility_cap_under_a_linear_objective_is_refused() -> None:
    capped = ConstraintSet(FULL, WeightBounds(0.0, 1.0), max_volatility=0.3)
    for objective in (MaximumReturn(MU), ScenarioCVaR(SCENARIOS, 0.75, 1.0)):
        with pytest.raises(ConstructionInputError, match="volatility cap"):
            construct(problem(objective, capped))


@dataclass(frozen=True, slots=True)
class Stating:
    """A linear objective whose form is whatever it is given."""

    form: object

    @property
    def convexity(self) -> Convexity:
        return Convexity.LINEAR

    def rendering(self) -> list[str]:
        return [f"form={self.form!r}"]

    def linear_form(self, problem: ConstructionProblem) -> LinearForm:
        return self.form  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("form", "reason"),
    [
        ("not a form", "not a LinearForm"),
        (LinearForm({"A": 1.0, "B": 1.0}), "must cost exactly the universe"),
        (
            LinearForm(
                dict.fromkeys(ASSETS, 0.0),
                (LinearVariable("x", 0.0, 1.0, 1.0),),
                (LinearRow("r", {"Z": 1.0}, {"x": 1.0}, 0.0, None),),
            ),
            r"weights assets outside the universe: \['Z'\]",
        ),
    ],
    ids=["not-a-form", "partial", "outside"],
)
def test_a_form_that_is_not_one_or_misses_the_universe_is_refused(
    form: object, reason: str
) -> None:
    with pytest.raises(ConstructionInputError, match=reason):
        construct(problem(Stating(form), ConstraintSet(FULL, WeightBounds(0.0, 1.0))))


def test_costs_that_miss_the_universe_are_refused() -> None:
    costs = LinearCosts({"A": 0.5, "B": 0.5}, {"A": 0.001, "B": 0.001})
    with pytest.raises(ConstructionInputError, match="cover exactly the universe"):
        construct(problem(MaximumReturn(MU, costs), ConstraintSet(FULL, WeightBounds(0.0, 1.0))))


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: LinearVariable("x", 1.0, 0.0, 0.0), "the range is empty"),
        (lambda: LinearVariable("x", 0.0, float("inf"), 0.0), "must be finite"),
        (lambda: LinearRow("r", {"A": 0.0}, {}, 0.0, None), "no non-zero coefficient"),
        (lambda: LinearRow("r", {"A": 1.0}, {}, None, None), "bounds neither side"),
        (
            lambda: LinearForm(
                {}, (LinearVariable("x", 0.0, 1.0, 0.0), LinearVariable("x", 0.0, 1.0, 0.0))
            ),
            "same variable twice",
        ),
        (
            lambda: LinearForm(
                {},
                (),
                (
                    LinearRow("r", {"A": 1.0}, {}, 0.0, None),
                    LinearRow("r", {"B": 1.0}, {}, 0.0, None),
                ),
            ),
            "two rows alike",
        ),
        (
            lambda: LinearForm({}, (), (LinearRow("r", {}, {"y": 1.0}, 0.0, None),)),
            r"does not introduce: \['y'\]",
        ),
        (lambda: MaximumReturn("mu"), "must be ExpectedReturns"),  # type: ignore[arg-type]
    ],
    ids=[
        "empty-range",
        "infinite",
        "zero-row",
        "no-side",
        "twice",
        "rows-alike",
        "unknown",
        "returns",
    ],
)
def test_a_malformed_form_is_refused(build: Callable[[], object], reason: str) -> None:
    with pytest.raises(ConstructionInputError, match=reason):
        build()


def test_a_rows_zero_coefficients_are_left_out() -> None:
    row = LinearRow("r", {"B": 0.0, "A": 2.0}, {"x": 0.0}, 0.0, 1.0)
    assert dict(row.weights) == {"A": 2.0} and dict(row.variables) == {}
