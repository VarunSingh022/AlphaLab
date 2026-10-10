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

What construction does not solve, by design: **cardinality** (at most ``k``
names) and **joint lot selection** are integer programs, and no integer
solver is part of this library -- rounding is per asset, toward zero, and
reported; **CVaR, drawdown and other scenario objectives** need a linear
program over scenarios, which this quadratic solver is not; and
**multi-period** construction (trading a path of books against a cost of
getting there) is a sequence of single-period problems the caller composes.
Each is an explicit boundary, not a limitation discovered later.

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
from types import MappingProxyType
from typing import Final

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
    "BoxUncertainty",
    "ConstraintSet",
    "ConstructionDiagnostics",
    "ConstructionObjective",
    "ConstructionProblem",
    "ConstructionResult",
    "ConstructionStatus",
    "EllipsoidalUncertainty",
    "ExpectedReturns",
    "ExposureRange",
    "FactorBound",
    "GroupBound",
    "LinearCosts",
    "MaximumDiversification",
    "MeanVariance",
    "MinimumVariance",
    "NotionalLimits",
    "RiskParity",
    "RobustMeanVariance",
    "SolverSettings",
    "TurnoverLimit",
    "WeightBounds",
    "construct",
]

#: Version 2 (v3.11, ledger DET-006): money and risk budgets -- the ``Decimal``
#: inputs -- are rendered by value, so ``100`` and ``100.00`` state one problem.
CONSTRUCTION_PROBLEM_SCHEME: Final = "alphalab.construction_problem.v2"
CONSTRUCTION_RESULT_SCHEME: Final = "alphalab.construction_result.v1"
EXPECTED_RETURNS_SCHEME: Final = "alphalab.expected_returns.v1"

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
            the robustness weight, the relative gap to a volatility cap. In
            ``(0, 1)``.
        max_iterations: The step budget: add-or-drop steps of the quadratic
            solver, sweeps of risk parity, outer iterations of the robust and
            volatility-cap searches. Exhausting it is ``ITERATION_LIMIT``, never
            a best-effort answer.
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
    objective: each unit of weight traded in asset ``i`` costs ``rates_i`` of
    return, in the covariance's currency and period -- so a one-way cost of ten
    basis points of notional, expected to be paid once over a holding period of
    a year while returns are daily, is ``0.001 / 252``: amortizing it is the
    caller's statement, not an assumption made here. The optimum trades an
    asset only where the improvement pays its cost, and leaves it exactly where
    it is otherwise -- a no-trade region a penalty-free optimum does not have.

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
        return lines


# --------------------------------------------------------------------------- #
# Objectives
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MinimumVariance:
    """Minimize ``w' C w``. No expected return is read."""

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
            construction that trades for free, as every one did before. Only a
            mean-variance objective takes costs: they are in return units, and
            no other objective here has a scale they could be traded against.
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

    def rendering(self) -> list[str]:
        return [
            "objective=robust_mean_variance",
            f"expected_returns={self.expected_returns.returns_id}",
            f"risk_aversion={_render(self.risk_aversion)}",
            *self.uncertainty.rendering(),
        ]


type ConstructionObjective = (
    MinimumVariance | MeanVariance | MaximumDiversification | RiskParity | RobustMeanVariance
)


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
        objective: What is optimized.
        constraints: What must hold.
        settings: The numerical contract.
    """

    covariance: CovarianceMatrix | FactorStructure
    objective: ConstructionObjective
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
                *self.objective.rendering(),
                *self.constraints.rendering(),
                f"feasibility_tolerance={_render(self.settings.feasibility_tolerance)}",
                f"convergence_tolerance={_render(self.settings.convergence_tolerance)}",
                f"max_iterations={_render(self.settings.max_iterations)}",
                # v3.12 (PRF-005): the two methods agree within the tolerances, not
                # to the bit, so which one solves a problem is part of what it is.
                # Present only when the structured method applies, so every other
                # problem keeps the identity it had.
                *(["solver=factor-structured"] if _factor_structured(self) else []),
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
            parity sweeps, and the outer iterations of a robust or
            volatility-capped search, summed.
        binding: Labels of the constraints active at the solution.
        conflict: For ``INFEASIBLE``: the constraints that jointly cannot hold.
        max_violation: The largest constraint residual of the reported (or
            last) point, in each constraint's own units.
        stationarity: The method's convergence measure at the end (see
            :class:`SolverSettings`).
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
            objective charged :class:`LinearCosts`, or ``None`` (v3.11).
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


def _factor_structured(problem: ConstructionProblem) -> bool:
    """Whether the factor-structured method solves ``problem``'s programs (ledger PRF-005).

    When the covariance is or carries a factor structure, the structure
    establishes positive definiteness by itself, the universe has at least
    :data:`FACTOR_STRUCTURED_MINIMUM_ASSETS` assets, and the objective is solved
    by quadratic programs -- risk parity is not.
    """

    factors = _structure(problem)
    return (
        factors is not None
        and not isinstance(problem.objective, RiskParity)
        and len(problem.covariance.assets) >= FACTOR_STRUCTURED_MINIMUM_ASSETS
        and factors.definiteness() is not None
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
        problem.objective.costs if isinstance(problem.objective, MeanVariance) else None
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
        binding=outcome.binding,
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

    Raises:
        ConstructionInputError: If the problem is malformed: a covariance that
            is not positive definite, inputs that do not cover exactly the
            universe or disagree with the covariance's currency or period, or a
            constraint the objective cannot express.
    """

    # A structure under a method that reads the dense values -- risk parity, a
    # universe too small for the structured method, a structure that cannot
    # establish definiteness by itself -- is written out once, here, and the
    # problem solved over the matrix it implies. Its result is the problem's
    # as stated: its identity, and diagnostics through the factors (PRF-013).
    stated = problem
    if isinstance(problem.covariance, FactorStructure) and not _factor_structured(problem):
        problem = replace(problem, covariance=problem.covariance.matrix())
    covariance = problem.covariance
    factors = _structure(problem)
    structured = (
        factors.definiteness() if factors is not None and _factor_structured(problem) else None
    )
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
    if isinstance(objective, MeanVariance | RobustMeanVariance):
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
    else:
        outcome = _risk_parity(problem, compiled, objective)
    return _result(stated, method, outcome, compiled, evidence.pivot_ratio, expected)
