"""Black-Litterman: a prior, stated views with stated confidence, and the posterior.

Black-Litterman is a *model* of expected returns, not an optimizer, and this
module keeps every one of its parts a separate, named input (Black and
Litterman, 1992; He and Litterman, 1999):

=============================== ============================================================
the covariance ``C``            a :class:`~alphalab.analytics.risk_model.CovarianceMatrix`
the prior ``pi``                :class:`EquilibriumPrior` -- reverse optimization from
                                *supplied* market values and risk aversion -- or
                                :class:`SuppliedPrior`, a caller's own expected returns
the prior's uncertainty ``tau`` a positive scalar, required
the views ``P``, ``q``          :class:`InvestorView` -- a portfolio of assets and the
                                return the investor expects of it
their uncertainty ``Omega``     each view's variance, stated with the view
=============================== ============================================================

and returns a :class:`BlackLittermanPosterior` carrying the posterior mean, the
uncertainty of that mean and the predictive covariance, each as a value with an
identity. Construction is then an ordinary
:class:`~alphalab.portfolio_optimizer.construction.MeanVariance` problem over
the posterior, under whatever constraints the caller states -- the model and the
construction stay two steps.

The formulas
------------

.. code-block:: text

    A     = P (tau C) P' + Omega                          (k x k, positive definite)
    mu    = pi + (tau C) P' A^-1 (q - P pi)                posterior mean
    M     = tau C - (tau C) P' A^-1 P (tau C)              uncertainty of the mean
    C_bl  = C + M                                          predictive covariance

This is the form that inverts only the ``k x k`` matrix ``A``, which is
positive definite because every view variance is. With no views it reduces to
``mu = pi`` and ``M = tau C``. ``M`` and ``C_bl`` are computed on one triangle
and mirrored, so they are exactly symmetric.

Nothing is invented
-------------------

There is no default risk aversion, no default ``tau``, no default view
confidence and no market portfolio AlphaLab knows: an equilibrium prior needs
the caller's market values, and a view with no stated variance cannot be
built. :func:`view_variance_from_prior` computes the one common convention for a
variance -- proportional to the view portfolio's prior variance -- only when
called, with its scale stated.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal
from types import MappingProxyType
from typing import Final

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.portfolio_optimizer.construction import ExpectedReturns
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError
from alphalab.portfolio_optimizer.quadratic import _cholesky, _cholesky_solve

__all__ = [
    "BLACK_LITTERMAN_SCHEME",
    "BlackLittermanModel",
    "BlackLittermanPosterior",
    "EquilibriumPrior",
    "InvestorView",
    "SuppliedPrior",
    "ViewDiagnostic",
    "black_litterman",
    "view_variance_from_prior",
]

BLACK_LITTERMAN_SCHEME: Final = "alphalab.black_litterman.v1"

_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConstructionInputError(f"{what} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise ConstructionInputError(f"{what} is {number!r}; every input must be finite.")
    return number


def _positive(value: object, what: str) -> float:
    number = _number(value, what)
    if number <= 0.0:
        raise ConstructionInputError(f"{what} is {number!r}; it must be positive.")
    return number


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EquilibriumPrior:
    """``pi = delta * C * w_mkt``: the returns that make the market portfolio optimal.

    Reverse optimization: if a mean-variance investor with risk aversion
    ``delta`` holds the market portfolio, these are the expected returns that
    justify it. Both the market and ``delta`` are the caller's.

    Attributes:
        market_values: Asset -> market value (capitalization, float-adjusted or
            not -- the caller's choice), positive, for exactly the universe. The
            weights are these over their total, computed in an explicit 28-digit
            context.
        currency: The currency every market value is in. Must be the
            covariance's: values in different currencies cannot be summed
            into a weight.
        risk_aversion: ``delta > 0``.
    """

    market_values: Mapping[str, Decimal]
    currency: str
    risk_aversion: float

    def __post_init__(self) -> None:
        if not self.market_values:
            raise ConstructionInputError("An equilibrium prior needs market values.")
        ordered: dict[str, Decimal] = {}
        for asset, value in sorted(self.market_values.items()):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ConstructionInputError(
                    f"The market value of {asset!r} is {value!r}; it must be a positive Decimal."
                )
            ordered[asset] = value
        object.__setattr__(self, "market_values", MappingProxyType(ordered))
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ConstructionInputError(
                "An equilibrium prior names the currency of its market values."
            )
        object.__setattr__(self, "risk_aversion", _positive(self.risk_aversion, "risk_aversion"))

    def market_weights(self) -> Mapping[str, float]:
        """``value / total`` per asset, each computed once in the explicit context."""

        total = sum(self.market_values.values(), Decimal(0))
        return MappingProxyType(
            {
                asset: float(_CONTEXT.divide(value, total))
                for asset, value in self.market_values.items()
            }
        )

    def rendering(self) -> list[str]:
        return [
            "prior=equilibrium",
            f"currency={self.currency!r}",
            f"risk_aversion={self.risk_aversion!r}",
            *(f"market_value[{asset!r}]={value}" for asset, value in self.market_values.items()),
        ]


@dataclass(frozen=True, slots=True)
class SuppliedPrior:
    """A prior the caller supplies directly -- a forecast, or another model's output.

    Attributes:
        returns: The prior mean, in the covariance's currency and period.
    """

    returns: ExpectedReturns

    def rendering(self) -> list[str]:
        return ["prior=supplied", f"returns={self.returns.returns_id}"]


@dataclass(frozen=True, slots=True)
class InvestorView:
    """One view: "this portfolio of assets will return this much", and how sure.

    Attributes:
        name: Identifies the view in diagnostics and in the model identity.
        exposures: Asset -> weight in the view portfolio (a row of ``P``).
            ``{"A": 1.0}`` is an absolute view on A; ``{"A": 1.0, "B": -1.0}``
            is "A outperforms B". Non-zero entries only.
        expected_return: ``q``: the view portfolio's expected return, per the
            covariance's period.
        variance: ``omega > 0``: the view's uncertainty, as a variance of
            ``q``. Required -- a view without a stated confidence would take its
            weight in the posterior from a default nobody chose.
    """

    name: str
    exposures: Mapping[str, float]
    expected_return: float
    variance: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or self.name != self.name.strip()
        ):
            raise ConstructionInputError(
                f"A view is named by a non-blank, unpadded string, got {self.name!r}."
            )
        if not self.exposures:
            raise ConstructionInputError(f"View {self.name!r} names no asset.")
        ordered: dict[str, float] = {}
        for asset, weight in sorted(self.exposures.items()):
            value = _number(weight, f"exposure of view {self.name!r} to {asset!r}")
            if value == 0.0:
                raise ConstructionInputError(
                    f"View {self.name!r} lists {asset!r} with zero exposure; leave it out."
                )
            ordered[asset] = value
        object.__setattr__(self, "exposures", MappingProxyType(ordered))
        object.__setattr__(
            self, "expected_return", _number(self.expected_return, f"return of view {self.name!r}")
        )
        object.__setattr__(
            self, "variance", _positive(self.variance, f"variance of view {self.name!r}")
        )

    def rendering(self) -> str:
        exposures = ",".join(f"{asset!r}:{weight!r}" for asset, weight in self.exposures.items())
        return f"view={self.name!r}|{exposures}|q={self.expected_return!r}|omega={self.variance!r}"


def view_variance_from_prior(
    exposures: Mapping[str, float], covariance: CovarianceMatrix, tau: float, scale: float
) -> float:
    """``scale * p' (tau C) p``: a view variance proportional to its prior variance.

    The He-Litterman convention (``scale = 1``) makes each view exactly as
    uncertain as the prior says that portfolio's return is; a smaller scale
    expresses more confidence. It is a convention and is offered as a function
    the caller calls, never as a default.
    """

    tau_value = _positive(tau, "tau")
    factor = _positive(scale, "view variance scale")
    names = sorted(exposures)
    weights = [_number(exposures[name], f"exposure to {name!r}") for name in names]
    positions = [covariance.index(name) for name in names]
    variance = math.fsum(
        weights[row] * covariance.values[positions[row]][positions[column]] * weights[column]
        for row in range(len(names))
        for column in range(len(names))
    )
    return factor * tau_value * variance


@dataclass(frozen=True, slots=True)
class BlackLittermanModel:
    """Every input the posterior is determined by.

    Attributes:
        covariance: ``C``. Positive definite.
        prior: ``pi``, as an equilibrium or supplied prior.
        tau: The scale of the prior's uncertainty, ``tau > 0``: ``tau C`` is the
            covariance of ``pi`` itself. Required; the literature's values
            range over two orders of magnitude and none is neutral.
        views: The views, any number including none. Their order does not
            matter and they are held sorted by name.
    """

    covariance: CovarianceMatrix
    prior: EquilibriumPrior | SuppliedPrior
    tau: float
    views: tuple[InvestorView, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tau", _positive(self.tau, "tau"))
        views = tuple(sorted(self.views, key=lambda view: view.name))
        names = [view.name for view in views]
        if len(set(names)) != len(names):
            raise ConstructionInputError(f"Two views share a name: {names}.")
        universe = set(self.covariance.assets)
        for view in views:
            outside = sorted(set(view.exposures) - universe)
            if outside:
                raise ConstructionInputError(
                    f"View {view.name!r} names {outside}, which the covariance does not cover."
                )
        object.__setattr__(self, "views", views)
        prior_assets = (
            set(self.prior.market_values)
            if isinstance(self.prior, EquilibriumPrior)
            else set(self.prior.returns.values)
        )
        if prior_assets != universe:
            raise ConstructionInputError(
                f"The prior must cover exactly the covariance's assets: missing "
                f"{sorted(universe - prior_assets)}, outside {sorted(prior_assets - universe)}."
            )
        if isinstance(self.prior, EquilibriumPrior):
            if self.prior.currency != self.covariance.currency:
                raise ConstructionInputError(
                    f"The market values are in {self.prior.currency!r} and the covariance's "
                    f"returns in {self.covariance.currency!r}; state both in one currency."
                )
        elif (
            self.prior.returns.currency != self.covariance.currency
            or self.prior.returns.period != self.covariance.period
        ):
            raise ConstructionInputError(
                "The supplied prior must be in the covariance's currency and period."
            )

    @property
    def model_id(self) -> str:
        """The derived identity of this model."""

        return _digest(
            [
                BLACK_LITTERMAN_SCHEME,
                f"covariance={self.covariance.covariance_id}",
                f"tau={self.tau!r}",
                *self.prior.rendering(),
                *(view.rendering() for view in self.views),
            ]
        )


@dataclass(frozen=True, slots=True)
class ViewDiagnostic:
    """What one view said, what the prior said about it, and where the posterior landed.

    Attributes:
        name: The view.
        stated: ``q``.
        prior_implied: ``p' pi``: what the prior expected of the view portfolio.
        posterior_implied: ``p' mu``: what the posterior expects of it. Between
            the two above, nearer the more confident.
        variance: ``omega``.
    """

    name: str
    stated: float
    prior_implied: float
    posterior_implied: float
    variance: float


@dataclass(frozen=True, slots=True)
class BlackLittermanPosterior:
    """The posterior a model implies. Feed :attr:`posterior_returns` to construction.

    Attributes:
        model_id: The model it came from.
        prior_returns: ``pi``.
        posterior_returns: ``mu``.
        mean_uncertainty: ``M``, the covariance of the posterior mean, as a
            matrix derived from ``C``.
        posterior_covariance: ``C + M``, the predictive covariance of returns,
            derived from ``C``. Whether construction uses it or ``C`` is the
            caller's stated choice.
        views: Per-view diagnostics, sorted by name.
    """

    model_id: str
    prior_returns: ExpectedReturns
    posterior_returns: ExpectedReturns
    mean_uncertainty: CovarianceMatrix
    posterior_covariance: CovarianceMatrix
    views: tuple[ViewDiagnostic, ...]


def black_litterman(model: BlackLittermanModel) -> BlackLittermanPosterior:
    """The posterior mean, its uncertainty and the predictive covariance. See the module docstring.

    Raises:
        ConstructionInputError: If ``P (tau C) P' + Omega`` cannot be factorized
            in double precision.
    """

    covariance = model.covariance
    assets = covariance.assets
    count = len(assets)
    rows = covariance.values
    tau = model.tau
    scaled = [[tau * rows[row][column] for column in range(count)] for row in range(count)]

    if isinstance(model.prior, EquilibriumPrior):
        weights = model.prior.market_weights()
        market = [weights[asset] for asset in assets]
        delta = model.prior.risk_aversion
        prior = [
            delta * math.fsum(rows[row][column] * market[column] for column in range(count))
            for row in range(count)
        ]
        prior_source = (
            f"equilibrium prior: delta={delta!r} times covariance {covariance.covariance_id[:12]} "
            "times supplied market weights"
        )
    else:
        prior = [model.prior.returns.values[asset] for asset in assets]
        prior_source = model.prior.returns.source

    views = model.views
    picks = [[view.exposures.get(asset, 0.0) for asset in assets] for view in views]
    stated = [view.expected_return for view in views]
    # (tau C) P' : n x k
    projected = [
        [math.fsum(scaled[row][column] * pick[column] for column in range(count)) for pick in picks]
        for row in range(count)
    ]
    size = len(views)
    system = [
        [
            math.fsum(picks[first][row] * projected[row][second] for row in range(count))
            + (views[first].variance if first == second else 0.0)
            for second in range(size)
        ]
        for first in range(size)
    ]
    prior_implied = [
        math.fsum(pick[column] * prior[column] for column in range(count)) for pick in picks
    ]
    if size:
        factored = _cholesky(system)
        if factored is None:
            raise ConstructionInputError(
                "P (tau C) P' + Omega is numerically singular: the views are too confident and "
                "too collinear to separate in double precision."
            )
        lower = factored[0]
        surprise = _cholesky_solve(lower, [stated[k] - prior_implied[k] for k in range(size)])
        posterior = [
            prior[row] + math.fsum(projected[row][k] * surprise[k] for k in range(size))
            for row in range(count)
        ]
        # X = A^-1 P (tau C), column by column: A x_j = (P tau C)[:, j] = projected[j, :]
        solved = [_cholesky_solve(lower, projected[column]) for column in range(count)]
    else:
        posterior = list(prior)
        solved = [[] for _ in range(count)]

    uncertainty = [[0.0] * count for _ in range(count)]
    for row in range(count):
        for column in range(row, count):
            value = scaled[row][column] - math.fsum(
                projected[row][k] * solved[column][k] for k in range(size)
            )
            uncertainty[row][column] = value
            uncertainty[column][row] = value
    predictive = [
        [rows[row][column] + uncertainty[row][column] for column in range(count)]
        for row in range(count)
    ]

    model_id = model.model_id
    prior_returns = ExpectedReturns(
        dict(zip(assets, prior, strict=True)), covariance.currency, covariance.period, prior_source
    )
    posterior_returns = ExpectedReturns(
        dict(zip(assets, posterior, strict=True)),
        covariance.currency,
        covariance.period,
        f"Black-Litterman posterior mean of model {model_id}",
    )
    mean_uncertainty = CovarianceMatrix(
        assets,
        tuple(tuple(row) for row in uncertainty),
        covariance.currency,
        covariance.period,
        covariance.source,
        covariance.observations,
        parent_id=covariance.covariance_id,
        derivation=f"Black-Litterman uncertainty of the posterior mean, model {model_id}",
    )
    posterior_covariance = CovarianceMatrix(
        assets,
        tuple(tuple(row) for row in predictive),
        covariance.currency,
        covariance.period,
        covariance.source,
        covariance.observations,
        parent_id=covariance.covariance_id,
        derivation=f"Black-Litterman predictive covariance C + M, model {model_id}",
    )
    diagnostics = tuple(
        ViewDiagnostic(
            name=view.name,
            stated=view.expected_return,
            prior_implied=prior_implied[index],
            posterior_implied=math.fsum(
                picks[index][column] * posterior[column] for column in range(count)
            ),
            variance=view.variance,
        )
        for index, view in enumerate(views)
    )
    return BlackLittermanPosterior(
        model_id=model_id,
        prior_returns=prior_returns,
        posterior_returns=posterior_returns,
        mean_uncertainty=mean_uncertainty,
        posterior_covariance=posterior_covariance,
        views=diagnostics,
    )
