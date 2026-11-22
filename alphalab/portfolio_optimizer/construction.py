"""Portfolio construction: a stated objective, stated constraints, a certified answer.

ADR-0005 made :mod:`alphalab.portfolio_optimizer` the construction authority --
*what should I own* -- beside the accounting engine's *what do I own*. Until
v3.8 it answered with four closed forms (:mod:`~alphalab.portfolio_optimizer.optimizer`)
and a clip-and-redistribute rule applied afterwards
(:func:`~alphalab.portfolio_optimizer.constraints.apply_weight_constraints`). This
module generalizes it: every method is an optimization **over** its
constraints, solved by one numerical method, and every answer carries the
evidence that it is one.

One entry point
---------------

:func:`construct` takes a :class:`ConstructionProblem` -- a covariance, an
objective, a :class:`ConstraintSet` and :class:`SolverSettings` -- and returns a
:class:`ConstructionResult`. The objectives:

============================ ==============================================================
:class:`MinimumVariance`     minimize ``w' C w``
:class:`MeanVariance`        maximize ``mu' w - (lambda / 2) w' C w``
:class:`MaximumDiversification` maximize ``sum_i w_i sigma_i / sqrt(w' C w)``, long only
:class:`RiskParity`          the long-only portfolio whose risk shares are stated budgets
:class:`RobustMeanVariance`  maximize the *worst case* of the mean-variance objective over
                             a stated uncertainty set for ``mu``
:class:`MaximumReturn`       maximize ``mu' w``, by the certified linear program (v4.3)
============================ ==============================================================

Black-Litterman is a *model* that produces expected returns, not an objective:
:func:`alphalab.portfolio_optimizer.black_litterman.black_litterman` computes a
posterior and :class:`MeanVariance` constructs from it. A factor-neutral
portfolio is any objective with :class:`FactorBound` constraints.

Since v3.11 (ledger OFE-002) a mean-variance objective may charge
:class:`LinearCosts` for trading away from the current book, solved exactly
(each orthant around the book is a quadratic program; a subgradient
certificate says when the right one has been found), and the answer can be
turned into tradable quantities by
:func:`~alphalab.portfolio_optimizer.lots.round_to_lots`. The covariance can be
shrunk (:meth:`~alphalab.analytics.risk_model.CovarianceMatrix.ledoit_wolf`),
exponentially weighted (:meth:`~alphalab.analytics.risk_model.CovarianceMatrix.ewma`)
or implied by a factor model
(:meth:`~alphalab.analytics.risk_model.CovarianceMatrix.factor_model`).

Since v3.12 (ledger PRF-005) a large universe is solved through its risk
model's structure. A covariance built by
:meth:`~alphalab.analytics.risk_model.CovarianceMatrix.factor_model` carries
its :class:`~alphalab.analytics.risk_model.FactorStructure`; over
:data:`FACTOR_STRUCTURED_MINIMUM_ASSETS` or more assets every quadratic
program of the construction is then solved by
:mod:`~alphalab.portfolio_optimizer.factor_quadratic` in ``O(n k^2)`` per step
rather than ``O(n^2)`` -- 0.18 s against 142 s for 800 assets, measured -- and
its positive definiteness is established from the structure. Any program that
method does not certify goes to the dense method, which decides it and proves
infeasibility; the problem's identity records which method applies.

Since v3.13 (ledger PRF-013) the covariance can be the structure itself: a
:class:`~alphalab.analytics.risk_model.FactorStructure` built by
:meth:`~alphalab.analytics.risk_model.FactorStructure.of` in ``O(n k^2)``.
Writing ``n^2`` values out had bounded the public path -- 29 s and 1.3 GB at
4,000 assets before the solver was reached -- and a problem over a structure
never writes them out unless a method needs the dense values: risk parity, a
universe under :data:`FACTOR_STRUCTURED_MINIMUM_ASSETS`, a structure that
cannot establish definiteness by itself, or a program the structured method
does not certify. Its diagnostics are computed through the factors.

Since v4.3 (ADR-0063) the objectives are open. An :class:`Objective` states
its :class:`Convexity` and its rendering, and :func:`construct` sends it to the
method its class admits, or refuses it before anything is solved. The
package's objectives declare theirs, and the five before v4.3 keep their
methods and identities.
A researcher's ``CONVEX_QUADRATIC`` objective states a :class:`QuadraticForm`, a
positive multiple of the problem's covariance and a linear term, and is solved
by the certified quadratic methods. A ``LINEAR`` objective states a
:class:`LinearForm` -- a cost on each weight, and any variables and rows its
lifting adds, as CVaR's does -- and is solved by the certified linear program
(:mod:`~alphalab.portfolio_optimizer.linear`), which every weight's range the
constraints imply keeps bounded. A ``NONCONVEX`` objective is always refused,
since no certified method minimizes one.

Since v4.3 (ADR-0063 section 4) construction takes scenario risk. A
:class:`ScenarioSet` -- supplied, never generated -- holds every asset's return
in each scenario. :class:`MinimumCVaR` minimizes the CVaR of the loss over it
(Rockafellar and Uryasev, 2000); :class:`CVaRLimit` bounds it, and
:class:`CDaRLimit` the conditional drawdown of the cumulative sum of
returns (Chekhlov, Uryasev and Zabarankin, 2005), each linear once lifted. A
limit is a :class:`LinearLimit` of the protocol, as a researcher's may be, in
:attr:`ConstraintSet.limits`, and is taken under a linear objective only.
Construction checks an answer against each limit's own value, not only the
lifting the method solved, and :func:`weight_ranges` gives the ranges a
lifting's variables are stated from.

What construction does not solve, by design: **cardinality** (at most ``k``
names) and **joint lot selection** are integer programs, and no integer
solver is part of this library -- rounding is per asset, toward zero, and
reported; a **quadratic objective under a scenario limit**, which neither
method certifies; and **multi-period** construction (trading a path of books
against a cost of getting there), a sequence of single-period problems the
caller composes. Each is an explicit boundary, not a limitation discovered
later.

Units, stated once
------------------

* A **weight** is a fraction of the capital the covariance's returns are
  measured against, in :attr:`~alphalab.analytics.risk_model.CovarianceMatrix.currency`.
  ``0.25`` is a quarter of capital; a negative weight is a short. Leverage is
  gross exposure in these units: ``sum |w_i|``.
* **Volatility, variance and expected return** are per the covariance's
  :attr:`~alphalab.analytics.risk_model.CovarianceMatrix.period`. Expected
  returns in another currency or period are refused, not rescaled.
* **Money** enters only through :class:`NotionalLimits`, as ``Decimal`` in the
  covariance's currency, converted once to a weight bound through an explicit
  28-digit context.

No silent fallback
------------------

* **Inputs** that are missing, non-finite, mis-sized, in another currency or
  period, or that an objective cannot express raise
  :class:`~alphalab.portfolio_optimizer.exceptions.ConstructionInputError`.
* **A covariance that is not positive definite is refused.** Every objective
  here has a unique solution when it is, and may have infinitely many when it is
  not; rather than pick one by an undocumented rule, the caller removes the
  redundancy or derives an explicit, recorded regularization
  (:meth:`~alphalab.analytics.risk_model.CovarianceMatrix.with_ridge`).
* **Constraints that cannot all hold** give ``INFEASIBLE``, naming the
  constraints that conflict. There is no unconstrained answer behind it and no
  constraint is relaxed.
* A result that is not ``OPTIMAL`` carries **no weights**.

Reproducibility
---------------

A problem's identity (:attr:`ConstructionProblem.problem_id`) is derived from
every input that decides the answer -- the covariance's identity, the
objective and its parameters, every constraint and the solver settings -- and
not from the order constraints were listed in. A result's identity covers the
problem, the status, the weights and the conflict. Nothing reads a clock, a
random source or the environment, and the arithmetic is IEEE-754 operations
and square roots only, so the same problem gives the same bits anywhere.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import Enum, auto
from functools import partial
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import (
    Classification,
    CovarianceMatrix,
    FactorLoadings,
    FactorStructure,
    RiskContributions,
    euler_decomposition,
    portfolio_factor_exposures,
)
from alphalab.common.arithmetic import canonical_text
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError, OptimizationError
from alphalab.portfolio_optimizer.factor_quadratic import (
    FactorCurvature,
    FactorQuadraticProgram,
    solve_factor_quadratic_program,
)
from alphalab.portfolio_optimizer.linear import (
    LinearProgram,
    LinearSolution,
    solve_linear_program,
)
from alphalab.portfolio_optimizer.quadratic import (
    AbsoluteSumLimit,
    LinearConstraint,
    QuadraticProgram,
    QuadraticSolution,
    SolveStatus,
    constraint_residuals,
    solve_quadratic_program,
)
from alphalab.portfolio_optimizer.risk_parity import solve_risk_budgets

__all__ = [
    "CONSTRUCTION_PROBLEM_SCHEME",
    "CONSTRUCTION_RESULT_SCHEME",
    "EXPECTED_RETURNS_SCHEME",
    "FACTOR_STRUCTURED_MINIMUM_ASSETS",
    "SCENARIO_SET_SCHEME",
    "BoxUncertainty",
    "CDaRLimit",
    "CVaRLimit",
    "ConstraintSet",
    "ConstructionDiagnostics",
    "ConstructionObjective",
    "ConstructionProblem",
    "ConstructionResult",
    "ConstructionStatus",
    "Convexity",
    "EllipsoidalUncertainty",
    "ExpectedReturns",
    "ExposureRange",
    "FactorBound",
    "GroupBound",
    "Lifting",
    "LinearCosts",
    "LinearForm",
    "LinearLimit",
    "LinearObjective",
    "LinearRow",
    "LinearVariable",
    "MaximumDiversification",
    "MaximumReturn",
    "MeanVariance",
    "MinimumCVaR",
    "MinimumVariance",
    "NotionalLimits",
    "Objective",
    "QuadraticForm",
    "QuadraticObjective",
    "RiskParity",
    "RobustMeanVariance",
    "ScenarioSet",
    "SolverSettings",
    "TurnoverLimit",
    "WeightBounds",
    "construct",
    "weight_ranges",
]

#: Version 2 (v3.11, ledger DET-006): money and risk budgets -- the ``Decimal``
#: inputs -- are rendered by value, so ``100`` and ``100.00`` state one problem.
CONSTRUCTION_PROBLEM_SCHEME: Final = "alphalab.construction_problem.v2"
CONSTRUCTION_RESULT_SCHEME: Final = "alphalab.construction_result.v1"
EXPECTED_RETURNS_SCHEME: Final = "alphalab.expected_returns.v1"
#: v4.3 (ADR-0063): a scenario set's identity.
SCENARIO_SET_SCHEME: Final = "alphalab.scenario_set.v1"

#: The smallest universe the factor-structured method solves (v3.12, ledger
#: PRF-005). The structured method is the faster one from about fifty assets
#: (measured: 0.01 s against 0.02 s at 50, 0.01 s against 0.13 s at 100); below
#: this the dense method takes about a tenth of a second, and keeping it there
#: keeps every smaller problem's identity and result bit for bit as v3.11 had
#: them. See :mod:`~alphalab.portfolio_optimizer.factor_quadratic`.
FACTOR_STRUCTURED_MINIMUM_ASSETS: Final = 100

#: The arithmetic a money limit becomes a weight bound under: 28 significant
#: digits, half-even, never the caller's thread context (the rule
#: ``alphalab.alt_data.fundamentals`` follows).
_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConstructionInputError(f"{what} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise ConstructionInputError(f"{what} is {number!r}; every input must be finite.")
    return number


def _optional(value: object, what: str) -> float | None:
    return None if value is None else _number(value, what)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConstructionInputError(f"{what} must be a non-blank, unpadded string, got {value!r}.")
    return value


def _range(lower: float | None, upper: float | None, what: str) -> None:
    if lower is not None and upper is not None and lower > upper:
        raise ConstructionInputError(
            f"{what} has lower {lower!r} above upper {upper!r}; the range is empty. That is a "
            "malformed constraint, not an infeasible portfolio."
        )


def _render(value: object) -> str:
    return repr(value)


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Settings and inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SolverSettings:
    """The numerical contract a construction is solved under. No field has a default.

    Attributes:
        feasibility_tolerance: How far a constraint may be missed, in its own
            units (weight, exposure, volatility), for a solution to count as
            satisfying it. In ``(0, 1)``.
        convergence_tolerance: What "converged" means for the method: the
            relative KKT stationarity residual of a quadratic program, the
            largest risk-budget deviation of risk parity, the relative change of
            the robustness weight, the relative gap to a volatility cap, the
            relative gap between a linear program's objective and its proven
            bound. In ``(0, 1)``.
        max_iterations: The step budget: add-or-drop steps of the quadratic
            solver, sweeps of risk parity, outer iterations of the robust and
            volatility-cap searches, interior-point iterations of the linear
            program. Exhausting it is ``ITERATION_LIMIT``, never a best-effort
            answer.
    """

    feasibility_tolerance: float
    convergence_tolerance: float
    max_iterations: int

    def __post_init__(self) -> None:
        for name in ("feasibility_tolerance", "convergence_tolerance"):
            value = _number(getattr(self, name), f"SolverSettings.{name}")
            if not 0.0 < value < 1.0:
                raise ConstructionInputError(
                    f"SolverSettings.{name} is {value!r}; it must lie in (0, 1)."
                )
            object.__setattr__(self, name, value)
        if (
            isinstance(self.max_iterations, bool)
            or not isinstance(self.max_iterations, int)
            or self.max_iterations < 1
        ):
            raise ConstructionInputError(
                f"SolverSettings.max_iterations is {self.max_iterations!r}; it must be a positive "
                "integer."
            )


@dataclass(frozen=True, slots=True)
class ExpectedReturns:
    """Expected return per asset, and what they are a claim about.

    AlphaLab estimates no expected return. These are the caller's -- a forecast,
    a model's output, or a Black-Litterman posterior -- and carry the currency
    and period they are in so a mean-variance objective cannot combine them with
    a covariance of another unit.

    Attributes:
        values: Asset -> expected return per :attr:`period`, sorted.
        currency: The currency the returns are measured in.
        period: The period each return spans.
        source: Where they came from, in words.
    """

    values: Mapping[str, float]
    currency: str
    period: str
    source: str

    def __post_init__(self) -> None:
        if not self.values:
            raise ConstructionInputError("Expected returns over no asset describe nothing.")
        ordered = {
            _text(asset, "expected-return asset"): _number(value, f"expected return of {asset!r}")
            for asset, value in sorted(self.values.items())
        }
        object.__setattr__(self, "values", MappingProxyType(ordered))
        _text(self.currency, "ExpectedReturns.currency")
        _text(self.period, "ExpectedReturns.period")
        _text(self.source, "ExpectedReturns.source")

    @property
    def returns_id(self) -> str:
        """The derived identity of these expected returns."""

        return _digest(
            [
                EXPECTED_RETURNS_SCHEME,
                f"currency={_render(self.currency)}",
                f"period={_render(self.period)}",
                f"source={_render(self.source)}",
                *(f"{_render(asset)}={_render(value)}" for asset, value in self.values.items()),
            ]
        )


def _level(value: object, what: str, *, inclusive: bool) -> float:
    """A confidence level in ``[0, 1)``, or ``[0, 1]`` when ``inclusive``."""

    level = _number(value, what)
    if not 0.0 <= level <= 1.0 or (level == 1.0 and not inclusive):
        bound = "[0, 1]" if inclusive else "[0, 1)"
        raise ConstructionInputError(f"{what} is {level!r}; it must lie in {bound}.")
    return level


def _tail_mean(values: Sequence[float], level: float) -> float:
    """The mean of the worst ``1 - level`` share of ``values``, fractions weighted.

    The least value over ``z`` of ``z + sum_i max(0, v_i - z) / ((1 - level) n)``
    (Rockafellar and Uryasev, 2000), in closed form: the largest
    ``floor((1 - level) n)`` values in full and the next by the remaining
    fraction, over ``(1 - level) n``.
    """

    share = (1.0 - level) * len(values)
    ordered = sorted(values, reverse=True)
    whole = min(math.floor(share), len(ordered))
    part = share - whole
    tail = ordered[:whole] + (
        [part * ordered[whole]] if part > 0.0 and whole < len(ordered) else []
    )
    return math.fsum(tail) / share


def _cumulative(rows: Sequence[Sequence[float]]) -> list[tuple[float, ...]]:
    """Each step's cumulative return per asset: the running sum of the steps so far."""

    running = [0.0] * (len(rows[0]) if rows else 0)
    path: list[tuple[float, ...]] = []
    for row in rows:
        running = [total + value for total, value in zip(running, row, strict=True)]
        path.append(tuple(running))
    return path


