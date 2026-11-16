"""Statistical factors: a covariance's principal components, as a factor model (v4.3, WS-25).

:func:`principal_component_factors` decomposes a covariance by M2's symmetric
eigensystem and keeps its ``k`` largest components as a
:class:`~alphalab.analytics.risk_model.FactorStructure`, ``B F B' + D``, which
construction, :func:`~alphalab.analytics.risk_model.euler_decomposition` and
:func:`~alphalab.analytics.risk_model.factor_risk` read as they read any
factor model.

* **Loadings.** Factor ``j`` is the portfolio whose weights are the ``j``-th
  eigenvector: unit length, its largest entry positive. Asset ``i``'s loading
  on it is that vector's ``i``-th entry.
* **The factor covariance.** ``diag(lambda_1 .. lambda_k)``: each component's
  variance is its eigenvalue, and the components are uncorrelated.
* **Specific variances.** ``D_i = sum_{j > k} lambda_j v_ji²``: what the ``k``
  components leave of asset ``i``'s variance, summed from the components left
  out, so each is a sum of non-negative terms.

The covariance must be positive definite, by its own pivoted test. A singular
one -- a sample covariance over fewer observations than assets -- is refused:
the caller derives a regularized matrix first, with its derivation recorded,
as :class:`~alphalab.analytics.risk_model.CovarianceMatrix` requires of every
regularization. Nothing is regularized here.

Classes
-------
Householder reductions and implicit QL are correctly rounded operations in a
fixed order: the eigenvalues and eigenvectors are class 2, the same bits on
every host, and the structure's identity renders them (ADR-0058).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import CovarianceMatrix, FactorLoadings, FactorStructure
from alphalab.common.convergence import ConvergenceReport
from alphalab.common.linalg import symmetric_eigensystem

__all__ = ["PrincipalComponents", "principal_component_factors"]


@dataclass(frozen=True, slots=True)
class PrincipalComponents:
    """A covariance's ``k`` largest principal components, as a factor model.

    Attributes:
        structure: ``B F B' + D``: the components' loadings, their variances,
            and what they leave of each asset's variance.
        eigenvalues: Every component's variance, largest first: the ``k``
            kept and the rest.
        explained: The share of the total variance each kept component
            carries: its eigenvalue over the trace.
        convergence: How the eigensystem met its criterion.
    """

    structure: FactorStructure
    eigenvalues: tuple[float, ...]
    explained: tuple[float, ...]
    convergence: ConvergenceReport


def principal_component_factors(
    covariance: CovarianceMatrix,
    *,
    factors: int,
    tolerance: float,
    max_iterations: int,
) -> PrincipalComponents:
    """The factor model of ``covariance``'s ``factors`` largest principal components.

    Args:
        covariance: A positive definite covariance of asset returns.
        factors: ``k``: one at least, fewer than the assets.
        tolerance: The eigensystem's splitting criterion (ADR-0058).
        max_iterations: Its QL iterations allowed for any one eigenvalue.

    Returns:
        The components, whose factors are named ``pc_1`` to ``pc_k``, zero-padded
        to sort in order, each with its lineage in the covariance's identity.

    Raises:
        AnalyticsValidationError: If ``covariance`` is not a covariance matrix
            or not positive definite, or ``factors`` is not between 1 and the
            assets less one.
        ConvergenceError: If the eigensystem does not meet its criterion.
    """

    if not isinstance(covariance, CovarianceMatrix):
        raise AnalyticsValidationError(
            f"covariance must be a CovarianceMatrix, got {covariance!r}."
        )
    size = len(covariance.assets)
    if isinstance(factors, bool) or not isinstance(factors, int) or not 1 <= factors < size:
        raise AnalyticsValidationError(
            f"factors is an integer from 1 to {size - 1}, fewer than the {size} assets, "
            f"got {factors!r}: the components left out are each asset's specific variance."
        )
    covariance.require_positive_definite("Principal-component factors")
    system = symmetric_eigensystem(
        covariance.values, tolerance=tolerance, max_iterations=max_iterations
    )
    vectors = system.eigenvectors
    width = len(str(factors))
    names = [f"pc_{j + 1:0{width}d}" for j in range(factors)]
    loadings = FactorLoadings.of(
        {
            asset: {name: vectors[j][i] for j, name in enumerate(names)}
            for i, asset in enumerate(covariance.assets)
        },
        source=f"principal components of: {covariance.source}",
        lineage={
            name: f"principal component {j + 1} of covariance {covariance.covariance_id}"
            for j, name in enumerate(names)
        },
        as_of=None,
    )
    eigenvalues = system.eigenvalues
    factor_covariance = CovarianceMatrix.from_rows(
        names,
        [[eigenvalues[j] if j == m else 0.0 for m in range(factors)] for j in range(factors)],
        currency=covariance.currency,
        period=covariance.period,
        source=f"principal-component variances of covariance {covariance.covariance_id}",
        observations=covariance.observations,
    )
    specific = {
        asset: math.fsum([eigenvalues[j] * vectors[j][i] ** 2 for j in range(factors, size)])
        for i, asset in enumerate(covariance.assets)
    }
    negative = sorted(asset for asset, value in specific.items() if value < 0.0)
    if negative:
        raise AnalyticsValidationError(
            f"The components left out give {negative} a negative specific variance: an "
            "eigenvalue of a positive definite covariance came out negative, which its "
            "conditioning, not the data, decided."
        )
    trace = math.fsum(eigenvalues)
    return PrincipalComponents(
        structure=FactorStructure.of(loadings, factor_covariance, specific),
        eigenvalues=eigenvalues,
        explained=tuple(eigenvalues[j] / trace for j in range(factors)),
        convergence=system.convergence,
    )