@dataclass(frozen=True, slots=True)
class ScenarioSet:
    """Scenarios of every asset's return, supplied and never generated (v4.3, ADR-0063).

    Historical, simulated or stressed, the scenarios are the researcher's. Each
    holds a return for every asset, per :attr:`period` in :attr:`currency`, as
    expected returns do, so they enter a construction only beside a covariance
    in the same units. They keep the order given: a drawdown reads them as a
    path, one step each; CVaR reads them as a sample, and has the same value in
    any order, though the identity does not.

    Attributes:
        assets: The assets, sorted; each scenario's returns follow this order.
        returns: One tuple per scenario, every asset's return in it: finite.
        currency: The currency the returns are measured in.
        period: The period each return spans.
        source: Where the scenarios came from, in words.
    """

    assets: tuple[str, ...]
    returns: tuple[tuple[float, ...], ...]
    currency: str
    period: str
    source: str
    _identity: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        assets = tuple(_text(asset, "scenario asset") for asset in self.assets)
        if not assets:
            raise ConstructionInputError("A scenario set over no asset describes nothing.")
        if len(set(assets)) != len(assets):
            raise ConstructionInputError("A scenario set names an asset twice.")
        rows = tuple(self.returns)
        if not rows:
            raise ConstructionInputError("A scenario set with no scenario describes nothing.")
        order = sorted(range(len(assets)), key=assets.__getitem__)
        canonical: list[tuple[float, ...]] = []
        for number, row in enumerate(rows):
            values = tuple(row)
            if len(values) != len(assets):
                raise ConstructionInputError(
                    f"Scenario {number} holds {len(values)} returns for {len(assets)} assets."
                )
            canonical.append(
                tuple(
                    _number(values[j], f"scenario {number}'s return of {assets[j]!r}")
                    for j in order
                )
            )
        sorted_assets = tuple(assets[j] for j in order)
        _text(self.currency, "ScenarioSet.currency")
        _text(self.period, "ScenarioSet.period")
        _text(self.source, "ScenarioSet.source")
        object.__setattr__(self, "assets", sorted_assets)
        object.__setattr__(self, "returns", tuple(canonical))
        object.__setattr__(
            self,
            "_identity",
            _digest(
                [
                    SCENARIO_SET_SCHEME,
                    f"currency={_render(self.currency)}",
                    f"period={_render(self.period)}",
                    f"source={_render(self.source)}",
                    "assets=" + ",".join(_render(asset) for asset in sorted_assets),
                    *(",".join(_render(value) for value in row) for row in canonical),
                ]
            ),
        )

    @classmethod
    def of(
        cls,
        scenarios: Sequence[Mapping[str, float]],
        *,
        currency: str,
        period: str,
        source: str,
    ) -> ScenarioSet:
        """From one mapping per scenario, each holding a return for the same assets."""

        rows = list(scenarios)
        if not rows:
            raise ConstructionInputError("A scenario set with no scenario describes nothing.")
        assets = tuple(sorted(rows[0]))
        for number, row in enumerate(rows):
            if set(row) != set(assets):
                raise ConstructionInputError(
                    f"Scenario {number} names {sorted(row)}, not the first scenario's "
                    f"{list(assets)}: every scenario holds a return for every asset."
                )
        return cls(
            assets,
            tuple(tuple(row[asset] for asset in assets) for row in rows),
            currency,
            period,
            source,
        )

    @property
    def scenarios_id(self) -> str:
        """The derived identity of these scenarios, in their order."""

        return self._identity

    @property
    def count(self) -> int:
        """How many scenarios there are."""

        return len(self.returns)

    def _weights(self, weights: Mapping[str, float]) -> list[float]:
        if set(weights) != set(self.assets):
            raise ConstructionInputError(
                "Weights for a scenario set must cover exactly its assets: missing "
                f"{sorted(set(self.assets) - set(weights))}, outside "
                f"{sorted(set(weights) - set(self.assets))}."
            )
        return [_number(weights[asset], f"the weight of {asset!r}") for asset in self.assets]

    def losses(self, weights: Mapping[str, float]) -> tuple[float, ...]:
        """Each scenario's loss, ``-r_s' w``, for weights over exactly these assets."""

        vector = self._weights(weights)
        return tuple(
            -math.fsum(r * w for r, w in zip(row, vector, strict=True)) for row in self.returns
        )

    def cvar(self, weights: Mapping[str, float], level: float) -> float:
        """The CVaR of the loss at ``level`` in ``[0, 1)``: the worst ``1 - level`` share's mean.

        Rockafellar and Uryasev's (2000) definition over these scenarios, each
        weighted equally. Zero is the mean loss; a level above ``1 - 1 / S`` is
        the largest.
        """

        return _tail_mean(self.losses(weights), _level(level, "CVaR level", inclusive=False))

    def cdar(self, weights: Mapping[str, float], level: float) -> float:
        """The conditional drawdown at ``level`` of the cumulative sum of returns.

        Chekhlov, Uryasev and Zabarankin (2005), over these scenarios read as a
        path: ``c_t`` the sum of the portfolio's returns to step ``t``, ``c_0 =
        0``, and the drawdown ``d_t = max(c_0, ..., c_t) - c_t``. At ``level``
        in ``[0, 1)`` it is the worst ``1 - level`` share's mean; at one, the
        maximum drawdown. Summed, not compounded.
        """

        level = _level(level, "CDaR level", inclusive=True)
        vector = self._weights(weights)
        peak = 0.0
        drawdowns: list[float] = []
        for cumulative in _cumulative(self.returns):
            value = math.fsum(r * w for r, w in zip(cumulative, vector, strict=True))
            peak = max(peak, value)
            drawdowns.append(peak - value)
        return max(drawdowns) if level == 1.0 else _tail_mean(drawdowns, level)


# --------------------------------------------------------------------------- #
# Constraints
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExposureRange:
    """A closed interval, either end of which may be open (``None``).

    Attributes:
        lower: The smallest value allowed, or ``None`` for no lower limit.
        upper: The largest value allowed, or ``None`` for no upper limit.
    """

    lower: float | None
    upper: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lower", _optional(self.lower, "ExposureRange.lower"))
        object.__setattr__(self, "upper", _optional(self.upper, "ExposureRange.upper"))
        _range(self.lower, self.upper, "ExposureRange")

    @classmethod
    def exactly(cls, value: float) -> ExposureRange:
        """``[value, value]``: an equality."""

        return cls(value, value)

    @classmethod
    def between(cls, lower: float, upper: float) -> ExposureRange:
        """``[lower, upper]``."""

        return cls(lower, upper)

    @property
    def is_exact(self) -> bool:
        """Whether this range is a single point."""

        return self.lower is not None and self.lower == self.upper

    def contains(self, value: float, tolerance: float) -> bool:
        """Whether ``value`` lies in the range, to within ``tolerance``."""

        return (self.lower is None or value >= self.lower - tolerance) and (
            self.upper is None or value <= self.upper + tolerance
        )


@dataclass(frozen=True, slots=True)
class WeightBounds:
    """Per-asset weight bounds: a default for every asset, and overrides.

    ``None`` on either side is *no bound* on that side, which is a statement the
    caller makes -- there is no default bound, because a long-only mandate and a
    130/30 one are different portfolios.

    Attributes:
        lower: The lower bound every asset has unless overridden, or ``None``.
        upper: The upper bound every asset has unless overridden, or ``None``.
        overrides: Asset -> ``(lower, upper)`` replacing the default for that
            asset. Each side may be ``None``.
    """

    lower: float | None
    upper: float | None
    overrides: Mapping[str, tuple[float | None, float | None]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "lower", _optional(self.lower, "WeightBounds.lower"))
        object.__setattr__(self, "upper", _optional(self.upper, "WeightBounds.upper"))
        _range(self.lower, self.upper, "WeightBounds")
        ordered: dict[str, tuple[float | None, float | None]] = {}
        for asset, (low, high) in sorted(self.overrides.items()):
            _text(asset, "bounded asset")
            pair = (
                _optional(low, f"lower bound of {asset!r}"),
                _optional(high, f"upper bound of {asset!r}"),
            )
            _range(pair[0], pair[1], f"the bounds of {asset!r}")
            ordered[asset] = pair
        object.__setattr__(self, "overrides", MappingProxyType(ordered))

    @classmethod
    def unbounded(cls) -> WeightBounds:
        """No weight bound on any asset: shorts and leverage allowed."""

        return cls(None, None)

    @classmethod
    def long_only(cls, upper: float | None) -> WeightBounds:
        """``0 <= w_i`` for every asset, with an optional common cap."""

        return cls(0.0, upper)

    @classmethod
    def uniform(cls, lower: float | None, upper: float | None) -> WeightBounds:
        """The same bounds for every asset."""

        return cls(lower, upper)

    def with_asset(self, asset: str, lower: float | None, upper: float | None) -> WeightBounds:
        """These bounds with ``asset`` overridden."""

        return WeightBounds(self.lower, self.upper, {**self.overrides, asset: (lower, upper)})

    def for_asset(self, asset: str) -> tuple[float | None, float | None]:
        """The bounds in force for ``asset``."""

        return self.overrides.get(asset, (self.lower, self.upper))


@dataclass(frozen=True, slots=True)
class GroupBound:
    """``lower <= sum of weights classified as label <= upper``.

    The sector, country and currency constraints -- and any other dimension a
    :class:`~alphalab.analytics.risk_model.Classification` names. The
    classification must cover the whole universe; an unclassified asset is
    refused rather than left out of every group.

    Attributes:
        classification: Who says which asset is in which bucket.
        label: The bucket constrained.
        lower: Its smallest total weight, or ``None``.
        upper: Its largest total weight, or ``None``.
    """

    classification: Classification
    label: str
    lower: float | None
    upper: float | None

    def __post_init__(self) -> None:
        _text(self.label, "GroupBound.label")
        object.__setattr__(self, "lower", _optional(self.lower, "GroupBound.lower"))
        object.__setattr__(self, "upper", _optional(self.upper, "GroupBound.upper"))
        if self.lower is None and self.upper is None:
            raise ConstructionInputError(
                f"The group bound on {self.label!r} states neither end, so it constrains nothing."
            )
        _range(
            self.lower, self.upper, f"the {self.classification.dimension} bound on {self.label!r}"
        )

    @property
    def name(self) -> str:
        """``dimension[label]``, as constraint labels read it."""

        return f"{self.classification.dimension}[{self.label}]"

    def rendering(self) -> str:
        return (
            f"group={self.classification.classification_id}:{_render(self.label)}:"
            f"{_render(self.lower)}:{_render(self.upper)}"
        )


@dataclass(frozen=True, slots=True)
class FactorBound:
    """``lower <= sum_i w_i * beta_i,factor <= upper``: a factor exposure constraint.

    A *factor-neutral* portfolio is ``lower == upper == 0``
    (:meth:`neutral`); a bounded exposure is a range; a target with a tolerance
    band is :meth:`target`. The exposure is the book's loading-weighted sum, the
    quantity :func:`~alphalab.analytics.risk_model.portfolio_factor_exposures`
    reports -- one definition, used by the constraint and by the diagnostic.

    Attributes:
        loadings: The factor model, with its source and lineage.
        factor: The factor constrained.
        lower: Smallest exposure allowed, or ``None``.
        upper: Largest exposure allowed, or ``None``.
    """

    loadings: FactorLoadings
    factor: str
    lower: float | None
    upper: float | None

    def __post_init__(self) -> None:
        try:
            self.loadings.require_factor(self.factor)
        except AnalyticsValidationError as error:
            raise ConstructionInputError(str(error)) from error
        object.__setattr__(self, "lower", _optional(self.lower, "FactorBound.lower"))
        object.__setattr__(self, "upper", _optional(self.upper, "FactorBound.upper"))
        if self.lower is None and self.upper is None:
            raise ConstructionInputError(
                f"The bound on factor {self.factor!r} states neither end, so it constrains nothing."
            )
        _range(self.lower, self.upper, f"the bound on factor {self.factor!r}")

    @classmethod
    def neutral(cls, loadings: FactorLoadings, factor: str) -> FactorBound:
        """Zero exposure to ``factor``."""

        return cls(loadings, factor, 0.0, 0.0)

    @classmethod
    def target(
        cls, loadings: FactorLoadings, factor: str, value: float, band: float
    ) -> FactorBound:
        """Exposure within ``band`` of ``value``."""

        width = _number(band, "FactorBound band")
        if width < 0.0:
            raise ConstructionInputError(f"A target band of {width!r} is negative.")
        center = _number(value, "FactorBound target")
        return cls(loadings, factor, center - width, center + width)

    @property
    def name(self) -> str:
        """``factor[<name>]``: how the bound is named in binding and conflict lists."""

        return f"factor[{self.factor}]"

    def rendering(self) -> str:
        return (
            f"factor={self.loadings.loadings_id}:{_render(self.factor)}:"
            f"{_render(self.lower)}:{_render(self.upper)}"
        )


@dataclass(frozen=True, slots=True)
class TurnoverLimit:
    """``sum_i |w_i - current_i| <= maximum``: two-way turnover from a stated book.

    Two-way means buys and sells both count: moving five percent from one asset
    to another is ten percent of turnover.

    Attributes:
        current: The weights held now, for **exactly** the construction
            universe (zeros included): a missing asset is refused rather than
            assumed unheld, and an asset outside the universe is refused
            because its forced sale is turnover this limit cannot see.
        maximum: The turnover allowed. Non-negative.
    """

    current: Mapping[str, float]
    maximum: float

    def __post_init__(self) -> None:
        ordered = {
            _text(asset, "turnover asset"): _number(value, f"current weight of {asset!r}")
            for asset, value in sorted(self.current.items())
        }
        object.__setattr__(self, "current", MappingProxyType(ordered))
        limit = _number(self.maximum, "TurnoverLimit.maximum")
        if limit < 0.0:
            raise ConstructionInputError(f"A turnover limit of {limit!r} is negative.")
        object.__setattr__(self, "maximum", limit)


@dataclass(frozen=True, slots=True)
class LinearCosts:
    """What trading away from a stated book costs, charged in the objective (OFE-002).

    ``sum_i rates_i * |w_i - current_i|`` is subtracted from the mean-variance
    objective -- or, since v4.3, the maximum-return one: each unit of weight
    traded in asset ``i`` costs ``rates_i`` of return, in the covariance's
    currency and period -- so a one-way cost of ten basis points of notional,
    expected to be paid once over a holding period of a year while returns are
    daily, is ``0.001 / 252``: amortizing it is the caller's statement, not an
    assumption made here. The optimum trades an asset only where the
    improvement pays its cost, and leaves it exactly where it is otherwise -- a
    no-trade region a penalty-free optimum does not have.

    Attributes:
        current: The weights held now, for **exactly** the construction
            universe (zeros included); a missing asset is refused, as for a
            :class:`TurnoverLimit`.
        rates: The cost of trading one unit of weight, per asset, for exactly
            the universe. Non-negative; zero trades freely.
    """

    current: Mapping[str, float]
    rates: Mapping[str, float]

    def __post_init__(self) -> None:
        current = {
            _text(asset, "cost asset"): _number(value, f"current weight of {asset!r}")
            for asset, value in sorted(self.current.items())
        }
        rates: dict[str, float] = {}
        for asset, value in sorted(self.rates.items()):
            rate = _number(value, f"cost rate of {asset!r}")
            if rate < 0.0:
                raise ConstructionInputError(
                    f"The cost rate of {asset!r} is {rate!r}; trading does not pay."
                )
            rates[_text(asset, "cost asset")] = rate
        if set(current) != set(rates):
            raise ConstructionInputError(
                "LinearCosts must give a current weight and a rate for the same assets: "
                f"weights only {sorted(set(current) - set(rates))}, rates only "
                f"{sorted(set(rates) - set(current))}."
            )
        object.__setattr__(self, "current", MappingProxyType(current))
        object.__setattr__(self, "rates", MappingProxyType(rates))

    def rendering(self) -> list[str]:
        return [
            *(f"costs.current[{_render(a)}]={_render(v)}" for a, v in self.current.items()),
            *(f"costs.rate[{_render(a)}]={_render(v)}" for a, v in self.rates.items()),
        ]

    def cost(self, weights: Mapping[str, float]) -> float:
        """``sum_i rates_i * |w_i - current_i|`` for ``weights``."""

        return math.fsum(
            rate * abs(weights[asset] - self.current[asset]) for asset, rate in self.rates.items()
        )


@dataclass(frozen=True, slots=True)
class NotionalLimits:
    """``|w_i| * capital <= maximum_i``: money caps per asset, as weight bounds.

    Attributes:
        capital: The capital weights are fractions of, in the covariance's
            currency. Positive.
        maximum_notional: Asset -> the largest absolute notional allowed in it,
            in the same currency. Non-negative. Assets not named are not capped.
    """

    capital: Decimal
    maximum_notional: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.capital, Decimal)
            or not self.capital.is_finite()
            or self.capital <= 0
        ):
            raise ConstructionInputError(
                f"NotionalLimits.capital is {self.capital!r}; it must be a positive, finite "
                "Decimal."
            )
        ordered: dict[str, Decimal] = {}
        for asset, amount in sorted(self.maximum_notional.items()):
            _text(asset, "notional-limited asset")
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
                raise ConstructionInputError(
                    f"The notional limit of {asset!r} is {amount!r}; it must be a non-negative, "
                    "finite Decimal."
                )
            ordered[asset] = amount
        if not ordered:
            raise ConstructionInputError("NotionalLimits names no asset, so it limits nothing.")
        object.__setattr__(self, "maximum_notional", MappingProxyType(ordered))

    def weight_limit(self, asset: str) -> float:
        """``maximum / capital`` for one asset, computed once in the explicit context."""

        return float(_CONTEXT.divide(self.maximum_notional[asset], self.capital))


@dataclass(frozen=True, slots=True)
class ConstraintSet:
    """Every constraint a construction is solved under. Explicit, inspectable, deterministic.

    Attributes:
        net_exposure: ``sum_i w_i`` -- the budget. :meth:`ExposureRange.exactly`
            (1.0) is fully invested; ``exactly(0.0)`` is dollar-neutral.
            Required.
        bounds: Per-asset weight bounds. Required; :meth:`WeightBounds.unbounded`
            says there are none.
        max_abs_weight: Concentration: ``|w_i| <= this`` for every asset, or
            ``None``.
        max_gross_exposure: Leverage: ``sum_i |w_i| <= this``, or ``None``.
        groups: Sector, country, currency and other classified-bucket bounds.
        factors: Factor exposure bounds.
        turnover: Two-way turnover from the current book, or ``None``.
        notional_limits: Per-asset money caps, or ``None``.
        max_volatility: A risk budget on the whole portfolio: ex-ante
            ``sqrt(w' C w) <= this``, per the covariance's period, or ``None``.
        limits: Limits of the protocol, each linear once lifted -- a CVaR or a
            drawdown limit over scenarios, or a researcher's -- taken under a
            linear objective only (v4.3, ADR-0063). In any order: they enter the
            identity sorted, and only when there is one.
    """

    net_exposure: ExposureRange
    bounds: WeightBounds
    max_abs_weight: float | None = None
    max_gross_exposure: float | None = None
    groups: tuple[GroupBound, ...] = ()
    factors: tuple[FactorBound, ...] = ()
    turnover: TurnoverLimit | None = None
    notional_limits: NotionalLimits | None = None
    max_volatility: float | None = None
    limits: tuple[LinearLimit, ...] = ()

    def __post_init__(self) -> None:
        for name in ("max_abs_weight", "max_gross_exposure", "max_volatility"):
            value = _optional(getattr(self, name), f"ConstraintSet.{name}")
            if value is not None and value < 0.0:
                raise ConstructionInputError(
                    f"ConstraintSet.{name} is {value!r}; it is a magnitude."
                )
            object.__setattr__(self, name, value)
        object.__setattr__(self, "groups", tuple(sorted(self.groups, key=GroupBound.rendering)))
        object.__setattr__(self, "factors", tuple(sorted(self.factors, key=FactorBound.rendering)))
        seen = [bound.rendering() for bound in self.groups] + [
            bound.rendering() for bound in self.factors
        ]
        if len(set(seen)) != len(seen):
            raise ConstructionInputError("The same group or factor bound is listed twice.")
        factor_sources: dict[str, str] = {}
        for bound in self.factors:
            owner = factor_sources.setdefault(bound.factor, bound.loadings.loadings_id)
            if owner != bound.loadings.loadings_id:
                raise ConstructionInputError(
                    f"Factor {bound.factor!r} is constrained under two different factor models. "
                    "A factor's identity is its model's; name them apart."
                )
        limits = tuple(self.limits)
        for limit in limits:
            if not isinstance(limit, LinearLimit):
                raise ConstructionInputError(
                    f"{limit!r} is not a limit: a limit states its convexity, label, bound, "
                    "rendering, lifting and value (ADR-0063)."
                )
            _text(limit.label, "a limit's label")
            if limit.convexity is not Convexity.LINEAR:
                raise ConstructionInputError(
                    f"The limit {limit.label!r} is {limit.convexity!r}: a limit is taken only when "
                    "it is LINEAR (ADR-0063)."
                )
            _number(limit.limit, f"the bound of the limit {limit.label!r}")
        keyed = sorted(
            ((tuple(_limit_lines(limit)), limit) for limit in limits), key=lambda pair: pair[0]
        )
        if len({key for key, _ in keyed}) != len(keyed):
            raise ConstructionInputError("The same limit is listed twice.")
        if len({limit.label for _, limit in keyed}) != len(keyed):
            raise ConstructionInputError(
                "Two limits share a label, so a result could not tell them apart."
            )
        object.__setattr__(self, "limits", tuple(limit for _, limit in keyed))

    def rendering(self) -> list[str]:
        """The canonical lines this constraint set enters an identity as."""

        lines = [
            f"net_exposure={_render(self.net_exposure.lower)}:{_render(self.net_exposure.upper)}",
            f"bounds={_render(self.bounds.lower)}:{_render(self.bounds.upper)}",
            *(
                f"bounds[{_render(asset)}]={_render(low)}:{_render(high)}"
                for asset, (low, high) in self.bounds.overrides.items()
            ),
            f"max_abs_weight={_render(self.max_abs_weight)}",
            f"max_gross_exposure={_render(self.max_gross_exposure)}",
            f"max_volatility={_render(self.max_volatility)}",
            *(bound.rendering() for bound in self.groups),
            *(bound.rendering() for bound in self.factors),
        ]
        if self.turnover is None:
            lines.append("turnover=None")
        else:
            lines.append(f"turnover={_render(self.turnover.maximum)}")
            lines.extend(
                f"turnover.current[{_render(asset)}]={_render(value)}"
                for asset, value in self.turnover.current.items()
            )
        if self.notional_limits is None:
            lines.append("notional=None")
        else:
            lines.append(f"notional.capital={canonical_text(self.notional_limits.capital)}")
            lines.extend(
                f"notional[{_render(asset)}]={canonical_text(amount)}"
                for asset, amount in self.notional_limits.maximum_notional.items()
            )
        # v4.3 (ADR-0063): only when there is one, so every other set's lines are as they were.
        for limit in self.limits:
            lines.extend(_limit_lines(limit))
        return lines


# --------------------------------------------------------------------------- #
# Objectives
# --------------------------------------------------------------------------- #


class Convexity(Enum):
    """What kind of function an objective is, which decides the method that solves it (v4.3).

    Construction stays one authority, and every method behind it is certified
    (ADR-0063). So an objective says what it is, and :func:`construct` sends it
    to the method its class admits, or refuses it before anything is solved.

    ``LINEAR``: a cost on each weight. ``CONVEX_QUADRATIC``: a positive multiple
    of the problem's covariance as its curvature, and a linear term, stated as a
    :class:`QuadraticForm`. ``CONVEX``: convex, with a method of its own, as the
    package's risk parity, maximum diversification and robust objectives have.
    ``NONCONVEX``: never solved, since no certified method minimizes one.
    """

    LINEAR = "linear"
    CONVEX_QUADRATIC = "convex_quadratic"
    CONVEX = "convex"
    NONCONVEX = "nonconvex"


@dataclass(frozen=True, slots=True)
class QuadraticForm:
    """A convex quadratic objective as construction's quadratic methods solve it (v4.3).

    .. code-block:: text

        minimize  (scale / 2) w' C w  +  sum_i linear_i * w_i

    over the problem's constraints, ``C`` its covariance. The curvature is the
    problem's own risk model, scaled, so the dense and the factor-structured
    methods both apply and a problem holds one covariance.

    Attributes:
        scale: The curvature's multiple of the covariance: finite and positive.
        linear: The coefficient of each weight, for exactly the universe.
    """

    scale: float
    linear: Mapping[str, float]

    def __post_init__(self) -> None:
        scale = _number(self.scale, "QuadraticForm scale")
        if scale <= 0.0:
            raise ConstructionInputError(f"QuadraticForm scale must be positive, got {scale!r}.")
        if not isinstance(self.linear, Mapping):
            raise ConstructionInputError("QuadraticForm linear must map each asset to a number.")
        linear = {
            _text(asset, "QuadraticForm asset"): _number(value, f"QuadraticForm linear[{asset!r}]")
            for asset, value in self.linear.items()
        }
        object.__setattr__(self, "scale", scale)
        object.__setattr__(self, "linear", MappingProxyType(dict(sorted(linear.items()))))


@runtime_checkable
class Objective(Protocol):
    """What a construction optimizes, and what kind of function it is (v4.3, ADR-0063).

    The package's five objectives satisfy it, and so may a researcher's own. An
    objective that is not one of the package's enters the problem's identity by
    its class's qualified name and its :class:`Convexity`, then its
    :meth:`rendering`: two objectives that render alike are still two problems.
    """

    @property
    def convexity(self) -> Convexity:
        """The objective's class, which decides the method that solves it."""
        ...

    def rendering(self) -> list[str]:
        """The objective's identity lines: every parameter that decides the answer."""
        ...


@runtime_checkable
class QuadraticObjective(Objective, Protocol):
    """A ``CONVEX_QUADRATIC`` objective of the protocol, stating its form (v4.3)."""

    def quadratic_form(self, problem: ConstructionProblem) -> QuadraticForm:
        """The objective over ``problem``: its curvature's scale and its linear term."""
        ...


def _coefficients(terms: object, what: str) -> MappingProxyType[str, float]:
    """Name -> finite coefficient, sorted, each zero left out: it adds nothing to a sum."""

    if not isinstance(terms, Mapping):
        raise ConstructionInputError(f"{what} must map each name to a number, got {terms!r}.")
    values = {
        _text(name, f"a name in {what}"): _number(value, f"{what}[{name!r}]")
        for name, value in terms.items()
    }
    return MappingProxyType({name: value for name, value in sorted(values.items()) if value})


@dataclass(frozen=True, slots=True)
class LinearVariable:
    """A variable a linear objective introduces beside the weights (v4.3, ADR-0063).

    A lifting's variable -- CVaR's threshold, an excess loss per scenario --
    with the finite range the objective implies for it, never one construction
    supplies (AC-12), and its cost.

    Attributes:
        name: The variable's name, unique within its :class:`LinearForm`.
        lower: Its least value: finite.
        upper: Its greatest: finite, and not below ``lower``.
        cost: Its coefficient in the objective: finite.
    """

    name: str
    lower: float
    upper: float
    cost: float

    def __post_init__(self) -> None:
        name = _text(self.name, "LinearVariable name")
        lower = _number(self.lower, f"LinearVariable {name!r} lower")
        upper = _number(self.upper, f"LinearVariable {name!r} upper")
        _range(lower, upper, f"LinearVariable {name!r}")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "cost", _number(self.cost, f"LinearVariable {name!r} cost"))


@dataclass(frozen=True, slots=True)
class LinearRow:
    """``lower <= sum_a weights_a w_a + sum_v variables_v x_v <= upper`` (v4.3, ADR-0063).

    A row of a linear objective's lifting, over the weights and the variables
    its :class:`LinearForm` introduces: sparse, so an asset or variable it does
    not list has no coefficient in it.

    Attributes:
        label: The row's name, unique within its form; a result names the row
            by it, as ``objective[label]``.
        weights: Asset -> coefficient, for assets of the universe.
        variables: Variable name -> coefficient, for the form's variables.
        lower: The least value of the sum, or ``None`` for none.
        upper: The greatest, or ``None``. At least one side is stated.
    """

    label: str
    weights: Mapping[str, float]
    variables: Mapping[str, float]
    lower: float | None
    upper: float | None

    def __post_init__(self) -> None:
        label = _text(self.label, "LinearRow label")
        weights = _coefficients(self.weights, f"LinearRow {label!r} weights")
        variables = _coefficients(self.variables, f"LinearRow {label!r} variables")
        if not weights and not variables:
            raise ConstructionInputError(f"LinearRow {label!r} has no non-zero coefficient.")
        lower = _optional(self.lower, f"LinearRow {label!r} lower")
        upper = _optional(self.upper, f"LinearRow {label!r} upper")
        if lower is None and upper is None:
            raise ConstructionInputError(
                f"LinearRow {label!r} bounds neither side, so it is no row."
            )
        _range(lower, upper, f"LinearRow {label!r}")
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "variables", variables)
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)


@dataclass(frozen=True, slots=True)
class LinearForm:
    """A linear objective as the certified linear program solves it (v4.3, ADR-0063).

    .. code-block:: text

        minimize  sum_a costs_a w_a  +  sum_v cost_v x_v

    over the problem's constraints, the form's variables ``x`` within their
    ranges, and its rows. A form with no variable and no row is a cost on each
    weight; a lifting -- CVaR's (Rockafellar and Uryasev, 2000), say -- adds the
    variables and the rows that tie them to the weights.

    Attributes:
        costs: The coefficient of each weight, for exactly the universe.
        variables: The variables it introduces, each named once.
        rows: Its rows, each labelled once, over the weights and its variables.
    """

    costs: Mapping[str, float]
    variables: tuple[LinearVariable, ...] = ()
    rows: tuple[LinearRow, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.costs, Mapping):
            raise ConstructionInputError("LinearForm costs must map each asset to a number.")
        costs = {
            _text(asset, "LinearForm asset"): _number(value, f"LinearForm costs[{asset!r}]")
            for asset, value in self.costs.items()
        }
        object.__setattr__(self, "costs", MappingProxyType(dict(sorted(costs.items()))))
        variables, rows = _lifting_parts(self.variables, self.rows, "A LinearForm", "form")
        object.__setattr__(self, "variables", variables)
        object.__setattr__(self, "rows", rows)


def _lifting_parts(
    variables: Iterable[object], rows: Iterable[object], what: str, noun: str
) -> tuple[tuple[LinearVariable, ...], tuple[LinearRow, ...]]:
    """Variables named once, rows labelled once, and each row over variables among them."""

    checked: list[LinearVariable] = []
    for variable in variables:
        if not isinstance(variable, LinearVariable):
            raise ConstructionInputError(f"{variable!r} is not a LinearVariable.")
        checked.append(variable)
    lines: list[LinearRow] = []
    for row in rows:
        if not isinstance(row, LinearRow):
            raise ConstructionInputError(f"{row!r} is not a LinearRow.")
        lines.append(row)
    names = [variable.name for variable in checked]
    if len(set(names)) != len(names):
        raise ConstructionInputError(f"{what} names the same variable twice.")
    labels = [row.label for row in lines]
    if len(set(labels)) != len(labels):
        raise ConstructionInputError(f"{what} labels two rows alike.")
    known = set(names)
    for row in lines:
        unknown = sorted(set(row.variables) - known)
        if unknown:
            raise ConstructionInputError(
                f"LinearRow {row.label!r} names variables the {noun} does not introduce: {unknown}."
            )
    return tuple(checked), tuple(lines)


@runtime_checkable
class LinearObjective(Objective, Protocol):
    """A ``LINEAR`` objective of the protocol, stating its form (v4.3)."""

    def linear_form(self, problem: ConstructionProblem) -> LinearForm:
        """The objective over ``problem``: a cost on each weight, and any lifting."""
        ...


@dataclass(frozen=True, slots=True)
class Lifting:
    """The variables and rows that state a limit to the linear program (v4.3, ADR-0063).

    A :class:`LinearForm` without costs: a limit constrains and never prices, so
    each of its variables costs zero. Each variable states the finite range the
    limit implies for it; :func:`weight_ranges` gives the weights' ranges such
    a range follows from.

    Attributes:
        variables: The variables it introduces, each named once and costing zero.
        rows: Its rows, each labelled once, over the weights and its variables.
    """

    variables: tuple[LinearVariable, ...] = ()
    rows: tuple[LinearRow, ...] = ()

    def __post_init__(self) -> None:
        variables, rows = _lifting_parts(self.variables, self.rows, "A Lifting", "lifting")
        priced = [variable.name for variable in variables if variable.cost != 0.0]
        if priced:
            raise ConstructionInputError(
                f"A limit's variables cost nothing, and {priced} are priced: a cost belongs to "
                "the objective."
            )
        if not rows:
            raise ConstructionInputError("A Lifting with no row limits nothing.")
        object.__setattr__(self, "variables", variables)
        object.__setattr__(self, "rows", rows)


@runtime_checkable
class LinearLimit(Protocol):
    """A constraint of the protocol: ``value(weights) <= limit``, linear once lifted (v4.3).

    ADR-0063 section 2: a constraint states its class and its rows. A limit
    states its class -- ``LINEAR``, the only one a limit takes -- the label a
    result names it by, its bound, its identity lines, the :class:`Lifting` that
    states it to the linear program, and its value at any weights. Construction
    checks an answer against :meth:`value` as well as the lifting, and reports
    it in :attr:`ConstructionDiagnostics.limit_values`. A limit is taken only
    under a linear objective: a quadratic objective under a scenario limit is
    refused (ADR-0063 section 4). Outside the package's own limits, the identity
    lines follow the class's qualified name and the convexity.
    """

    @property
    def convexity(self) -> Convexity:
        """The limit's class: ``LINEAR``."""
        ...

    @property
    def label(self) -> str:
        """How a result names the limit, among its binding constraints and conflicts."""
        ...

    @property
    def limit(self) -> float:
        """The bound: the limit holds when :meth:`value` is at most this."""
        ...

    def rendering(self) -> list[str]:
        """The limit's identity lines: every parameter that decides it."""
        ...

    def lifting(self, problem: ConstructionProblem) -> Lifting:
        """The variables and rows that hold :meth:`value` within :attr:`limit` over ``problem``."""
        ...

    def value(self, problem: ConstructionProblem, weights: Mapping[str, float]) -> float:
        """The limited quantity at ``weights``: what must not exceed :attr:`limit`."""
        ...


@dataclass(frozen=True, slots=True)
class MinimumVariance:
    """Minimize ``w' C w``. No expected return is read."""

    @property
    def convexity(self) -> Convexity:
        return Convexity.CONVEX_QUADRATIC

    def rendering(self) -> list[str]:
        return ["objective=minimum_variance"]


@dataclass(frozen=True, slots=True)
class MeanVariance:
    """Maximize ``mu' w - (risk_aversion / 2) * w' C w`` -- less linear costs, when given.

    Attributes:
        expected_returns: ``mu``, in the covariance's currency and period.
        risk_aversion: ``lambda > 0``. Required: it sets the trade-off between
            return and variance, and there is no neutral value for it.
        costs: What trading away from the current book costs, subtracted from
            the objective (:class:`LinearCosts`, v3.11), or ``None`` for a
            construction that trades for free, as every one did before. Only an
            objective in return units takes costs -- this one, and since v4.3
            :class:`MaximumReturn` -- since no other objective here has a scale
            they could be traded against.
    """

    expected_returns: ExpectedReturns
    risk_aversion: float
    costs: LinearCosts | None = None

    def __post_init__(self) -> None:
        aversion = _number(self.risk_aversion, "risk_aversion")
        if aversion <= 0.0:
            raise ConstructionInputError(f"risk_aversion is {aversion!r}; it must be positive.")
        object.__setattr__(self, "risk_aversion", aversion)
        if self.costs is not None and not isinstance(self.costs, LinearCosts):
            raise ConstructionInputError(f"costs must be LinearCosts, got {self.costs!r}.")

    @property
    def convexity(self) -> Convexity:
        return Convexity.CONVEX_QUADRATIC

    def rendering(self) -> list[str]:
        return [
            "objective=mean_variance",
            f"expected_returns={self.expected_returns.returns_id}",
            f"risk_aversion={_render(self.risk_aversion)}",
            # Only when given, so a cost-free problem's identity is what it was.
            *(() if self.costs is None else self.costs.rendering()),
        ]


@dataclass(frozen=True, slots=True)
class MaximumDiversification:
    """Maximize the diversification ratio ``sum_i w_i sigma_i / sqrt(w' C w)``.

    ``sigma_i`` is ``sqrt(C_ii)`` -- the volatility the covariance itself
    implies; no separate volatility input is accepted, because one that
    disagreed with the diagonal would describe a different risk model.
    Long-only by definition (Choueifaty and Coignard, 2008): every lower bound
    must be non-negative. Solved exactly through the change of variables
    ``y = w / (sigma' w)``, under which the problem is a quadratic program --
    which is also why a constraint that is not invariant to rescaling ``w``
    (turnover, a volatility cap) cannot be expressed and is refused.
    """

    @property
    def convexity(self) -> Convexity:
        return Convexity.CONVEX

    def rendering(self) -> list[str]:
        return ["objective=maximum_diversification"]


@dataclass(frozen=True, slots=True)
class RiskParity:
    """The long-only portfolio whose risk contributions are stated shares of its risk.

    Attributes:
        budgets: Asset -> the share of portfolio volatility it should
            contribute, as exact ``Decimal`` fractions summing to exactly one
            over the whole universe; or ``None`` for *equal* risk contribution,
            ``1/n`` each.

    The solution is unique (see :mod:`~alphalab.portfolio_optimizer.risk_parity`),
    so any further constraint either holds there or cannot hold at all: a
    constraint is checked at the solution and an unmet one is ``INFEASIBLE``.
    Relaxing the budgets to meet other constraints is a different problem, not
    offered here.
    """

    budgets: Mapping[str, Decimal] | None

    def __post_init__(self) -> None:
        if self.budgets is None:
            return
        ordered: dict[str, Decimal] = {}
        for asset, budget in sorted(self.budgets.items()):
            _text(asset, "risk-budget asset")
            if not isinstance(budget, Decimal) or not budget.is_finite() or budget <= 0:
                raise ConstructionInputError(
                    f"The risk budget of {asset!r} is {budget!r}; it must be a positive Decimal."
                )
            ordered[asset] = budget
        total = sum(ordered.values(), Decimal(0))
        if total != 1:
            raise ConstructionInputError(
                f"Risk budgets sum to {total}, not exactly one. They are shares of one "
                "portfolio's risk; state them so they add up rather than having them rescaled."
            )
        object.__setattr__(self, "budgets", MappingProxyType(ordered))

    @classmethod
    def equal(cls) -> RiskParity:
        """Equal risk contribution."""

        return cls(None)

    @property
    def convexity(self) -> Convexity:
        return Convexity.CONVEX

    def rendering(self) -> list[str]:
        if self.budgets is None:
            return ["objective=risk_parity", "budgets=equal"]
        return [
            "objective=risk_parity",
            *(
                f"budget[{_render(asset)}]={canonical_text(budget)}"
                for asset, budget in self.budgets.items()
            ),
        ]


@dataclass(frozen=True, slots=True)
class EllipsoidalUncertainty:
    """``mu`` lies in ``{mu_hat + Omega^(1/2) u : ||u|| <= radius}``.

    The worst case of ``mu' w`` over this set is ``mu_hat' w - radius *
    sqrt(w' Omega w)`` (Goldfarb and Iyengar, 2003; Ceria and Stubbs, 2006), so a
    robust portfolio pays a penalty proportional to how uncertain its own
    expected return is.

    Attributes:
        omega: The covariance of the *estimation error* of ``mu_hat`` -- not the
            covariance of returns. For a sample mean of ``T`` independent
            observations it is ``C / T`` (:meth:`of_sample_mean`). Positive
            definite, same currency and period as the returns.
        radius: The size of the set, ``>= 0``. Zero is the non-robust problem.
    """

    omega: CovarianceMatrix
    radius: float

    def __post_init__(self) -> None:
        radius = _number(self.radius, "uncertainty radius")
        if radius < 0.0:
            raise ConstructionInputError(f"The uncertainty radius {radius!r} is negative.")
        object.__setattr__(self, "radius", radius)

    @classmethod
    def of_sample_mean(
        cls, covariance: CovarianceMatrix, observations: int, radius: float
    ) -> EllipsoidalUncertainty:
        """``Omega = C / T``: the sampling covariance of a mean of ``T`` independent returns."""

        if isinstance(observations, bool) or not isinstance(observations, int) or observations < 2:
            raise ConstructionInputError(
                f"observations is {observations!r}; it must be at least 2."
            )
        scale = 1.0 / observations
        count = len(covariance.assets)
        rows = tuple(
            tuple(covariance.values[row][column] * scale for column in range(count))
            for row in range(count)
        )
        omega = CovarianceMatrix(
            covariance.assets,
            rows,
            covariance.currency,
            covariance.period,
            covariance.source,
            covariance.observations,
            parent_id=covariance.covariance_id,
            derivation=f"sampling covariance of a mean: C / {observations}",
        )
        return cls(omega, radius)

    def rendering(self) -> list[str]:
        return [
            "uncertainty=ellipsoidal",
            f"omega={self.omega.covariance_id}",
            f"radius={_render(self.radius)}",
        ]


@dataclass(frozen=True, slots=True)
class BoxUncertainty:
    """``mu_i`` lies in ``[mu_hat_i - half_width_i, mu_hat_i + half_width_i]``.

    For a **long-only** portfolio the worst case is exactly ``mu_hat - half_width``
    element by element, so the robust problem is mean-variance on the shifted
    returns -- an identity, not an approximation. For a portfolio that **may
    short**, the worst case is ``mu_hat' w - half_width' |w|``: an ``L1``
    penalty on the weights, solved exactly as :class:`LinearCosts` are -- one
    orthant at a time, certified by the true subgradient condition -- with the
    held weights at zero (v3.13; until then a box set was refused for a
    portfolio that may short). An asset whose return is too uncertain to be
    worth holding either way is held at exactly zero, and the result says so.

    Attributes:
        half_widths: Asset -> ``>= 0``, for exactly the universe.
    """

    half_widths: Mapping[str, float]

    def __post_init__(self) -> None:
        ordered: dict[str, float] = {}
        for asset, width in sorted(self.half_widths.items()):
            value = _number(width, f"half width of {_text(asset, 'box-uncertainty asset')!r}")
            if value < 0.0:
                raise ConstructionInputError(f"The half width of {asset!r} is negative.")
            ordered[asset] = value
        if not ordered:
            raise ConstructionInputError("A box uncertainty set over no asset describes nothing.")
        object.__setattr__(self, "half_widths", MappingProxyType(ordered))

    def rendering(self) -> list[str]:
        return [
            "uncertainty=box",
            *(
                f"half_width[{_render(asset)}]={_render(width)}"
                for asset, width in self.half_widths.items()
            ),
        ]


@dataclass(frozen=True, slots=True)
class RobustMeanVariance:
    """Maximize the worst case of the mean-variance objective over an uncertainty set.

    .. code-block:: text

        maximize   min over mu in U of  [ mu' w - (lambda / 2) w' C w ]

    The uncertainty is only in ``mu``; ``C`` is taken as given. That is the
    whole of what "robust" means here, and it is stated because a heuristic
    that merely shrinks returns is often sold under the same name.

    Attributes:
        expected_returns: ``mu_hat``, the nominal estimate.
        risk_aversion: ``lambda > 0``.
        uncertainty: The set ``U``.
    """

    expected_returns: ExpectedReturns
    risk_aversion: float
    uncertainty: EllipsoidalUncertainty | BoxUncertainty

    def __post_init__(self) -> None:
        aversion = _number(self.risk_aversion, "risk_aversion")
        if aversion <= 0.0:
            raise ConstructionInputError(f"risk_aversion is {aversion!r}; it must be positive.")
        object.__setattr__(self, "risk_aversion", aversion)

    @property
    def convexity(self) -> Convexity:
        return Convexity.CONVEX

    def rendering(self) -> list[str]:
        return [
            "objective=robust_mean_variance",
            f"expected_returns={self.expected_returns.returns_id}",
            f"risk_aversion={_render(self.risk_aversion)}",
            *self.uncertainty.rendering(),
        ]


@dataclass(frozen=True, slots=True)
class MaximumReturn:
    """Maximize ``mu' w`` -- less linear costs, when given (v4.3, ADR-0063).

    Linear, so solved by the certified linear program, and bounded only by the
    constraints: each weight needs a finite range they imply (see
    :func:`construct`). Its optimum need not be unique -- two assets with one
    expected return, say -- and the answer is then the optimal vertex the
    method's finish reads from its iterate, the same on every host: one optimum
    of several, certified as optimal, not as the only one.

    Attributes:
        expected_returns: ``mu``, in the covariance's currency and period.
        costs: What trading away from the current book costs, subtracted from
            the objective as :class:`MeanVariance` subtracts it, or ``None``.
    """

    expected_returns: ExpectedReturns
    costs: LinearCosts | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.expected_returns, ExpectedReturns):
            raise ConstructionInputError(
                f"expected_returns must be ExpectedReturns, got {self.expected_returns!r}."
            )
        if self.costs is not None and not isinstance(self.costs, LinearCosts):
            raise ConstructionInputError(f"costs must be LinearCosts, got {self.costs!r}.")

    @property
    def convexity(self) -> Convexity:
        return Convexity.LINEAR

    def rendering(self) -> list[str]:
        return [
            "objective=maximum_return",
            f"expected_returns={self.expected_returns.returns_id}",
            *(() if self.costs is None else self.costs.rendering()),
        ]

    def linear_form(self, problem: ConstructionProblem) -> LinearForm:
        """A cost of ``-mu_a`` on each weight; the trading costs are lifted by construction."""

        returns = _returns_vector(problem, self.expected_returns)
        return LinearForm(
            {asset: -value for asset, value in zip(problem.universe, returns, strict=True)}
        )


# --------------------------------------------------------------------------- #
# Scenario risk (v4.3, ADR-0063 section 4)
# --------------------------------------------------------------------------- #


def _scenario_returns(
    problem: ConstructionProblem, scenarios: object, what: str
) -> tuple[tuple[float, ...], ...]:
    """The scenarios' returns, each over the universe in its order, or a refusal."""

    if not isinstance(scenarios, ScenarioSet):
        raise ConstructionInputError(f"{what} needs a ScenarioSet, got {scenarios!r}.")
    covariance = problem.covariance
    if scenarios.currency != covariance.currency or scenarios.period != covariance.period:
        raise ConstructionInputError(
            f"{what}'s scenarios are per {scenarios.period!r} in {scenarios.currency!r} and the "
            f"covariance is per {covariance.period!r} in {covariance.currency!r}. They are refused "
            "as a pair rather than rescaled or converted."
        )
    if scenarios.assets != problem.universe:
        held, universe = set(scenarios.assets), set(problem.universe)
        raise ConstructionInputError(
            f"{what}'s scenarios must cover exactly the universe: missing "
            f"{sorted(universe - held)}, outside {sorted(held - universe)}. A missing return is "
            "not zero."
        )
    return scenarios.returns


def _reach(
    problem: ConstructionProblem, rows: Sequence[Sequence[float]]
) -> tuple[list[float], list[float]]:
    """The least and the greatest each row's ``r' w`` reaches over the weights' ranges."""

    ranges = weight_ranges(problem)
    lows = [ranges[asset][0] for asset in problem.universe]
    highs = [ranges[asset][1] for asset in problem.universe]
    least = [
        math.fsum(min(r * low, r * high) for r, low, high in zip(row, lows, highs, strict=True))
        for row in rows
    ]
    most = [
        math.fsum(max(r * low, r * high) for r, low, high in zip(row, lows, highs, strict=True))
        for row in rows
    ]
    return least, most


def _cvar_parts(
    problem: ConstructionProblem, scenarios: ScenarioSet, level: float, priced: bool, what: str
) -> tuple[list[LinearVariable], list[LinearRow], float]:
    """CVaR lifted (Rockafellar and Uryasev, 2000): ``alpha`` and one excess per scenario.

    ``excess_s >= -r_s' w - alpha`` and ``excess_s >= 0``, so ``alpha + share *
    sum excess_s``, ``share = 1 / ((1 - level) S)``, is at least the CVaR, and
    equals it at the least ``alpha`` and excesses. The ranges follow from the
    weights': ``alpha`` within the losses' reach, each excess up to its loss's
    greatest less ``alpha``'s least. ``priced`` gives the objective's costs --
    ``alpha`` one, each excess ``share`` -- and a limit's lifting none.
    """

    rows = _scenario_returns(problem, scenarios, what)
    gain_least, gain_most = _reach(problem, rows)
    loss_most = [-value for value in gain_least]
    floor = min(-value for value in gain_most)
    ceiling = max(loss_most)
    share = 1.0 / ((1.0 - level) * len(rows))
    variables = [LinearVariable("alpha", floor, ceiling, 1.0 if priced else 0.0)]
    variables.extend(
        LinearVariable(f"excess[{s}]", 0.0, max(most - floor, 0.0), share if priced else 0.0)
        for s, most in enumerate(loss_most)
    )
    lifted = [
        LinearRow(
            f"excess[{s}]",
            dict(zip(problem.universe, row, strict=True)),
            {"alpha": 1.0, f"excess[{s}]": 1.0},
            0.0,
            None,
        )
        for s, row in enumerate(rows)
    ]
    return variables, lifted, share


@dataclass(frozen=True, slots=True)
class MinimumCVaR:
    """Minimize the CVaR of the portfolio's loss over stated scenarios (v4.3, ADR-0063).

    .. code-block:: text

        CVaR(w) = min over alpha of  alpha + sum_s max(0, -r_s' w - alpha) / ((1 - level) S)

    Rockafellar and Uryasev (2000): the mean loss of the worst ``1 - level``
    share of the scenarios, a fraction weighted. Linear once lifted by
    ``alpha`` and one excess per scenario, so solved by the certified linear
    program; each lifted variable's range follows from the weights'
    (:func:`weight_ranges`), so every weight needs a finite one.
    :meth:`ScenarioSet.cvar` evaluates it at any weights.

    Attributes:
        scenarios: The scenarios, in the covariance's currency and period, over
            exactly the universe.
        level: In ``[0, 1)``. Zero is the mean loss. Required: no level is
            assumed (AC-12).
    """

    scenarios: ScenarioSet
    level: float

    def __post_init__(self) -> None:
        if not isinstance(self.scenarios, ScenarioSet):
            raise ConstructionInputError(
                f"scenarios must be a ScenarioSet, got {self.scenarios!r}."
            )
        object.__setattr__(self, "level", _level(self.level, "MinimumCVaR level", inclusive=False))

    @property
    def convexity(self) -> Convexity:
        return Convexity.LINEAR

    def rendering(self) -> list[str]:
        return [
            "objective=minimum_cvar",
            f"scenarios={self.scenarios.scenarios_id}",
            f"level={_render(self.level)}",
        ]

    def linear_form(self, problem: ConstructionProblem) -> LinearForm:
        """No cost on the weights; ``alpha`` and the excesses carry the objective."""

        variables, rows, _ = _cvar_parts(problem, self.scenarios, self.level, True, "MinimumCVaR")
        return LinearForm(dict.fromkeys(problem.universe, 0.0), tuple(variables), tuple(rows))


@dataclass(frozen=True, slots=True)
class CVaRLimit:
    """The CVaR of the portfolio's loss over stated scenarios, at most ``limit`` (v4.3).

    The CVaR as :class:`MinimumCVaR` defines it, at a stated ``level``; the
    lifting's rows, and ``alpha + sum excess_s / ((1 - level) S) <= limit``
    (ADR-0063 section 4). Taken under a linear objective only.

    Attributes:
        scenarios: The scenarios, in the covariance's units, over exactly the universe.
        level: In ``[0, 1)``.
        limit: The largest CVaR allowed, in return units: finite.
    """

    scenarios: ScenarioSet
    level: float
    limit: float

    def __post_init__(self) -> None:
        if not isinstance(self.scenarios, ScenarioSet):
            raise ConstructionInputError(
                f"scenarios must be a ScenarioSet, got {self.scenarios!r}."
            )
        object.__setattr__(self, "level", _level(self.level, "CVaRLimit level", inclusive=False))
        object.__setattr__(self, "limit", _number(self.limit, "CVaRLimit limit"))

    @property
    def convexity(self) -> Convexity:
        return Convexity.LINEAR

    @property
    def label(self) -> str:
        return (
            f"CVaR at {self.level!r} over scenarios {self.scenarios.scenarios_id[:12]} "
            f"<= {self.limit!r}"
        )

    def rendering(self) -> list[str]:
        return [
            "limit=cvar",
            f"limit.scenarios={self.scenarios.scenarios_id}",
            f"limit.level={_render(self.level)}",
            f"limit.bound={_render(self.limit)}",
        ]

    def lifting(self, problem: ConstructionProblem) -> Lifting:
        variables, rows, share = _cvar_parts(
            problem, self.scenarios, self.level, False, "CVaRLimit"
        )
        rows.append(
            LinearRow(
                "cvar",
                {},
                {"alpha": 1.0, **{f"excess[{s}]": share for s in range(self.scenarios.count)}},
                None,
                self.limit,
            )
        )
        return Lifting(tuple(variables), tuple(rows))

    def value(self, problem: ConstructionProblem, weights: Mapping[str, float]) -> float:
        return self.scenarios.cvar(weights, self.level)


@dataclass(frozen=True, slots=True)
class CDaRLimit:
    """The conditional drawdown of the cumulative sum of returns, at most ``limit`` (v4.3).

    Chekhlov, Uryasev and Zabarankin (2005), over the scenarios read as a path
    (:meth:`ScenarioSet.cdar`): ``c_t`` the sum of the portfolio's returns
    to step ``t``, ``c_0 = 0``, the drawdown ``d_t = max(c_0, ..., c_t) - c_t``.
    At ``level`` in ``[0, 1)`` the mean of the worst ``1 - level`` share of the
    drawdowns is bounded; at one, the maximum drawdown. Lifted by one running
    peak and, below level one, an excess per step (ADR-0063 section 4). Summed,
    not compounded: compounding is not linear in the weights.

    Attributes:
        scenarios: The path, in the covariance's units, over exactly the universe.
        level: In ``[0, 1]``.
        limit: The largest conditional drawdown allowed: finite, and not below
            zero, since a drawdown never is.
    """

    scenarios: ScenarioSet
    level: float
    limit: float

    def __post_init__(self) -> None:
        if not isinstance(self.scenarios, ScenarioSet):
            raise ConstructionInputError(
                f"scenarios must be a ScenarioSet, got {self.scenarios!r}."
            )
        object.__setattr__(self, "level", _level(self.level, "CDaRLimit level", inclusive=True))
        limit = _number(self.limit, "CDaRLimit limit")
        if limit < 0.0:
            raise ConstructionInputError(
                f"CDaRLimit limit is {limit!r}; a drawdown is never negative, so no "
                "portfolio could meet it."
            )
        object.__setattr__(self, "limit", limit)

    @property
    def convexity(self) -> Convexity:
        return Convexity.LINEAR

    @property
    def label(self) -> str:
        return (
            f"CDaR at {self.level!r} over scenarios {self.scenarios.scenarios_id[:12]} "
            f"<= {self.limit!r}"
        )

    def rendering(self) -> list[str]:
        return [
            "limit=cdar",
            f"limit.scenarios={self.scenarios.scenarios_id}",
            f"limit.level={_render(self.level)}",
            f"limit.bound={_render(self.limit)}",
        ]

    def lifting(self, problem: ConstructionProblem) -> Lifting:
        """``peak_t >= c_t``, ``peak_t >= peak_{t-1}``, ``peak_t >= 0``; then the bound.

        At level one, ``peak_t - c_t <= limit`` at every step. Below it, ``zeta``
        and ``excess_t >= peak_t - c_t - zeta``, ``excess_t >= 0``, and ``zeta +
        sum excess_t / ((1 - level) T) <= limit``. A peak may stand above the
        running maximum, which only overstates a drawdown, so the rows hold
        exactly when the drawdown limit does. Each peak's range runs up to the
        most any cumulative sum to then reaches; ``zeta`` and the excesses up to
        the greatest drawdown the weights' ranges allow.
        """

        rows = _scenario_returns(problem, self.scenarios, "CDaRLimit")
        path = _cumulative(rows)
        least, most = _reach(problem, path)
        peaks: list[float] = []
        ceiling = 0.0
        for value in most:
            ceiling = max(ceiling, value)
            peaks.append(ceiling)
        reach = max(peak - low for peak, low in zip(peaks, least, strict=True))
        universe = problem.universe
        variables = [LinearVariable(f"peak[{t}]", 0.0, peak, 0.0) for t, peak in enumerate(peaks)]
        top = self.limit if self.level == 1.0 else None
        lifted = [
            LinearRow(
                f"above[{t}]",
                {asset: -value for asset, value in zip(universe, cumulative, strict=True)},
                {f"peak[{t}]": 1.0},
                0.0,
                top,
            )
            for t, cumulative in enumerate(path)
        ]
        lifted.extend(
            LinearRow(f"rising[{t}]", {}, {f"peak[{t}]": 1.0, f"peak[{t - 1}]": -1.0}, 0.0, None)
            for t in range(1, len(path))
        )
        if self.level < 1.0:
            share = 1.0 / ((1.0 - self.level) * len(path))
            variables.append(LinearVariable("zeta", 0.0, reach, 0.0))
            variables.extend(
                LinearVariable(f"excess[{t}]", 0.0, reach, 0.0) for t in range(len(path))
            )
            lifted.extend(
                LinearRow(
                    f"excess[{t}]",
                    dict(zip(universe, cumulative, strict=True)),
                    {f"excess[{t}]": 1.0, f"peak[{t}]": -1.0, "zeta": 1.0},
                    0.0,
                    None,
                )
                for t, cumulative in enumerate(path)
            )
            lifted.append(
                LinearRow(
                    "cdar",
                    {},
                    {"zeta": 1.0, **{f"excess[{t}]": share for t in range(len(path))}},
                    None,
                    self.limit,
                )
            )
        return Lifting(tuple(variables), tuple(lifted))

    def value(self, problem: ConstructionProblem, weights: Mapping[str, float]) -> float:
        return self.scenarios.cdar(weights, self.level)


#: The package's own limits, identified by their rendering alone.
_SHIPPED_LIMITS: Final = (CVaRLimit, CDaRLimit)


def _limit_lines(limit: LinearLimit) -> list[str]:
    """A limit's identity lines, after its class and kind when it is not the package's."""

    if isinstance(limit, _SHIPPED_LIMITS):
        return limit.rendering()
    kind = type(limit)
    return [
        f"limit.class={kind.__module__}.{kind.__qualname__}",
        f"limit.convexity={limit.convexity.value}",
        *limit.rendering(),
    ]


#: The objectives the package ships. A problem takes any :class:`Objective` (v4.3).
type ConstructionObjective = (
    MinimumVariance
    | MeanVariance
    | MaximumDiversification
    | RiskParity
    | RobustMeanVariance
    | MaximumReturn
    | MinimumCVaR
)

#: The package's own objectives, each solved by a method of its own and identified
#: by its rendering alone, as before v4.3.
_SHIPPED: Final = (
    MinimumVariance,
    MeanVariance,
    MaximumDiversification,
    RiskParity,
    RobustMeanVariance,
    MaximumReturn,
    MinimumCVaR,
)


def _objective_lines(objective: Objective) -> list[str]:
    """An objective's identity lines, after its class and kind when it is not the package's."""

    if isinstance(objective, _SHIPPED):
        return objective.rendering()
    kind = type(objective)
    return [
        f"objective.class={kind.__module__}.{kind.__qualname__}",
        f"objective.convexity={objective.convexity.value}",
        *objective.rendering(),
    ]


@dataclass(frozen=True, slots=True)
class ConstructionProblem:
    """Everything a construction is decided by.

    Which method solves it follows from the covariance: a
    :class:`~alphalab.analytics.risk_model.FactorStructure`, or a matrix that
    carries one, over at least :data:`FACTOR_STRUCTURED_MINIMUM_ASSETS` assets,
    is solved by the factor-structured method (v3.12, ledger PRF-005), and its
    :attr:`problem_id` says so.

    Attributes:
        covariance: The risk model: a matrix, or since v3.13 a covariance
            stated by its factor structure, which is never written out unless a
            method needs the dense values (ledger PRF-013). Its assets are the
            universe, in canonical order; its currency and period are the units
            of every weight and risk figure.
        objective: What is optimized: one of the package's objectives, or
            since v4.3 any of the :class:`Objective` protocol (ADR-0063).
        constraints: What must hold.
        settings: The numerical contract.
    """

    covariance: CovarianceMatrix | FactorStructure
    objective: Objective
    constraints: ConstraintSet
    settings: SolverSettings

    @property
    def universe(self) -> tuple[str, ...]:
        """The assets weighted, sorted."""

        return self.covariance.assets

    @property
    def problem_id(self) -> str:
        """The derived identity of this problem, independent of constraint order."""

        return _digest(
            [
                CONSTRUCTION_PROBLEM_SCHEME,
                f"covariance={self.covariance.covariance_id}",
                *_objective_lines(self.objective),
                *self.constraints.rendering(),
                f"feasibility_tolerance={_render(self.settings.feasibility_tolerance)}",
                f"convergence_tolerance={_render(self.settings.convergence_tolerance)}",
                f"max_iterations={_render(self.settings.max_iterations)}",
                # v3.12 (PRF-005): the two methods agree within the tolerances, not
                # to the bit, so which one solves a problem is part of what it is.
                # Present only when the structured method applies, so every other
                # problem keeps the identity it had.
                *(["solver=factor-structured"] if _factor_structured(self) else []),
                # v4.3 (ADR-0063): a linear objective is solved by the linear program.
                *(["solver=linear"] if _is_linear(self.objective) else []),
            ]
        )


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


class ConstructionStatus(Enum):
    """How a construction ended.

    ``OPTIMAL``: a solution that verifiably satisfies every constraint and the
    method's optimality conditions within the stated tolerances. ``INFEASIBLE``:
    the constraints cannot all hold; the diagnostics name the ones that
    conflict. ``ITERATION_LIMIT``: the step budget ran out first.
    ``NUMERICAL_FAILURE``: the method stopped without an answer it could
    certify in double precision. Only ``OPTIMAL`` carries weights.
    """

    OPTIMAL = auto()
    INFEASIBLE = auto()
    ITERATION_LIMIT = auto()
    NUMERICAL_FAILURE = auto()


@dataclass(frozen=True, slots=True)
class ConstructionDiagnostics:
    """The evidence behind a result: what the method did and what the portfolio is.

    Attributes:
        method: The objective's name.
        iterations: Solver steps taken -- quadratic add-or-drop steps, risk
            parity sweeps, the outer iterations of a robust or
            volatility-capped search, and the linear program's interior-point
            iterations, summed.
        binding: Labels of the constraints active at the solution.
        conflict: For ``INFEASIBLE``: the constraints that jointly cannot hold.
        max_violation: The largest constraint residual of the reported (or
            last) point, in each constraint's own units.
        stationarity: The method's convergence measure at the end (see
            :class:`SolverSettings`); for the linear program, the relative gap
            of an optimal answer, and infinity otherwise.
        covariance_pivot_ratio: Smallest over largest Cholesky pivot of the
            covariance: how close it is to singular.
        variance: ``w' C w``, or ``None`` without weights.
        volatility: ``sqrt(variance)``, or ``None``.
        expected_return: ``mu' w`` for an objective with expected returns, or
            ``None``.
        worst_case_return: For a robust objective, the worst ``mu' w`` over the
            uncertainty set; ``None`` otherwise.
        diversification_ratio: ``sum_i w_i sigma_i / volatility`` for a long
            portfolio, or ``None``.
        net_exposure: ``sum_i w_i``, or ``None``.
        gross_exposure: ``sum_i |w_i|``, or ``None``.
        turnover: ``sum_i |w_i - current_i|`` when a turnover limit was given,
            or ``None``.
        risk: The Euler risk decomposition of the weights, or ``None``.
        factor_exposures: Factor -> exposure, for every constrained factor.
        group_exposures: ``dimension[label]`` -> total weight, for every bucket
            of every classification a constraint used.
        budget_deviation: For risk parity, the largest ``|share - budget|``.
        effective_risk_aversion: When a volatility cap bound a mean-variance
            objective, the risk aversion at which the capped solution was
            found (the cap's multiplier, re-expressed); otherwise ``None``.
        detail: A sentence saying why the status is what it is.
        transaction_cost: ``sum_i rates_i * |w_i - current_i|`` when the
            objective charged :class:`LinearCosts`, or ``None`` (v3.11;
            :class:`MaximumReturn` charges them since v4.3).
        limit_values: Each limit's :meth:`LinearLimit.value` at the weights,
            by its label: what construction checked against its bound, beside
            the lifting the method solved (v4.3). Empty without weights or
            limits.
    """

    method: str
    iterations: int
    binding: tuple[str, ...]
    conflict: tuple[str, ...]
    max_violation: float
    stationarity: float
    covariance_pivot_ratio: float
    variance: float | None
    volatility: float | None
    expected_return: float | None
    worst_case_return: float | None
    diversification_ratio: float | None
    net_exposure: float | None
    gross_exposure: float | None
    turnover: float | None
    risk: RiskContributions | None
    factor_exposures: Mapping[str, float]
    group_exposures: Mapping[str, float]
    budget_deviation: float | None
    effective_risk_aversion: float | None
    detail: str
    transaction_cost: float | None = None
    limit_values: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class ConstructionResult:
    """The outcome of :func:`construct`.

    Attributes:
        problem_id: The problem it answers.
        status: How it ended.
        weights: Asset -> weight for the whole universe, sorted, when
            ``OPTIMAL``; ``None`` otherwise.
        diagnostics: The evidence.
    """

    problem_id: str
    status: ConstructionStatus
    weights: Mapping[str, float] | None
    diagnostics: ConstructionDiagnostics

    @property
    def succeeded(self) -> bool:
        """Whether this result carries a certified portfolio."""

        return self.status is ConstructionStatus.OPTIMAL

    @property
    def result_id(self) -> str:
        """The derived identity of this outcome."""

        weights = (
            ["weights=None"]
            if self.weights is None
            else [
                f"weight[{_render(asset)}]={_render(value)}"
                for asset, value in self.weights.items()
            ]
        )
        return _digest(
            [
                CONSTRUCTION_RESULT_SCHEME,
                f"problem={self.problem_id}",
                f"status={self.status.name}",
                *weights,
                *(f"conflict={_render(label)}" for label in self.diagnostics.conflict),
            ]
        )

    def require_weights(self) -> Mapping[str, float]:
        """The weights, or a refusal that says why there are none.

        Raises:
            OptimizationError: If the status is not ``OPTIMAL``.
        """

        if self.weights is None:
            conflict = (
                f" Conflicting constraints: {list(self.diagnostics.conflict)}."
                if self.diagnostics.conflict
                else ""
            )
            raise OptimizationError(
                f"Construction {self.problem_id[:12]} ended {self.status.name} and has no weights. "
                f"{self.diagnostics.detail}{conflict}"
            )
        return self.weights


# --------------------------------------------------------------------------- #
# Compilation: constraints -> linear constraints over the universe
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Compiled:
    linear: tuple[LinearConstraint, ...]
    absolute: tuple[AbsoluteSumLimit, ...]
    unmet: tuple[str, ...]  # constraints with no variable that cannot hold: infeasible outright


def _interval(
    label: str,
    terms: Sequence[tuple[int, float]],
    lower: float | None,
    upper: float | None,
) -> list[LinearConstraint]:
    """``lower <= terms.x <= upper`` as one equality or up to two inequalities."""

    if lower is not None and lower == upper:
        return [LinearConstraint(f"{label} = {lower!r}", tuple(terms), lower, True)]
    built: list[LinearConstraint] = []
    if lower is not None:
        built.append(LinearConstraint(f"{label} >= {lower!r}", tuple(terms), lower, False))
    if upper is not None:
        built.append(
            LinearConstraint(
                f"{label} <= {upper!r}",
                tuple((index, -value) for index, value in terms),
                -upper,
                False,
            )
        )
    return built


def _require_universe(problem: ConstructionProblem) -> None:
    universe = set(problem.universe)
    constraints = problem.constraints
    unknown = sorted(set(constraints.bounds.overrides) - universe)
    if unknown:
        raise ConstructionInputError(f"Weight bounds name assets outside the universe: {unknown}.")
    for bound in constraints.groups:
        try:
            bound.classification.require_covers(universe, f"The {bound.name} constraint")
        except AnalyticsValidationError as error:
            raise ConstructionInputError(str(error)) from error
    for factor in constraints.factors:
        try:
            factor.loadings.require_covers(universe, f"The {factor.name} constraint")
        except AnalyticsValidationError as error:
            raise ConstructionInputError(str(error)) from error
    if constraints.turnover is not None:
        held = set(constraints.turnover.current)
        if held != universe:
            raise ConstructionInputError(
                "TurnoverLimit.current must list exactly the universe, zeros included: missing "
                f"{sorted(universe - held)}, outside {sorted(held - universe)}. An unlisted asset "
                "is not assumed unheld."
            )
    if constraints.notional_limits is not None:
        outside = sorted(set(constraints.notional_limits.maximum_notional) - universe)
        if outside:
            raise ConstructionInputError(
                f"Notional limits name assets outside the universe: {outside}."
            )


def _compile(problem: ConstructionProblem) -> _Compiled:
    """Every constraint as linear constraints and absolute-sum limits over the weights."""

    universe = problem.universe
    constraints = problem.constraints
    everyone = [(index, 1.0) for index in range(len(universe))]
    linear: list[LinearConstraint] = []
    unmet: list[str] = []

    linear.extend(
        _interval(
            "net exposure", everyone, constraints.net_exposure.lower, constraints.net_exposure.upper
        )
    )
    for index, asset in enumerate(universe):
        lower, upper = constraints.bounds.for_asset(asset)
        linear.extend(_interval(f"weight[{asset}]", [(index, 1.0)], lower, upper))
    if constraints.max_abs_weight is not None:
        cap = constraints.max_abs_weight
        for index, asset in enumerate(universe):
            linear.extend(_interval(f"concentration[{asset}]", [(index, 1.0)], -cap, cap))
    if constraints.notional_limits is not None:
        limits = constraints.notional_limits
        position = {asset: index for index, asset in enumerate(universe)}
        for asset in limits.maximum_notional:
            cap = limits.weight_limit(asset)
            linear.extend(_interval(f"notional[{asset}]", [(position[asset], 1.0)], -cap, cap))
    for bound in constraints.groups:
        members = set(bound.classification.members(bound.label))
        terms = [(index, 1.0) for index, asset in enumerate(universe) if asset in members]
        if terms:
            linear.extend(_interval(bound.name, terms, bound.lower, bound.upper))
        elif not ExposureRange(bound.lower, bound.upper).contains(0.0, 0.0):
            unmet.append(f"{bound.name}: no asset in the universe is classified {bound.label!r}")
    for factor in constraints.factors:
        column = factor.loadings.require_factor(factor.factor)
        rows = dict(zip(factor.loadings.assets, factor.loadings.values, strict=True))
        loadings = [rows[asset][column] for asset in universe]
        terms = [(index, value) for index, value in enumerate(loadings) if value != 0.0]
        if terms:
            linear.extend(_interval(factor.name, terms, factor.lower, factor.upper))
        elif not ExposureRange(factor.lower, factor.upper).contains(0.0, 0.0):
            unmet.append(f"{factor.name}: every asset in the universe has zero loading")

    absolute: list[AbsoluteSumLimit] = []
    if constraints.max_gross_exposure is not None:
        absolute.append(
            AbsoluteSumLimit(
                f"gross exposure <= {constraints.max_gross_exposure!r}",
                tuple(range(len(universe))),
                tuple(0.0 for _ in universe),
                constraints.max_gross_exposure,
            )
        )
    if constraints.turnover is not None:
        absolute.append(
            AbsoluteSumLimit(
                f"turnover <= {constraints.turnover.maximum!r}",
                tuple(range(len(universe))),
                tuple(constraints.turnover.current[asset] for asset in universe),
                constraints.turnover.maximum,
            )
        )
    return _Compiled(tuple(linear), tuple(absolute), tuple(unmet))


def _hessian(rows: Sequence[Sequence[float]], scale: float) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(scale * value for value in row) for row in rows)


@dataclass(frozen=True, slots=True)
class _Constraints:
    """A program's constraints alone: what checking a point reads, nothing ``n x n``."""

    constraints: tuple[LinearConstraint, ...]
    absolute_sums: tuple[AbsoluteSumLimit, ...]


def _structure(problem: ConstructionProblem) -> FactorStructure | None:
    """The factor structure: the covariance itself, the one its matrix carries, or none."""

    covariance = problem.covariance
    return covariance if isinstance(covariance, FactorStructure) else covariance.factors


def _matrix(problem: ConstructionProblem) -> CovarianceMatrix:
    """The dense covariance: the problem's matrix, or the one its structure implies.

    A structure writes its matrix out -- ``O(n^2)`` -- the first time a method
    needs the dense values, and keeps it (ledger PRF-013).
    """

    covariance = problem.covariance
    return covariance.matrix() if isinstance(covariance, FactorStructure) else covariance


def _is_linear(objective: object) -> bool:
    """Whether ``objective`` declares itself ``LINEAR``: the linear program solves it (v4.3)."""

    return getattr(objective, "convexity", None) is Convexity.LINEAR


def _through_factors(problem: ConstructionProblem) -> bool:
    """Whether ``problem``'s risk is read through its factor structure (ledger PRF-005).

    When the covariance is or carries a factor structure, the structure
    establishes positive definiteness by itself, and the universe has at least
    :data:`FACTOR_STRUCTURED_MINIMUM_ASSETS` assets.
    """

    factors = _structure(problem)
    return (
        factors is not None
        and len(problem.covariance.assets) >= FACTOR_STRUCTURED_MINIMUM_ASSETS
        and factors.definiteness() is not None
    )


def _factor_structured(problem: ConstructionProblem) -> bool:
    """Whether the factor-structured method solves ``problem``'s programs (ledger PRF-005).

    When its risk is read through the factor structure and the objective is
    solved by quadratic programs -- risk parity is not, nor since v4.3 is a
    linear objective.
    """

    return (
        not isinstance(problem.objective, RiskParity)
        and not _is_linear(problem.objective)
        and _through_factors(problem)
    )


def _curvature(problem: ConstructionProblem, scale: float) -> FactorCurvature:
    factors = _structure(problem)
    assert factors is not None  # only asked for when _factor_structured holds
    return FactorCurvature.of_structure(factors, scale)


def _solve(
    problem: ConstructionProblem,
    scale: float,
    linear: Sequence[float],
    constraints: tuple[LinearConstraint, ...],
    absolute: tuple[AbsoluteSumLimit, ...],
    iterations: int,
    hessian: tuple[tuple[float, ...], ...] | None = None,
) -> QuadraticSolution:
    """One program with curvature ``scale * C``, or ``hessian``, by the problem's method.

    The factor-structured method answers when it certifies. Anything else it
    returns goes to the dense method with the steps that remain, which decides
    -- the structured method never claims infeasibility -- and the detail says
    so. ``hessian`` is a curvature that is not a multiple of the covariance (a
    robust objective's), which only the dense method takes.
    """

    settings = problem.settings
    budget = max(1, settings.max_iterations - iterations)
    if hessian is None and _factor_structured(problem):
        structured = solve_factor_quadratic_program(
            FactorQuadraticProgram(
                _curvature(problem, scale), tuple(linear), constraints, absolute
            ),
            feasibility_tolerance=settings.feasibility_tolerance,
            convergence_tolerance=settings.convergence_tolerance,
            max_iterations=budget,
        )
        if structured.status is SolveStatus.OPTIMAL:
            return structured
        dense = solve_quadratic_program(
            QuadraticProgram(
                _hessian(_matrix(problem).values, scale), tuple(linear), constraints, absolute
            ),
            feasibility_tolerance=settings.feasibility_tolerance,
            convergence_tolerance=settings.convergence_tolerance,
            max_iterations=max(1, budget - structured.iterations),
        )
        return replace(
            dense,
            iterations=structured.iterations + dense.iterations,
            detail=(
                f"{dense.detail} The factor-structured method did not certify this program "
                f"({structured.status.name}: {structured.detail}), so the dense method decided "
                "it."
            ),
        )
    return solve_quadratic_program(
        QuadraticProgram(
            hessian if hessian is not None else _hessian(_matrix(problem).values, scale),
            tuple(linear),
            constraints,
            absolute,
        ),
        feasibility_tolerance=settings.feasibility_tolerance,
        convergence_tolerance=settings.convergence_tolerance,
        max_iterations=budget,
    )


def _portfolio_volatility(problem: ConstructionProblem, weights: Sequence[float]) -> float:
    """``sqrt(w' C w)``, through the factor structure when the problem is solved through it."""

    if _factor_structured(problem):
        product = _curvature(problem, 1.0).times(weights)
        variance = math.fsum(w * p for w, p in zip(weights, product, strict=True))
        return math.sqrt(max(0.0, variance))
    return _volatility(weights, _matrix(problem).values)


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: ConstructionStatus
    weights: tuple[float, ...] | None
    iterations: int
    binding: tuple[str, ...]
    conflict: tuple[str, ...]
    stationarity: float
    detail: str
    budget_deviation: float | None = None
    effective_risk_aversion: float | None = None
    worst_case_return: float | None = None


_STATUS: Final = {
    SolveStatus.OPTIMAL: ConstructionStatus.OPTIMAL,
    SolveStatus.INFEASIBLE: ConstructionStatus.INFEASIBLE,
    SolveStatus.ITERATION_LIMIT: ConstructionStatus.ITERATION_LIMIT,
    SolveStatus.NUMERICAL_FAILURE: ConstructionStatus.NUMERICAL_FAILURE,
}


def _from_solution(solution: QuadraticSolution, iterations: int) -> _Outcome:
    # Two facets of one absolute-sum limit can be active together; the limit is
    # reported once.
    return _Outcome(
        _STATUS[solution.status],
        solution.x,
        iterations + solution.iterations,
        tuple(dict.fromkeys(solution.active)),
        tuple(dict.fromkeys(solution.conflict)),
        solution.stationarity,
        solution.detail,
    )


def _result(
    problem: ConstructionProblem,
    method: str,
    outcome: _Outcome,
    compiled: _Compiled,
    pivot_ratio: float,
    expected: ExpectedReturns | None,
) -> ConstructionResult:
    universe = problem.universe
    constraints = problem.constraints
    objective_costs = (
        problem.objective.costs
        if isinstance(problem.objective, MeanVariance | MaximumReturn)
        else None
    )
    checker = _Constraints(compiled.linear, compiled.absolute)
    if outcome.weights is None or outcome.status is not ConstructionStatus.OPTIMAL:
        diagnostics = ConstructionDiagnostics(
            method=method,
            iterations=outcome.iterations,
            binding=outcome.binding,
            conflict=outcome.conflict,
            max_violation=0.0,
            stationarity=outcome.stationarity,
            covariance_pivot_ratio=pivot_ratio,
            variance=None,
            volatility=None,
            expected_return=None,
            worst_case_return=None,
            diversification_ratio=None,
            net_exposure=None,
            gross_exposure=None,
            turnover=None,
            risk=None,
            factor_exposures=MappingProxyType({}),
            group_exposures=MappingProxyType({}),
            budget_deviation=outcome.budget_deviation,
            effective_risk_aversion=None,
            detail=outcome.detail,
        )
        return ConstructionResult(problem.problem_id, outcome.status, None, diagnostics)

    weights = MappingProxyType(dict(zip(universe, outcome.weights, strict=True)))
    residuals = constraint_residuals(checker, outcome.weights)
    violation = max(residuals.values(), default=0.0)
    # A non-zero portfolio under a positive definite covariance has positive
    # volatility; only the zero portfolio (a zero net exposure with nothing
    # else to hold) has none, and it has no risk to decompose.
    risk = euler_decomposition(weights, problem.covariance) if any(weights.values()) else None
    variance = 0.0 if risk is None else risk.variance
    volatility = 0.0 if risk is None else risk.volatility
    if constraints.max_volatility is not None:
        violation = max(violation, max(0.0, volatility - constraints.max_volatility))
    # v4.3 (ADR-0063): each limit is checked at its own value, not only through its lifting.
    limit_values: dict[str, float] = {}
    binding = outcome.binding
    for limit in constraints.limits:
        value = _number(limit.value(problem, weights), f"the value of the limit {limit.label!r}")
        limit_values[limit.label] = value
        violation = max(violation, value - limit.limit)
        if limit.limit - value <= problem.settings.feasibility_tolerance:
            binding = (*binding, limit.label)

    factor_exposures: dict[str, float] = {}
    for bound in constraints.factors:
        exposures = portfolio_factor_exposures(weights, bound.loadings)
        factor_exposures[bound.factor] = exposures[bound.factor]
    group_exposures: dict[str, float] = {}
    classifications = {
        bound.classification.classification_id: bound.classification for bound in constraints.groups
    }
    for classification in classifications.values():
        for label in classification.buckets:
            members = set(classification.members(label))
            group_exposures[f"{classification.dimension}[{label}]"] = math.fsum(
                weights[asset] for asset in universe if asset in members
            )
    long_only = all(value >= 0.0 for value in outcome.weights)
    diversification = (
        math.fsum(weights[asset] * problem.covariance.volatility(asset) for asset in universe)
        / volatility
        if long_only and volatility > 0.0
        else None
    )
    diagnostics = ConstructionDiagnostics(
        method=method,
        iterations=outcome.iterations,
        binding=binding,
        conflict=(),
        max_violation=violation,
        stationarity=outcome.stationarity,
        covariance_pivot_ratio=pivot_ratio,
        variance=variance,
        volatility=volatility,
        expected_return=(
            None
            if expected is None
            else math.fsum(expected.values[asset] * weights[asset] for asset in universe)
        ),
        worst_case_return=outcome.worst_case_return,
        diversification_ratio=diversification,
        net_exposure=math.fsum(outcome.weights),
        gross_exposure=math.fsum(abs(value) for value in outcome.weights),
        transaction_cost=(objective_costs.cost(weights) if objective_costs is not None else None),
        turnover=(
            None
            if constraints.turnover is None
            else math.fsum(
                abs(weights[asset] - constraints.turnover.current[asset]) for asset in universe
            )
        ),
        risk=risk,
        factor_exposures=MappingProxyType(dict(sorted(factor_exposures.items()))),
        group_exposures=MappingProxyType(dict(sorted(group_exposures.items()))),
        budget_deviation=outcome.budget_deviation,
        effective_risk_aversion=outcome.effective_risk_aversion,
        detail=outcome.detail,
        limit_values=MappingProxyType(limit_values),
    )
    if violation > problem.settings.feasibility_tolerance:
        # Every method certifies its own answer; this is the construction-level
        # check that the certificate and the constraint set agree.
        return ConstructionResult(
            problem.problem_id,
            ConstructionStatus.NUMERICAL_FAILURE,
            None,
            replace(
                diagnostics,
                detail=(
                    f"The method's answer misses a constraint by {violation:.3g}, more than "
                    f"the feasibility tolerance {problem.settings.feasibility_tolerance:.3g}."
                ),
            ),
        )
    return ConstructionResult(problem.problem_id, ConstructionStatus.OPTIMAL, weights, diagnostics)


# --------------------------------------------------------------------------- #
# The methods
# --------------------------------------------------------------------------- #


def _qp(
    problem: ConstructionProblem,
    compiled: _Compiled,
    scale: float,
    linear: Sequence[float],
    iterations: int,
    hessian: tuple[tuple[float, ...], ...] | None = None,
) -> _Outcome:
    solution = _solve(
        problem, scale, linear, compiled.linear, compiled.absolute, iterations, hessian
    )
    return _from_solution(solution, iterations)


def _qp_with_costs(
    problem: ConstructionProblem,
    compiled: _Compiled,
    scale: float,
    linear: Sequence[float],
    costs: LinearCosts,
    iterations: int,
    *,
    penalty: str = "Linear costs",
    side: str = "no trade in {asset} (not worth its cost)",
) -> _Outcome:
    """Minimize ``1/2 x'Gx + a'x + sum_i c_i |x_i - w0_i|`` exactly, one orthant at a time.

    The cost term is linear on each orthant around the current book: with
    signs ``s``, the problem is the quadratic program with linear term
    ``a + c * s`` and every charged asset held to its side of ``w0`` -- which
    the solver solves and certifies like any other. The orthant's optimum is
    the true optimum when the certificate also holds for the true problem: a
    side constraint that binds (``x_i = w0_i``, the asset not traded) with
    multiplier ``mu_i`` leaves the subgradient ``s_i (c_i - mu_i)``, inside
    ``[-c_i, c_i]`` exactly when ``mu_i <= 2 c_i``. Where one exceeds that,
    trading ``i`` the other way pays, and that asset's side is switched. The
    point just found lies on the new orthant too, so the objective never rises;
    no orthant is solved twice, so the method ends. It starts from the orthant
    of the cost-free optimum, where the answer usually already is. Should every
    switch a certificate asks for lead back to an orthant already solved -- a
    degenerate vertex whose multipliers are not unique -- the method says it
    could not certify rather than returning an uncertified portfolio.
    """

    universe = problem.universe
    settings = problem.settings
    size = len(universe)
    current = [costs.current[asset] for asset in universe]
    rates = [costs.rates[asset] for asset in universe]
    charged = [index for index in range(size) if rates[index] > 0.0]
    base = _qp(problem, compiled, scale, linear, iterations)
    if base.status is not ConstructionStatus.OPTIMAL or base.weights is None or not charged:
        # Costs change no constraint: what the cost-free problem cannot
        # satisfy, no orthant of it can -- and with nothing charged it is the
        # problem.
        return base
    signs = {index: 1.0 if base.weights[index] >= current[index] else -1.0 for index in charged}
    labels = {index: side.format(asset=universe[index]) for index in charged}
    used = base.iterations
    solved: set[tuple[float, ...]] = set()
    lowest = math.inf
    while True:
        solved.add(tuple(signs[index] for index in charged))
        solution = _solve(
            problem,
            scale,
            tuple(linear[i] + rates[i] * signs.get(i, 0.0) for i in range(size)),
            (
                *compiled.linear,
                *(
                    LinearConstraint(labels[i], ((i, signs[i]),), signs[i] * current[i], False)
                    for i in charged
                ),
            ),
            compiled.absolute,
            used,
        )
        outcome = _from_solution(solution, used)
        used = outcome.iterations
        if outcome.status is not ConstructionStatus.OPTIMAL or outcome.weights is None:
            return outcome
        value = _costed_objective(problem, scale, outcome.weights, linear, current, rates)
        if value > lowest + settings.convergence_tolerance * max(1.0, abs(lowest)):
            return _Outcome(
                ConstructionStatus.NUMERICAL_FAILURE,
                None,
                used,
                (),
                (),
                outcome.stationarity,
                f"Switching a side raised the costed objective from {lowest!r} to {value!r}, "
                "which exact arithmetic excludes: the solves disagree beyond tolerance.",
            )
        lowest = min(lowest, value)
        excesses = sorted(
            (-(solution.multipliers.get(labels[i], 0.0) - 2.0 * rates[i]), i)
            for i in charged
            if solution.multipliers.get(labels[i], 0.0) - 2.0 * rates[i]
            > settings.convergence_tolerance * max(1.0, 2.0 * rates[i])
        )
        if not excesses:
            return replace(
                outcome,
                detail=(
                    f"{outcome.detail} {penalty}: the orthant optimum meets the true "
                    f"subgradient condition, after {len(solved) - 1} side switch(es)."
                ),
            )
        switch = next(
            (
                i
                for _, i in excesses
                if tuple(-signs[j] if j == i else signs[j] for j in charged) not in solved
            ),
            None,
        )
        if switch is None:
            return _Outcome(
                ConstructionStatus.NUMERICAL_FAILURE,
                None,
                used,
                (),
                (),
                outcome.stationarity,
                f"{penalty}: every side switch the optimality certificate asks for leads "
                "to an orthant already solved -- a degenerate vertex -- so optimality "
                "could not be certified.",
            )
        if used >= settings.max_iterations:
            return _Outcome(
                ConstructionStatus.ITERATION_LIMIT,
                None,
                used,
                (),
                (),
                math.inf,
                "The cost-bearing optimum was not reached within the step budget.",
            )
        signs[switch] = -signs[switch]


def _costed_objective(
    problem: ConstructionProblem,
    scale: float,
    x: Sequence[float],
    linear: Sequence[float],
    current: Sequence[float],
    rates: Sequence[float],
) -> float:
    """``1/2 x'Gx + a'x + sum_i c_i |x_i - w0_i|``, ``G = scale * C``, summed exactly-rounded."""

    size = len(x)
    if _factor_structured(problem):
        product = _curvature(problem, scale).times(x)
        quadratic = [0.5 * x[i] * product[i] for i in range(size)]
    else:
        rows = _matrix(problem).values
        quadratic = [
            0.5 * x[i] * (scale * rows[i][j]) * x[j] for i in range(size) for j in range(size)
        ]
    return math.fsum(
        [
            *quadratic,
            *(linear[i] * x[i] for i in range(size)),
            *(rates[i] * abs(x[i] - current[i]) for i in range(size)),
        ]
    )


def _volatility(weights: Sequence[float], rows: Sequence[Sequence[float]]) -> float:
    count = len(weights)
    variance = math.fsum(
        weights[row] * rows[row][column] * weights[column]
        for row in range(count)
        for column in range(count)
    )
    return math.sqrt(max(0.0, variance))


def _cap(
    problem: ConstructionProblem,
    compiled: _Compiled,
    solve_at: Callable[[float, int], _Outcome],
    aversion: float,
) -> _Outcome:
    """Enforce ``max_volatility`` on a mean-variance-type objective by raising risk aversion.

    With a volatility cap ``sqrt(w'Cw) <= s``, the Lagrangian adds ``nu (w'Cw -
    s^2)``: the capped problem is the uncapped one at risk aversion ``lambda +
    2 nu``. Its volatility is non-increasing in risk aversion, so the smallest
    risk aversion at or above ``lambda`` whose solution meets the cap is found by
    bisection -- on a logarithmic scale, with geometric midpoints computed by
    square roots. If even the minimum-variance portfolio exceeds the cap, the cap
    cannot be met by any portfolio satisfying the other constraints.
    """

    settings = problem.settings
    cap = problem.constraints.max_volatility
    assert cap is not None
    size = len(problem.universe)
    tolerance = settings.feasibility_tolerance
    first = solve_at(aversion, 0)
    if first.status is not ConstructionStatus.OPTIMAL or first.weights is None:
        return first
    if _portfolio_volatility(problem, first.weights) <= cap + tolerance:
        return first

    floor = _qp(problem, compiled, 1.0, [0.0] * size, first.iterations)
    if floor.status is not ConstructionStatus.OPTIMAL or floor.weights is None:
        return floor
    lowest = _portfolio_volatility(problem, floor.weights)
    if lowest > cap + tolerance:
        return _Outcome(
            ConstructionStatus.INFEASIBLE,
            None,
            floor.iterations,
            (),
            (f"volatility <= {cap!r}", *floor.binding),
            floor.stationarity,
            f"The lowest volatility any portfolio meeting the other constraints can have is "
            f"{lowest:.6g}, above the cap {cap!r}.",
        )

    iterations = floor.iterations
    low = aversion
    high = aversion
    best = floor
    for _ in range(settings.max_iterations):
        high *= 2.0
        trial = solve_at(high, iterations)
        iterations = trial.iterations
        if trial.status is not ConstructionStatus.OPTIMAL or trial.weights is None:
            return trial
        if _portfolio_volatility(problem, trial.weights) <= cap + tolerance:
            best = trial
            break
        low = high
    else:
        return _Outcome(
            ConstructionStatus.ITERATION_LIMIT,
            None,
            iterations,
            (),
            (),
            math.inf,
            "The volatility cap could not be bracketed within the step budget.",
        )

    effective = high
    for _ in range(settings.max_iterations):
        assert best.weights is not None
        reached = _portfolio_volatility(problem, best.weights)
        if reached >= cap * (1.0 - settings.convergence_tolerance) or (
            high / low - 1.0 <= settings.convergence_tolerance
        ):
            return _Outcome(
                ConstructionStatus.OPTIMAL,
                best.weights,
                iterations,
                (*best.binding, f"volatility <= {cap!r}"),
                (),
                best.stationarity,
                f"Optimal under the volatility cap: found at risk aversion {effective:.6g}, "
                f"volatility {reached:.6g} against the cap {cap!r}.",
                effective_risk_aversion=effective,
                worst_case_return=best.worst_case_return,
            )
        middle = math.sqrt(low * high)
        trial = solve_at(middle, iterations)
        iterations = trial.iterations
        if trial.status is not ConstructionStatus.OPTIMAL or trial.weights is None:
            return trial
        if _portfolio_volatility(problem, trial.weights) <= cap + tolerance:
            high, best, effective = middle, trial, middle
        else:
            low = middle
    return _Outcome(
        ConstructionStatus.ITERATION_LIMIT,
        None,
        iterations,
        (),
        (),
        math.inf,
        "The volatility-capped risk aversion did not converge within the step budget.",
    )


def _returns_vector(problem: ConstructionProblem, returns: ExpectedReturns) -> list[float]:
    covariance = problem.covariance
    if returns.currency != covariance.currency or returns.period != covariance.period:
        raise ConstructionInputError(
            f"The expected returns are per {returns.period!r} in {returns.currency!r} and the "
            f"covariance is per {covariance.period!r} in {covariance.currency!r}. They are refused "
            "as a pair rather than rescaled or converted."
        )
    held = set(returns.values)
    universe = set(problem.universe)
    if held != universe:
        raise ConstructionInputError(
            f"Expected returns must cover exactly the universe: missing {sorted(universe - held)}, "
            f"outside {sorted(held - universe)}. A missing expected return is not zero."
        )
    return [returns.values[asset] for asset in problem.universe]


def _is_long_only(problem: ConstructionProblem) -> bool:
    """Whether every weight is bounded below by zero or more."""

    for asset in problem.universe:
        lower, _ = problem.constraints.bounds.for_asset(asset)
        if lower is None or lower < 0.0:
            return False
    return True


def _long_only(problem: ConstructionProblem, what: str) -> None:
    for asset in problem.universe:
        lower, _ = problem.constraints.bounds.for_asset(asset)
        if lower is None or lower < 0.0:
            raise ConstructionInputError(
                f"{what} is defined for long-only portfolios, and {asset!r} has lower bound "
                f"{lower!r}. Bound every weight below by zero or more."
            )


def _mean_variance(
    problem: ConstructionProblem, compiled: _Compiled, objective: MeanVariance
) -> _Outcome:
    returns = _returns_vector(problem, objective.expected_returns)
    linear = [-value for value in returns]
    costs = objective.costs
    if costs is not None and set(costs.current) != set(problem.universe):
        universe = set(problem.universe)
        raise ConstructionInputError(
            "LinearCosts must cover exactly the universe, zeros included: missing "
            f"{sorted(universe - set(costs.current))}, outside "
            f"{sorted(set(costs.current) - universe)}."
        )

    def solve_at(aversion: float, iterations: int) -> _Outcome:
        if costs is None:
            return _qp(problem, compiled, aversion, linear, iterations)
        return _qp_with_costs(problem, compiled, aversion, linear, costs, iterations)

    if problem.constraints.max_volatility is None:
        return solve_at(objective.risk_aversion, 0)
    return _cap(problem, compiled, solve_at, objective.risk_aversion)


def _robust(
    problem: ConstructionProblem, compiled: _Compiled, objective: RobustMeanVariance
) -> _Outcome:
    returns = _returns_vector(problem, objective.expected_returns)
    settings = problem.settings
    rows = _matrix(problem).values
    uncertainty = objective.uncertainty

    if isinstance(uncertainty, BoxUncertainty):
        if set(uncertainty.half_widths) != set(problem.universe):
            raise ConstructionInputError(
                "A box uncertainty set must give a half width for exactly the universe."
            )
        if not _is_long_only(problem):
            return _robust_box_with_shorts(problem, compiled, objective, returns, uncertainty)
        shifted = [
            -(value - uncertainty.half_widths[asset])
            for asset, value in zip(problem.universe, returns, strict=True)
        ]

        def solve_box(aversion: float, iterations: int) -> _Outcome:
            outcome = _qp(problem, compiled, aversion, shifted, iterations)
            if outcome.weights is None:
                return outcome
            worst = -math.fsum(
                value * weight for value, weight in zip(shifted, outcome.weights, strict=True)
            )
            return _with_worst_case(outcome, worst)

        if problem.constraints.max_volatility is None:
            return solve_box(objective.risk_aversion, 0)
        return _cap(problem, compiled, solve_box, objective.risk_aversion)

    omega = uncertainty.omega
    if omega.assets != problem.universe:
        raise ConstructionInputError(
            "The uncertainty covariance must be over exactly the construction universe."
        )
    if omega.currency != problem.covariance.currency or omega.period != problem.covariance.period:
        raise ConstructionInputError(
            "The uncertainty covariance must be in the covariance's currency and period."
        )
    try:
        omega.require_positive_definite("An ellipsoidal uncertainty set")
    except AnalyticsValidationError as error:
        raise ConstructionInputError(str(error)) from error
    radius = uncertainty.radius
    linear = [-value for value in returns]

    def solve_robust(aversion: float, iterations: int) -> _Outcome:
        """Alternate the QP in ``w`` with the closed form ``eta = sqrt(w' Omega w)``.

        ``sqrt(v) = min over eta > 0 of v / (2 eta) + eta / 2``, so the robust
        objective is the joint minimum over ``(w, eta)`` of a function convex in
        both; each block has a unique minimizer, so alternating them decreases
        the objective monotonically to the optimum. Stops when ``eta`` moves by
        at most the convergence tolerance, relatively.
        """

        outcome = _qp(problem, compiled, aversion, linear, iterations)
        if outcome.weights is None or radius == 0.0:
            return _robust_outcome(outcome, returns, omega.values, radius)
        eta = _volatility(outcome.weights, omega.values)
        for _ in range(settings.max_iterations):
            if eta == 0.0:
                return _robust_outcome(outcome, returns, omega.values, radius)
            scale = radius / eta
            hessian = tuple(
                tuple(
                    aversion * rows[row][column] + scale * omega.values[row][column]
                    for column in range(len(rows))
                )
                for row in range(len(rows))
            )
            outcome = _qp(
                problem, compiled, aversion, linear, outcome.iterations + 1, hessian=hessian
            )
            if outcome.weights is None:
                return outcome
            updated = _volatility(outcome.weights, omega.values)
            if abs(updated - eta) <= settings.convergence_tolerance * eta:
                return _robust_outcome(outcome, returns, omega.values, radius)
            eta = updated
        return _Outcome(
            ConstructionStatus.ITERATION_LIMIT,
            None,
            outcome.iterations,
            (),
            (),
            math.inf,
            "The robust alternation did not converge within the step budget.",
        )

    if problem.constraints.max_volatility is None:
        return solve_robust(objective.risk_aversion, 0)
    return _cap(problem, compiled, solve_robust, objective.risk_aversion)


def _robust_box_with_shorts(
    problem: ConstructionProblem,
    compiled: _Compiled,
    objective: RobustMeanVariance,
    returns: Sequence[float],
    uncertainty: BoxUncertainty,
) -> _Outcome:
    """The box's robust counterpart for a portfolio that may short (v3.13).

    ``min over mu in the box of mu' w`` is ``mu_hat' w - half_width' |w|``, so
    the robust problem minimizes ``(lambda / 2) w' C w - mu_hat' w +
    half_width' |w|`` -- the costed problem of :class:`LinearCosts` with the
    held weights at zero, solved and certified by the same method.
    """

    universe = problem.universe
    linear = [-value for value in returns]
    widths = [uncertainty.half_widths[asset] for asset in universe]
    penalty = LinearCosts(current=dict.fromkeys(universe, 0.0), rates=dict(uncertainty.half_widths))

    def solve_box(aversion: float, iterations: int) -> _Outcome:
        outcome = _qp_with_costs(
            problem,
            compiled,
            aversion,
            linear,
            penalty,
            iterations,
            penalty="Box uncertainty (an L1 penalty on the weights)",
            side="no position in {asset} (its return too uncertain to hold either way)",
        )
        if outcome.weights is None:
            return outcome
        held = outcome.weights
        worst = math.fsum(
            [
                *(value * weight for value, weight in zip(returns, held, strict=True)),
                *(-width * abs(weight) for width, weight in zip(widths, held, strict=True)),
            ]
        )
        return _with_worst_case(outcome, worst)

    if problem.constraints.max_volatility is None:
        return solve_box(objective.risk_aversion, 0)
    return _cap(problem, compiled, solve_box, objective.risk_aversion)


def _with_worst_case(outcome: _Outcome, worst: float) -> _Outcome:
    return _Outcome(
        outcome.status,
        outcome.weights,
        outcome.iterations,
        outcome.binding,
        outcome.conflict,
        outcome.stationarity,
        outcome.detail,
        outcome.budget_deviation,
        outcome.effective_risk_aversion,
        worst,
    )


def _robust_outcome(
    outcome: _Outcome,
    returns: Sequence[float],
    omega: Sequence[Sequence[float]],
    radius: float,
) -> _Outcome:
    if outcome.weights is None:
        return outcome
    nominal = math.fsum(
        value * weight for value, weight in zip(returns, outcome.weights, strict=True)
    )
    return _with_worst_case(outcome, nominal - radius * _volatility(outcome.weights, omega))


def _maximum_diversification(problem: ConstructionProblem, compiled: _Compiled) -> _Outcome:
    constraints = problem.constraints
    _long_only(problem, "Maximum diversification")
    if constraints.turnover is not None or constraints.max_volatility is not None:
        raise ConstructionInputError(
            "Maximum diversification is solved through y = w / (sigma' w), under which only "
            "constraints invariant to rescaling w are linear. A turnover limit and a volatility "
            "cap are not, and are refused rather than approximated."
        )
    if not constraints.net_exposure.is_exact or not (constraints.net_exposure.lower or 0.0) > 0.0:
        raise ConstructionInputError(
            "Maximum diversification needs an exact, positive net exposure (a long-only budget)."
        )
    budget = constraints.net_exposure.lower
    assert budget is not None
    universe = problem.universe
    count = len(universe)
    sigma = [problem.covariance.volatility(asset) for asset in universe]

    if constraints.max_gross_exposure is not None and budget > constraints.max_gross_exposure:
        return _Outcome(
            ConstructionStatus.INFEASIBLE,
            None,
            0,
            (),
            ("net exposure", f"gross exposure <= {constraints.max_gross_exposure!r}"),
            0.0,
            "A long-only portfolio's gross exposure is its net exposure, which exceeds the cap.",
        )

    homogenized: list[LinearConstraint] = [
        LinearConstraint(
            "diversification normalization: sigma' y = 1",
            tuple((index, sigma[index]) for index in range(count)),
            1.0,
            True,
        )
    ]
    for constraint in compiled.linear:
        if constraint.label.startswith("net exposure"):
            continue
        # A constraint terms.w (= | >=) b, with w = B y / (1'y), reads
        # B terms.y - b 1'y (= | >=) 0: linear, homogeneous, and exact.
        coefficients = [0.0] * count
        for index, value in constraint.terms:
            coefficients[index] += budget * value
        for index in range(count):
            coefficients[index] -= constraint.bound
        terms = tuple((index, value) for index, value in enumerate(coefficients) if value != 0.0)
        if not terms:
            continue
        homogenized.append(LinearConstraint(constraint.label, terms, 0.0, constraint.equality))
    # No explicit y >= 0 is needed: every lower bound is at least zero (checked
    # above), and each homogenizes to B y_i >= lower * 1'y >= 0.

    solution = _solve(problem, 1.0, [0.0] * count, tuple(homogenized), (), 0)
    outcome = _from_solution(solution, 0)
    if solution.x is None:
        return outcome
    total = math.fsum(solution.x)
    weights = tuple(budget * value / total for value in solution.x)
    return _Outcome(
        outcome.status,
        weights,
        outcome.iterations,
        tuple(label for label in outcome.binding if not label.startswith("diversification")),
        (),
        outcome.stationarity,
        outcome.detail,
    )


def _risk_parity(
    problem: ConstructionProblem, compiled: _Compiled, objective: RiskParity
) -> _Outcome:
    constraints = problem.constraints
    universe = problem.universe
    if not constraints.net_exposure.is_exact or not (constraints.net_exposure.lower or 0.0) > 0.0:
        raise ConstructionInputError(
            "Risk parity needs an exact, positive net exposure: the budgets are shares of a long "
            "portfolio's risk."
        )
    budget = constraints.net_exposure.lower
    assert budget is not None
    if objective.budgets is None:
        budgets = [1.0 / len(universe)] * len(universe)
    else:
        if set(objective.budgets) != set(universe):
            raise ConstructionInputError(
                "Risk budgets must name exactly the universe: missing "
                f"{sorted(set(universe) - set(objective.budgets))}, outside "
                f"{sorted(set(objective.budgets) - set(universe))}."
            )
        budgets = [float(objective.budgets[asset]) for asset in universe]
    settings = problem.settings
    iterate = solve_risk_budgets(
        _matrix(problem).values,
        budgets,
        convergence_tolerance=settings.convergence_tolerance,
        max_sweeps=settings.max_iterations,
    )
    if not iterate.converged:
        return _Outcome(
            ConstructionStatus.ITERATION_LIMIT,
            None,
            iterate.sweeps,
            (),
            (),
            iterate.max_deviation,
            f"Coordinate descent reached {iterate.sweeps} sweeps with a largest budget deviation "
            f"of {iterate.max_deviation:.3g}, above the tolerance.",
            budget_deviation=iterate.max_deviation,
        )
    weights = tuple(budget * value for value in iterate.weights)
    residuals = constraint_residuals(_Constraints(compiled.linear, compiled.absolute), weights)
    unmet = [
        label for label, residual in residuals.items() if residual > settings.feasibility_tolerance
    ]
    cap = constraints.max_volatility
    if (
        cap is not None
        and _volatility(weights, _matrix(problem).values) > cap + settings.feasibility_tolerance
    ):
        unmet.append(f"volatility <= {cap!r}")
    if unmet:
        return _Outcome(
            ConstructionStatus.INFEASIBLE,
            None,
            iterate.sweeps,
            (),
            ("risk budgets", *unmet),
            iterate.max_deviation,
            "The portfolio with these risk budgets is unique, and it does not meet the named "
            "constraints; no portfolio with these budgets can.",
            budget_deviation=iterate.max_deviation,
        )
    return _Outcome(
        ConstructionStatus.OPTIMAL,
        weights,
        iterate.sweeps,
        (),
        (),
        iterate.max_deviation,
        f"Converged in {iterate.sweeps} sweeps: every risk share within "
        f"{iterate.max_deviation:.3g} of its budget.",
        budget_deviation=iterate.max_deviation,
    )


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def construct(problem: ConstructionProblem) -> ConstructionResult:
    """Solve a construction problem and certify the answer. See the module docstring.

    A linear objective's weights must each have a finite range the
    constraints imply -- a weight bound, a concentration or notional limit, or
    the range ``c_k - L <= w_k <= c_k + L`` a gross-exposure or turnover limit
    implies -- since nothing else bounds its optimum (ADR-0063).

    Raises:
        ConstructionInputError: If the problem is malformed: a covariance that
            is not positive definite, inputs that do not cover exactly the
            universe or disagree with the covariance's currency or period, a
            constraint the objective cannot express, or since v4.3 an objective
            of the protocol whose class no certified method takes, a linear
            objective with a weight no constraint bounds or under a volatility
            cap (ADR-0063).
    """

    # An objective of the protocol is admitted, or refused, before anything is solved.
    admitted = None if isinstance(problem.objective, _SHIPPED) else _admitted(problem)
    linear = _is_linear(problem.objective)
    if linear and problem.constraints.max_volatility is not None:
        raise ConstructionInputError(
            f"A volatility cap under {type(problem.objective).__name__} has no certified method: "
            "the cap is quadratic, and the linear program takes linear rows only (ADR-0063)."
        )
    if problem.constraints.limits and not linear:
        raise ConstructionInputError(
            f"A limit under {type(problem.objective).__name__} has no certified method: only a "
            "linear objective takes one. The dense quadratic method needs strict convexity in "
            "every variable, and a lifting's variables have none (ADR-0063 section 4)."
        )

    # A structure under a method that reads the dense values -- risk parity, a
    # universe too small for the structured method, a structure that cannot
    # establish definiteness by itself -- is written out once, here, and the
    # problem solved over the matrix it implies. Its result is the problem's
    # as stated: its identity, and diagnostics through the factors (PRF-013).
    # A linear objective reads no curvature, so its structure is kept whenever
    # the structured method would have read it (v4.3).
    stated = problem
    through = _through_factors(problem) if linear else _factor_structured(problem)
    if isinstance(problem.covariance, FactorStructure) and not through:
        problem = replace(problem, covariance=problem.covariance.matrix())
    covariance = problem.covariance
    factors = _structure(problem)
    structured = factors.definiteness() if factors is not None and through else None
    if structured is not None:
        # Established from the factor structure in O(n k^2): every specific
        # variance positive and the factor covariance positive semidefinite.
        evidence = structured
    else:
        try:
            evidence = _matrix(problem).require_positive_definite(
                f"{type(problem.objective).__name__} construction"
            )
        except AnalyticsValidationError as error:
            raise ConstructionInputError(str(error)) from error
    _require_universe(problem)
    compiled = _compile(problem)
    objective = problem.objective
    method = type(objective).__name__

    expected: ExpectedReturns | None = None
    if isinstance(objective, MeanVariance | RobustMeanVariance | MaximumReturn):
        expected = objective.expected_returns

    if compiled.unmet:
        outcome = _Outcome(
            ConstructionStatus.INFEASIBLE,
            None,
            0,
            (),
            compiled.unmet,
            0.0,
            "A constraint reaches no asset in the universe and cannot hold at zero.",
        )
        return _result(stated, method, outcome, compiled, evidence.pivot_ratio, expected)

    if isinstance(objective, MinimumVariance):
        outcome = _qp(problem, compiled, 1.0, [0.0] * len(covariance.assets), 0)
        cap = problem.constraints.max_volatility
        if outcome.weights is not None and cap is not None:
            lowest = _portfolio_volatility(problem, outcome.weights)
            if lowest > cap + problem.settings.feasibility_tolerance:
                outcome = _Outcome(
                    ConstructionStatus.INFEASIBLE,
                    None,
                    outcome.iterations,
                    (),
                    (f"volatility <= {cap!r}", *outcome.binding),
                    outcome.stationarity,
                    f"The minimum-variance portfolio under the other constraints has volatility "
                    f"{lowest:.6g}, above the cap {cap!r}: no portfolio meets all of them.",
                )
    elif isinstance(objective, MeanVariance):
        outcome = _mean_variance(problem, compiled, objective)
    elif isinstance(objective, RobustMeanVariance):
        outcome = _robust(problem, compiled, objective)
    elif isinstance(objective, MaximumDiversification):
        outcome = _maximum_diversification(problem, compiled)
    elif isinstance(objective, RiskParity):
        outcome = _risk_parity(problem, compiled, objective)
    elif isinstance(objective, MaximumReturn):
        outcome = _maximum_return(problem, compiled, objective)
    elif isinstance(objective, MinimumCVaR):
        outcome = _linear(problem, compiled, objective.linear_form(problem), None, method)
    else:
        assert admitted is not None  # every objective not the package's was admitted above
        outcome = admitted(problem, compiled)
    return _result(stated, method, outcome, compiled, evidence.pivot_ratio, expected)


def _admitted(problem: ConstructionProblem) -> Callable[[ConstructionProblem, _Compiled], _Outcome]:
    """The certified method that solves an objective of the protocol, or a refusal (ADR-0063).

    Raises:
        ConstructionInputError: If the objective states no class or rendering, or a
            class no certified method takes: ``NONCONVEX`` always, and ``CONVEX``,
            which only the package's own objectives have methods for. Also a
            ``LINEAR`` one that states no linear form, a ``CONVEX_QUADRATIC`` one
            that states no quadratic form, or a quadratic one under a volatility
            cap.
    """

    objective = problem.objective
    name = type(objective).__name__
    if not isinstance(objective, Objective) or not isinstance(objective.convexity, Convexity):
        raise ConstructionInputError(
            f"{name} is not a construction objective: it states no Convexity and no "
            "rendering (ADR-0063)."
        )
    kind = objective.convexity
    if kind is Convexity.NONCONVEX:
        raise ConstructionInputError(
            f"{name} is NONCONVEX: no certified method minimizes a non-convex objective, so "
            "construction refuses it rather than return an answer it cannot certify."
        )
    if kind is Convexity.CONVEX:
        raise ConstructionInputError(
            f"{name} is CONVEX, and only the package's own convex objectives -- risk parity, "
            "maximum diversification, the robust objective -- have methods of their own: state "
            "it as CONVEX_QUADRATIC with its QuadraticForm."
        )
    if kind is Convexity.LINEAR:
        if not isinstance(objective, LinearObjective):
            raise ConstructionInputError(f"{name} is LINEAR but states no linear_form.")
        return partial(_protocol_linear, objective=objective)
    if not isinstance(objective, QuadraticObjective):
        raise ConstructionInputError(f"{name} is CONVEX_QUADRATIC but states no quadratic_form.")
    if problem.constraints.max_volatility is not None:
        raise ConstructionInputError(
            f"A volatility cap under {name} has no certified method: only the package's "
            "minimum-variance and mean-variance objectives take one."
        )
    return partial(_protocol_quadratic, objective=objective)


def _protocol_quadratic(
    problem: ConstructionProblem, compiled: _Compiled, objective: QuadraticObjective
) -> _Outcome:
    """A ``CONVEX_QUADRATIC`` objective of the protocol, by the certified quadratic methods."""

    form = objective.quadratic_form(problem)
    name = type(objective).__name__
    if not isinstance(form, QuadraticForm):
        raise ConstructionInputError(
            f"{name}.quadratic_form returned {form!r}, not a QuadraticForm."
        )
    universe = problem.universe
    if set(form.linear) != set(universe):
        raise ConstructionInputError(
            f"{name}'s QuadraticForm must cover exactly the universe: missing "
            f"{sorted(set(universe) - set(form.linear))}, outside "
            f"{sorted(set(form.linear) - set(universe))}."
        )
    return _qp(problem, compiled, form.scale, [form.linear[asset] for asset in universe], 0)


# --------------------------------------------------------------------------- #
# The linear method (v4.3, ADR-0063)
# --------------------------------------------------------------------------- #


def _protocol_linear(
    problem: ConstructionProblem, compiled: _Compiled, objective: LinearObjective
) -> _Outcome:
    """A ``LINEAR`` objective of the protocol, by the certified linear program."""

    form = objective.linear_form(problem)
    name = type(objective).__name__
    if not isinstance(form, LinearForm):
        raise ConstructionInputError(f"{name}.linear_form returned {form!r}, not a LinearForm.")
    return _linear(problem, compiled, form, None, name)


def _maximum_return(
    problem: ConstructionProblem, compiled: _Compiled, objective: MaximumReturn
) -> _Outcome:
    costs = objective.costs
    if costs is not None and set(costs.current) != set(problem.universe):
        universe = set(problem.universe)
        raise ConstructionInputError(
            "LinearCosts must cover exactly the universe, zeros included: missing "
            f"{sorted(universe - set(costs.current))}, outside "
            f"{sorted(set(costs.current) - universe)}."
        )
    return _linear(problem, compiled, objective.linear_form(problem), costs, "MaximumReturn")


def weight_ranges(problem: ConstructionProblem) -> Mapping[str, tuple[float, float]]:
    """Each weight's range, as the constraints on it alone imply it (v4.3, ADR-0063).

    A weight bound, a concentration or notional limit, a one-asset group, and
    the range ``c_k - L <= w_k <= c_k + L`` that a gross-exposure or turnover
    limit implies: the ranges the linear program holds every weight to. A
    lifting states its own variables' ranges from them -- a scenario's loss lies
    between the least and the most ``-r' w`` reaches over them, say. A range
    whose lower end is above its upper means the constraints cannot hold.

    Raises:
        ConstructionInputError: If the problem names assets outside its
            universe, or leaves a weight without a finite range.
    """

    _require_universe(problem)
    box = _box(problem, _compile(problem), type(problem.objective).__name__)
    return MappingProxyType(
        {
            asset: (box.lower[index], box.upper[index])
            for index, asset in enumerate(problem.universe)
        }
    )


@dataclass(frozen=True, slots=True)
class _Box:
    """Each weight's range as the constraints on it alone imply it, and what states each side."""

    lower: tuple[float, ...]
    upper: tuple[float, ...]
    lower_label: tuple[str, ...]
    upper_label: tuple[str, ...]
    #: The compiled constraints that are not one weight's range: the program's rows.
    rows: tuple[LinearConstraint, ...]


def _box(problem: ConstructionProblem, compiled: _Compiled, name: str) -> _Box:
    """Fold each constraint on one weight into its range; derive ranges from absolute sums.

    A constraint on one weight with coefficient one -- a weight bound, a
    concentration or notional limit, a one-asset group -- is that weight's
    range, so it leaves the rows. An absolute-sum limit ``sum |w_k - c_k| <= L``
    implies ``c_k - L <= w_k <= c_k + L``. Each side keeps the label of the
    constraint that states it, the first stated one on a tie: every range is
    one the problem implies, none is supplied (AC-12).

    Raises:
        ConstructionInputError: If a weight is left without a finite range: the
            linear objective's optimum would not be bounded by the problem.
    """

    universe = problem.universe
    size = len(universe)
    lower: list[float | None] = [None] * size
    upper: list[float | None] = [None] * size
    lower_label = [""] * size
    upper_label = [""] * size
    rows: list[LinearConstraint] = []

    def tighten(index: int, low: float | None, high: float | None, label: str) -> None:
        current_low, current_high = lower[index], upper[index]
        if low is not None and (current_low is None or low > current_low):
            lower[index], lower_label[index] = low, label
        if high is not None and (current_high is None or high < current_high):
            upper[index], upper_label[index] = high, label

    for constraint in compiled.linear:
        if len(constraint.terms) != 1 or abs(constraint.terms[0][1]) != 1.0:
            rows.append(constraint)
            continue
        index, sign = constraint.terms[0]
        # sign * w >= bound: a lower bound, or an upper one of -bound (written so
        # that a zero bound stays +0.0).
        value = constraint.bound if sign > 0.0 else 0.0 - constraint.bound
        if constraint.equality:
            tighten(index, value, value, constraint.label)
        elif sign > 0.0:
            tighten(index, value, None, constraint.label)
        else:
            tighten(index, None, value, constraint.label)
    for family in compiled.absolute:
        for index, center in zip(family.indices, family.centers, strict=True):
            tighten(index, center - family.limit, center + family.limit, family.label)

    loose = [
        asset
        for asset, low, high in zip(universe, lower, upper, strict=True)
        if low is None or high is None
    ]
    if loose:
        raise ConstructionInputError(
            f"The constraints leave {loose} without a finite range, and only they bound "
            f"{name}'s linear program. Bound every weight -- WeightBounds, a concentration or "
            "notional limit, or a gross-exposure or turnover limit (ADR-0063)."
        )
    return _Box(
        tuple(low for low in lower if low is not None),
        tuple(high for high in upper if high is not None),
        tuple(lower_label),
        tuple(upper_label),
        tuple(rows),
    )


def _linear(
    problem: ConstructionProblem,
    compiled: _Compiled,
    form: LinearForm,
    costs: LinearCosts | None,
    name: str,
) -> _Outcome:
    """A linear objective over the problem's constraints, by the certified linear program.

    The variables are the weights, then the form's, then two per absolute-sum
    term and per costed asset: ``w_k - c_k = p_k - q_k``, with ``p, q >= 0``. A
    limit bounds ``sum (p_k + q_k)``, which is at least ``sum |w_k - c_k|`` and
    equal to it for the least split, so the limit holds exactly as stated; a
    cost charges ``rate_k (p_k + q_k)``, which the optimum makes the least split.
    """

    universe = problem.universe
    size = len(universe)
    if set(form.costs) != set(universe):
        raise ConstructionInputError(
            f"{name}'s LinearForm must cost exactly the universe: missing "
            f"{sorted(set(universe) - set(form.costs))}, outside "
            f"{sorted(set(form.costs) - set(universe))}."
        )
    for row in form.rows:
        outside = sorted(set(row.weights) - set(universe))
        if outside:
            raise ConstructionInputError(
                f"{name}'s LinearRow {row.label!r} weights assets outside the universe: {outside}."
            )
    box = _box(problem, compiled, name)
    crossed = [index for index in range(size) if box.lower[index] > box.upper[index]]
    if crossed:
        return _Outcome(
            ConstructionStatus.INFEASIBLE,
            None,
            0,
            (),
            tuple(
                dict.fromkeys(
                    label
                    for index in crossed
                    for label in (box.lower_label[index], box.upper_label[index])
                )
            ),
            math.inf,
            "A weight's range is empty: the constraints named bound it from both sides, and "
            "they cross.",
        )

    position = {asset: index for index, asset in enumerate(universe)}
    lower = list(box.lower)
    upper = list(box.upper)
    objective = [form.costs[asset] for asset in universe]
    # What states each variable's lower and upper bound, and each row, for naming a
    # conflict: a limit's rows and variables are named by the limit.
    bounds = list(zip(box.lower_label, box.upper_label, strict=True))
    rows = list(box.rows)
    reported = [constraint.label for constraint in rows]

    def variable(low: float, high: float, cost: float, labels: tuple[str, str]) -> int:
        lower.append(low)
        upper.append(high)
        objective.append(cost)
        bounds.append(labels)
        return len(lower) - 1

    def add(built: Sequence[LinearConstraint], report: str | None = None) -> None:
        rows.extend(built)
        reported.extend(constraint.label if report is None else report for constraint in built)

    named = {
        item.name: variable(
            item.lower,
            item.upper,
            item.cost,
            (
                f"objective[{item.name}] >= {item.lower!r}",
                f"objective[{item.name}] <= {item.upper!r}",
            ),
        )
        for item in form.variables
    }
    for row in form.rows:
        terms = sorted(
            [(position[asset], value) for asset, value in row.weights.items()]
            + [(named[item], value) for item, value in row.variables.items()]
        )
        add(_interval(f"objective[{row.label}]", terms, row.lower, row.upper))
    for limit in problem.constraints.limits:
        lifting = limit.lifting(problem)
        if not isinstance(lifting, Lifting):
            raise ConstructionInputError(
                f"The limit {limit.label!r} gave {lifting!r} as its lifting, not a Lifting."
            )
        for row in lifting.rows:
            outside = sorted(set(row.weights) - set(universe))
            if outside:
                raise ConstructionInputError(
                    f"The limit {limit.label!r}'s row {row.label!r} weights assets outside the "
                    f"universe: {outside}."
                )
        local = {
            item.name: variable(item.lower, item.upper, 0.0, (limit.label, limit.label))
            for item in lifting.variables
        }
        for row in lifting.rows:
            terms = sorted(
                [(position[asset], value) for asset, value in row.weights.items()]
                + [(local[item], value) for item, value in row.variables.items()]
            )
            add(_interval(f"{limit.label}: {row.label}", terms, row.lower, row.upper), limit.label)
    for family in compiled.absolute:
        split: list[int] = []
        for index, center in zip(family.indices, family.centers, strict=True):
            plus = variable(0.0, family.limit, 0.0, (family.label, family.label))
            minus = variable(0.0, family.limit, 0.0, (family.label, family.label))
            add(
                [
                    LinearConstraint(
                        family.label, ((index, 1.0), (plus, -1.0), (minus, 1.0)), center, True
                    )
                ]
            )
            split.extend((plus, minus))
        add([LinearConstraint(family.label, tuple((k, -1.0) for k in split), -family.limit, False)])
    if costs is not None:
        for index, asset in enumerate(universe):
            rate = costs.rates[asset]
            if rate == 0.0:
                continue
            center = costs.current[asset]
            label = f"costs[{asset}]"
            # The most each side of the trade can be, from the weight's own range.
            plus = variable(0.0, max(box.upper[index] - center, 0.0), rate, (label, label))
            minus = variable(0.0, max(center - box.lower[index], 0.0), rate, (label, label))
            add([LinearConstraint(label, ((index, 1.0), (plus, -1.0), (minus, 1.0)), center, True)])

    program = LinearProgram(tuple(objective), tuple(rows), tuple(lower), tuple(upper))
    settings = problem.settings
    solution = solve_linear_program(
        program,
        feasibility_tolerance=settings.feasibility_tolerance,
        convergence_tolerance=settings.convergence_tolerance,
        max_iterations=settings.max_iterations,
    )
    if solution.status is SolveStatus.OPTIMAL and solution.x is not None:
        weights = solution.x[:size]
        return _Outcome(
            ConstructionStatus.OPTIMAL,
            weights,
            solution.iterations,
            _linear_binding(problem, compiled, weights),
            (),
            solution.gap,
            solution.detail,
        )
    conflict = (
        _linear_conflict(program, solution, bounds, reported)
        if solution.status is SolveStatus.INFEASIBLE
        else ()
    )
    return _Outcome(
        _STATUS[solution.status],
        None,
        solution.iterations,
        (),
        conflict,
        math.inf,
        solution.detail,
    )


def _linear_binding(
    problem: ConstructionProblem, compiled: _Compiled, weights: Sequence[float]
) -> tuple[str, ...]:
    """The constraints active at a linear answer: each equality, and each one met to tolerance."""

    tolerance = problem.settings.feasibility_tolerance
    active: list[str] = []
    for constraint in compiled.linear:
        slack = math.fsum(a * weights[k] for k, a in constraint.terms) - constraint.bound
        if constraint.equality or slack <= tolerance:
            active.append(constraint.label)
    for family in compiled.absolute:
        used = math.fsum(
            abs(weights[k] - center)
            for k, center in zip(family.indices, family.centers, strict=True)
        )
        if family.limit - used <= tolerance:
            active.append(family.label)
    return tuple(dict.fromkeys(active))


def _linear_conflict(
    program: LinearProgram,
    solution: LinearSolution,
    bounds: Sequence[tuple[str, str]],
    reported: Sequence[str],
) -> tuple[str, ...]:
    """What a Farkas certificate rests on: the rows it weights, and the bounds it reaches.

    For multipliers ``y``, the certificate compares the rows' bounds with the
    most ``y' A x`` reaches over the variables' ranges: each variable at its
    upper bound where ``(A' y)_j`` is positive, at its lower where negative.
    Those bounds are part of the conflict as much as the rows are.
    """

    weights = solution.multipliers
    # Relative to the certificate's largest weight, as the solver names its rows.
    threshold = 1e-6 * max((abs(value) for value in weights), default=0.0)
    labels = [
        label for label, value in zip(reported, weights, strict=True) if abs(value) > threshold
    ]
    reach = [0.0] * len(program.costs)
    for constraint, value in zip(program.constraints, weights, strict=True):
        if value != 0.0:
            for k, a in constraint.terms:
                reach[k] += value * a
    for (low, high), value in zip(bounds, reach, strict=True):
        if value > threshold:
            labels.append(high)
        elif value < -threshold:
            labels.append(low)
    return tuple(dict.fromkeys(labels))
