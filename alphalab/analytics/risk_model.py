"""The risk model as values: a covariance, factor loadings and a classification.

v3.3 made risk a *measurement* and put it here, in :mod:`alphalab.analytics`
(ADR-0038 decision 7). What it measured from was a list of
:class:`~alphalab.analytics.decomposition.PositionRisk` -- a market value and a
return series per position -- and every figure was computed from those on the
spot. That is enough to decompose a book that already exists. It is not enough
for v3.8, where the same covariance is read by four things that are not a book:

========================= ==================================================
portfolio construction    :mod:`alphalab.portfolio_optimizer` solves for
                          weights *before* any position exists
risk budgeting            :mod:`alphalab.analytics.risk_budget` compares
                          contributions with limits along five dimensions
cross-strategy risk       :mod:`alphalab.analytics.cross_strategy` correlates
                          strategies rather than assets
capital allocation        weights from a construction become capital
========================= ==================================================

So this module makes the inputs those computations share into values with an
identity, and gives the arithmetic they share exactly one implementation.

One implementation per concept
------------------------------

* **The covariance estimator** is :func:`alphalab.common.statistics.sample_covariance`,
  applied pairwise by :func:`_pairwise_sample_covariance`. v3.3's
  :func:`~alphalab.analytics.decomposition.covariance_matrix` now calls the same
  loop, so a matrix estimated here and one estimated there are the same floats.
* **The Euler decomposition** -- ``CTR_i = w_i (C w)_i / sigma`` -- is
  :func:`_euler`, which v3.3's :func:`~alphalab.analytics.decomposition.risk_contributions`
  and :func:`~alphalab.analytics.decomposition.portfolio_volatility` now call. It
  is written as the same expressions in the same order they were written in
  before, so the numbers v3.3 published are unchanged bit for bit (pinned by a
  regression test that recomputes them the old way).
* **A portfolio's factor exposure** -- ``sum_i w_i * beta_if`` -- is
  :func:`_accumulate_exposures`, read by
  :func:`~alphalab.analytics.decomposition.factor_exposure`, by construction's
  factor constraints and by cross-strategy crowding.
* **Concentration** -- the Herfindahl index -- is :func:`herfindahl_index`.

A covariance is a claim, so it says what it is a claim about
-------------------------------------------------------------

A covariance matrix of daily returns measured in euros and one of monthly
returns measured in dollars are both "a covariance", and mixing them produces a
portfolio volatility in no unit at all. :class:`CovarianceMatrix` therefore
carries the **currency** every return it describes was measured in and the
**period** each return spans, and every consumer that combines it with anything
else checks both. It also carries where it came from (``source``), how many
observations it was estimated from, and -- when it was derived from another
matrix by an explicit regularization or restriction -- which one and how. All of
it enters :attr:`CovarianceMatrix.covariance_id`, so two matrices share an
identity exactly when they are the same claim.

Definiteness is measured, not assumed
-------------------------------------

A sample covariance over fewer observations than assets is singular, a matrix
typed in by hand can be indefinite, and an asset whose price never moved has a
zero row. :meth:`CovarianceMatrix.definiteness` classifies a matrix by a
Cholesky factorization with diagonal pivoting and the standard rank-revealing
stopping rule -- a pivot at or below ``n * eps * max(diag)`` is zero (Higham,
*Accuracy and Stability of Numerical Algorithms*, section 10.3) -- and reports
the rank and the pivot ratio rather than a yes or no. Nothing here regularizes a
matrix on its own initiative: :meth:`CovarianceMatrix.with_ridge` and
:meth:`CovarianceMatrix.with_diagonal_shrinkage` derive a *new* matrix whose
identity records the parent and the rule, exactly as a cleaned dataset derives a
new version rather than editing the old one (ADR-0036).

Absent is not zero
------------------

A factor loading that was not supplied is not a loading of zero -- zero is a
measurement -- and an asset with no classification is not in a bucket called
"unknown". :class:`FactorLoadings` refuses to be built with a hole in it, and
:meth:`Classification.require_covers` names every asset a computation needed a
label for and did not get. The same rule ADR-0038 decision 6 applies to
attribution, applied to the inputs of risk.
"""

from __future__ import annotations

import hashlib
import math
import sys
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from operator import itemgetter
from types import MappingProxyType
from typing import Final

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.common.statistics import sample_covariance

__all__ = [
    "CLASSIFICATION_SCHEME",
    "COVARIANCE_SCHEME",
    "EXCHANGE_RATE_FACTOR_PREFIX",
    "FACTOR_LOADINGS_SCHEME",
    "FACTOR_STRUCTURE_SCHEME",
    "Classification",
    "CorrelationMatrix",
    "CovarianceMatrix",
    "Definiteness",
    "DefinitenessKind",
    "FactorLoadings",
    "FactorRisk",
    "FactorStructure",
    "RiskContributions",
    "currency_loadings",
    "euler_decomposition",
    "factor_cholesky_pivots",
    "factor_risk",
    "herfindahl_index",
    "portfolio_factor_exposures",
]

#: Scheme tags, and the first line of each canonical key.
COVARIANCE_SCHEME: Final = "alphalab.covariance.v1"
FACTOR_LOADINGS_SCHEME: Final = "alphalab.factor_loadings.v1"
FACTOR_STRUCTURE_SCHEME: Final = "alphalab.factor_structure.v1"
CLASSIFICATION_SCHEME: Final = "alphalab.classification.v1"

#: How :func:`currency_loadings` names an exchange rate's factor: ``FX:EUR`` is
#: the return of one euro measured in the reporting currency.
EXCHANGE_RATE_FACTOR_PREFIX: Final = "FX:"

#: Machine epsilon for IEEE-754 binary64, the precision every figure here is in.
_EPSILON: Final = sys.float_info.epsilon


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _require_text(value: object, what: str) -> str:
    """A non-blank, unpadded, printable string -- the identity rule for a label.

    Padded and multi-line strings are refused rather than normalized, following
    :mod:`alphalab.research.regimes`: a label enters a derived identity, and two
    spellings of one label would be two identities.
    """

    if not isinstance(value, str):
        raise AnalyticsValidationError(f"{what} must be a string, got {type(value).__name__}.")
    if not value.strip():
        raise AnalyticsValidationError(f"{what} is blank.")
    if value != value.strip() or not value.isprintable():
        raise AnalyticsValidationError(
            f"{what} {value!r} is padded or contains a control character. It enters a "
            "derived identity, so it is refused rather than normalized."
        )
    return value


def _require_finite(value: object, what: str) -> float:
    """A finite real number, as a float. ``bool`` is refused: it is not a quantity."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AnalyticsValidationError(f"{what} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise AnalyticsValidationError(
            f"{what} is {number!r}. A non-finite input would propagate into every figure "
            "computed from it and read as a measurement."
        )
    return number


def _require_instant(value: object, what: str) -> float | None:
    if value is None:
        return None
    return _require_finite(value, what)


def _render(value: object) -> str:
    """How every value enters a canonical key: ``repr``, which is exact for a float."""

    return repr(value)


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Shared arithmetic -- one implementation of each
# --------------------------------------------------------------------------- #


def _pairwise_sample_covariance(
    series: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], ...]:
    """Sample covariance of every pair of ``series``, in the order given.

    Each pair is computed once, by :func:`~alphalab.common.statistics.sample_covariance`,
    and written to both cells, so the result cannot disagree with itself; the
    diagonal is exactly :func:`~alphalab.common.statistics.sample_variance` of
    each series. ``n(n+1)/2`` estimates over ``n`` series -- there is no linear
    algorithm for a covariance matrix, and sampling or assuming a diagonal
    would change the number rather than the cost (ADR-0038).
    """

    count = len(series)
    rows = [[0.0] * count for _ in range(count)]
    for outer in range(count):
        for inner in range(outer, count):
            value = sample_covariance(series[outer], series[inner])
            rows[outer][inner] = value
            rows[inner][outer] = value
    return tuple(tuple(row) for row in rows)


@dataclass(frozen=True, slots=True)
class _EulerTerms:
    variance: float
    volatility: float
    exposures: tuple[float, ...]  # (C w)_i, in the order of the weights given
    contributions: tuple[float, ...]  # w_i (C w)_i / sigma, or () when sigma is zero


def _euler(weights: Sequence[float], rows: Sequence[Sequence[float]]) -> _EulerTerms:
    """The quadratic form ``w' C w`` and its Euler decomposition.

    Written as exactly the expressions :mod:`alphalab.analytics.decomposition`
    used in v3.3 -- the variance as one flat sum of ``w_i * C_ij * w_j`` taken
    row by row, and each contribution as ``w_i * sum_j(C_ij * w_j) / sigma`` --
    so that routing that module through this function changed no published
    number. ``rows`` and ``weights`` are aligned: ``rows[i][j]`` is the
    covariance of the ``i``-th and ``j``-th weighted asset.

    A tiny negative variance can only arise from floating-point cancellation on
    a near-singular matrix; the true quadratic form of a covariance matrix is
    non-negative, so it is read as zero, as it was in v3.3.
    """

    count = len(weights)
    variance = sum(
        weights[first] * rows[first][second] * weights[second]
        for first in range(count)
        for second in range(count)
    )
    volatility = math.sqrt(max(0.0, variance))
    exposures = tuple(
        sum(rows[index][other] * weights[other] for other in range(count)) for index in range(count)
    )
    if volatility == 0.0:
        return _EulerTerms(variance, volatility, exposures, ())
    contributions = tuple(weights[index] * exposures[index] / volatility for index in range(count))
    return _EulerTerms(variance, volatility, exposures, contributions)


def _structured_euler(
    weights: Sequence[float], positions: Sequence[int], structure: FactorStructure
) -> _EulerTerms:
    """:func:`_euler` through a factor structure, in ``O(n k^2)`` (v3.13, ledger PRF-013).

    ``(C w)_i = b_i' F (B' w) + d_i w_i`` for the weighted assets, at
    ``positions`` in the structure, and ``w' C w = (B' w)' F (B' w) +
    sum_i d_i w_i^2`` -- each an exactly-rounded sum.
    """

    b = structure.loadings.values
    f = structure.factor_covariance.values
    d = structure.specific
    k = len(structure.loadings.factors)
    held = list(zip(weights, positions, strict=True))
    factor_exposures = [math.fsum(w * b[p][h] for w, p in held) for h in range(k)]
    pushed = [math.fsum(f[g][h] * factor_exposures[h] for h in range(k)) for g in range(k)]
    exposures = tuple(
        math.fsum([*(b[p][g] * pushed[g] for g in range(k)), d[p] * w]) for w, p in held
    )
    variance = math.fsum(
        [
            *(factor_exposures[g] * pushed[g] for g in range(k)),
            *(d[p] * w * w for w, p in held),
        ]
    )
    volatility = math.sqrt(max(0.0, variance))
    if volatility == 0.0:
        return _EulerTerms(variance, volatility, exposures, ())
    contributions = tuple(
        w * exposure / volatility for w, exposure in zip(weights, exposures, strict=True)
    )
    return _EulerTerms(variance, volatility, exposures, contributions)


def _correlation_rows(rows: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    """``C_ij / sqrt(C_ii * C_jj)`` for every cell: correlation derived from covariance.

    Derived rather than estimated independently, so a correlation and a
    covariance can never disagree about the same pair. Every diagonal must be
    positive; the callers refuse a zero variance first, each naming the asset.
    """

    count = len(rows)
    return tuple(
        tuple(
            rows[row][column] / math.sqrt(rows[row][row] * rows[column][column])
            for column in range(count)
        )
        for row in range(count)
    )


def _accumulate_exposures(
    rows: Iterable[tuple[float, Iterable[tuple[str, float]]]],
) -> dict[str, float]:
    """``sum_i w_i * beta_if`` per factor, accumulated in the order given.

    ``rows`` yields ``(weight, ((factor, loading), ...))`` per asset. The one
    place a book's factor exposure is added up; the order of accumulation is
    the caller's (assets, then factors, each sorted), which is what makes the
    float result reproducible.
    """

    exposure: dict[str, float] = {}
    for weight, loadings in rows:
        for factor, loading in loadings:
            exposure[factor] = exposure.get(factor, 0.0) + weight * loading
    return dict(sorted(exposure.items()))


def herfindahl_index(shares: Iterable[float]) -> float:
    """The Herfindahl-Hirschman index: the sum of squared shares.

    ``shares`` should already be shares of one whole -- this squares and adds
    and does not normalize, because what the whole *is* (gross exposure,
    capital, a factor's gross loading) is the caller's statement. For ``n``
    equal shares of one it is ``1/n``, and ``1/index`` reads as the number of
    equal holdings that would be this concentrated. The single implementation
    :func:`~alphalab.analytics.decomposition.concentration` and cross-strategy
    capital concentration share.
    """

    return sum(share * share for share in shares)


# --------------------------------------------------------------------------- #
# Definiteness
# --------------------------------------------------------------------------- #


class DefinitenessKind(Enum):
    """What a pivoted Cholesky factorization found."""

    #: Every pivot is above the rank floor: the matrix is invertible and every
    #: quadratic form over it is strictly positive for a non-zero vector.
    POSITIVE_DEFINITE = auto()

    #: Positive semidefinite with rank below its size: some non-zero portfolio
    #: has zero variance under it, so a minimum-variance solution need not be
    #: unique.
    SINGULAR = auto()

    #: Not positive semidefinite: some portfolio would have *negative* variance.
    #: Not a covariance matrix of anything.
    INDEFINITE = auto()


@dataclass(frozen=True, slots=True)
class Definiteness:
    """How a covariance matrix factorized, and the evidence for the verdict.

    Attributes:
        kind: The classification.
        rank: How many pivots exceeded :attr:`pivot_floor`.
        smallest_pivot: The smallest pivot accepted, or the first one refused.
            A pivot is a variance conditional on every earlier pivot's asset.
        largest_pivot: The first (largest) pivot.
        pivot_floor: ``n * eps * max(diag)``: the rank-revealing threshold the
            factorization used. At or below it a pivot is indistinguishable from
            rounding error in the diagonal.
        zero_variance_assets: Assets whose own variance is zero -- each makes
            the matrix singular by itself and is named so it can be removed or
            treated as cash explicitly.
    """

    kind: DefinitenessKind
    rank: int
    smallest_pivot: float
    largest_pivot: float
    pivot_floor: float
    zero_variance_assets: tuple[str, ...]

    @property
    def pivot_ratio(self) -> float:
        """``smallest_pivot / largest_pivot``: an inexpensive conditioning indicator.

        Near ``eps`` means the matrix is numerically near-singular even when it
        is classified positive definite; the square root of its reciprocal is a
        lower bound on the condition number of the Cholesky factor.
        """

        if self.largest_pivot <= 0.0:
            return 0.0
        return self.smallest_pivot / self.largest_pivot


def _definiteness(assets: Sequence[str], rows: Sequence[Sequence[float]]) -> Definiteness:
    """Cholesky with diagonal pivoting and the ``n * eps * max(diag)`` stopping rule.

    At each step the largest remaining diagonal of the Schur complement is
    pivoted in. When it falls to the floor the factorization stops: the rank is
    the number of pivots taken. The remaining block must then be zero to within
    the floor for the matrix to be semidefinite -- a PSD matrix's off-diagonal
    entries are bounded by the geometric mean of their diagonals -- and a
    negative diagonal anywhere is indefinite outright.
    """

    count = len(rows)
    work = [list(row) for row in rows]
    diagonal = [work[index][index] for index in range(count)]
    largest_diagonal = max(diagonal) if diagonal else 0.0
    floor = count * _EPSILON * largest_diagonal
    zero_variance = tuple(assets[index] for index in range(count) if diagonal[index] == 0.0)

    remaining = list(range(count))
    rank = 0
    largest_pivot = 0.0
    smallest_pivot = 0.0
    while remaining:
        pivot_index = max(remaining, key=lambda index: (work[index][index], -index))
        pivot = work[pivot_index][pivot_index]
        if rank == 0:
            largest_pivot = pivot
        if pivot <= floor:
            smallest_pivot = pivot
            break
        smallest_pivot = pivot
        rank += 1
        remaining.remove(pivot_index)
        scale = math.sqrt(pivot)
        column = {index: work[index][pivot_index] / scale for index in remaining}
        for first in remaining:
            for second in remaining:
                work[first][second] -= column[first] * column[second]

    if rank == count:
        return Definiteness(
            DefinitenessKind.POSITIVE_DEFINITE,
            rank,
            smallest_pivot,
            largest_pivot,
            floor,
            zero_variance,
        )

    indefinite = any(work[index][index] < -floor for index in remaining) or any(
        abs(work[first][second]) > floor
        for first in remaining
        for second in remaining
        if first != second
    )
    return Definiteness(
        DefinitenessKind.INDEFINITE if indefinite else DefinitenessKind.SINGULAR,
        rank,
        smallest_pivot,
        largest_pivot,
        floor,
        zero_variance,
    )


def _semidefinite_root(rows: Sequence[Sequence[float]]) -> list[tuple[float, ...]] | None:
    """``R`` with ``F = R R'`` for a small positive semidefinite ``F``, or ``None``.

    The pivoted factorization and stopping rule of :func:`_definiteness`, kept
    as columns: ``R`` has one column per pivot taken, so its width is the rank.
    ``None`` when ``F`` is indefinite.
    """

    count = len(rows)
    work = [[float(value) for value in row] for row in rows]
    if any(work[index][index] < 0.0 for index in range(count)):
        return None
    largest = max((work[index][index] for index in range(count)), default=0.0)
    floor = count * _EPSILON * largest
    remaining = list(range(count))
    columns: list[list[float]] = []
    while remaining:
        pivot_index = max(remaining, key=lambda index: (work[index][index], -index))
        pivot = work[pivot_index][pivot_index]
        if pivot <= floor:
            break
        remaining.remove(pivot_index)
        root = math.sqrt(pivot)
        column = [0.0] * count
        column[pivot_index] = root
        for index in remaining:
            column[index] = work[index][pivot_index] / root
        for first in remaining:
            for second in remaining:
                work[first][second] -= column[first] * column[second]
        columns.append(column)
    if any(work[index][index] < -floor for index in remaining) or any(
        abs(work[first][second]) > floor
        for first in remaining
        for second in remaining
        if first != second
    ):
        return None
    return [tuple(column[row] for column in columns) for row in range(count)]


def factor_cholesky_pivots(
    loadings: Sequence[Sequence[float]],
    factor_covariance: Sequence[Sequence[float]],
    specific: Sequence[float],
) -> tuple[float, ...] | None:
    """The natural-order Cholesky pivots of ``B F B' + D``, in ``O(n k^2)`` (ledger PRF-005).

    Pivot ``i`` is the variance of asset ``i`` conditional on every asset
    before it. With ``F = R R'`` and ``u_i = R' b_i`` it is
    ``D_i + u_i' M_i^-1 u_i``, ``M_i = I + sum_{j<i} u_j u_j' / D_j`` -- and
    ``M_i^-1`` is carried from one asset to the next by Sherman-Morrison, so no
    ``n x n`` matrix is ever formed. Every term is non-negative: no pivot is
    computed by cancellation.

    Returns ``None`` when the structure does not establish definiteness on its
    own -- a specific variance that is not positive, or a factor covariance that
    is not positive semidefinite. The dense pivoted factorization
    (:meth:`CovarianceMatrix.definiteness`) is then the authority.
    """

    if any(not value > 0.0 for value in specific):
        return None
    root = _semidefinite_root(factor_covariance)
    if root is None:
        return None
    width = len(root[0]) if root else 0
    columns = [tuple(row[column] for row in root) for column in range(width)]
    inverse = [[1.0 if row == column else 0.0 for column in range(width)] for row in range(width)]
    pivots: list[float] = []
    for exposure, variance in zip(loadings, specific, strict=True):
        u = [math.sumprod(exposure, column) for column in columns]
        v = [math.sumprod(row, u) for row in inverse]
        pivot = variance + math.sumprod(u, v)
        pivots.append(pivot)
        for row in range(width):
            target = inverse[row]
            for column in range(width):
                # (v_r v_c) / pivot is the same float both ways round, so the
                # carried inverse stays exactly symmetric.
                target[column] -= v[row] * v[column] / pivot
    return tuple(pivots)


# --------------------------------------------------------------------------- #
# Covariance and correlation
# --------------------------------------------------------------------------- #


def _canonical_order(
    assets: Sequence[str], rows: Sequence[Sequence[float]], what: str
) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...]]:
    """Reorder a square matrix so its assets are sorted, validating as it goes."""

    names = tuple(_require_text(asset, f"{what} asset") for asset in assets)
    if not names:
        raise AnalyticsValidationError(
            f"A {what} over no assets describes nothing, and every figure computed from it "
            "would be vacuous."
        )
    if len(set(names)) != len(names):
        repeated = sorted({name for name in names if names.count(name) > 1})
        raise AnalyticsValidationError(f"The {what} names {repeated} more than once.")
    if len(rows) != len(names):
        raise AnalyticsValidationError(
            f"The {what} has {len(rows)} rows for {len(names)} assets; it must be square."
        )
    for index, row in enumerate(rows):
        if len(row) != len(names):
            raise AnalyticsValidationError(
                f"Row {index} ({names[index]!r}) of the {what} has {len(row)} entries for "
                f"{len(names)} assets; it must be square."
            )
    order = sorted(range(len(names)), key=lambda index: names[index])
    in_order = all(position == index for index, position in enumerate(order))
    canonical: list[tuple[float, ...]] = []
    for position in order:
        source = rows[position]
        if set(map(type, source)) <= {float} and all(map(math.isfinite, source)):
            # Every cell already a finite float -- what the check below returns
            # cell by cell -- read in bulk (v3.13, ledger PRF-013).
            canonical.append(
                tuple(source) if in_order else tuple(source[column] for column in order)
            )
            continue
        canonical.append(
            tuple(
                _require_finite(source[column], f"{what}[{names[position]!r}][{names[column]!r}]")
                for column in order
            )
        )
    return tuple(names[index] for index in order), tuple(canonical)


@dataclass(frozen=True, slots=True)
class CovarianceMatrix:
    """A covariance of returns, with the unit, the period and the provenance it needs.

    Build one with :meth:`of`, :meth:`from_rows` or :meth:`sample`; each puts the
    assets in canonical (sorted) order, so the input order never reaches an
    identity. Direct construction must already be canonical.

    Attributes:
        assets: The assets, sorted and unique.
        values: ``values[i][j]`` is the covariance of ``assets[i]`` and
            ``assets[j]``. Exactly symmetric, finite, with a non-negative
            diagonal.
        currency: The currency every return this matrix describes was measured
            in. A euro stock's return in dollars includes the currency's move;
            in euros it does not. Required, never assumed.
        period: The return period each entry is per -- ``"1D"``, ``"1W"``,
            ``"1M"`` or whatever vocabulary the caller uses. Required: a daily
            and a monthly covariance differ by a factor of about twenty and are
            refused as a pair rather than mixed.
        source: Where the matrix came from, in words: a vendor's risk model and
            its version, or how it was estimated. Required.
        observations: How many aligned observations it was estimated from, or
            ``None`` when it was supplied rather than estimated here.
        parent_id: The :attr:`covariance_id` of the matrix this one was derived
            from, or ``None`` for a matrix that was not derived.
        derivation: How it was derived from its parent -- the rule and its
            parameters -- or ``None`` when it was not.
        factors: The :class:`FactorStructure` the matrix was implied by, when
            :meth:`factor_model` built it, or ``None``. A solving aid rather than
            part of the claim (v3.12, ledger PRF-005): not in
            :attr:`covariance_id` -- whose parent and derivation already name the
            factor covariance, the loadings and the specific variances -- and not
            compared. One passed in directly is checked against every value. A
            matrix derived from this one does not inherit it.

    Raises:
        AnalyticsValidationError: On a non-canonical order, a ragged, asymmetric
            or non-finite matrix, a negative variance, a blank currency, period
            or source, fewer than two observations, a derivation without a
            parent (or the reverse), or a factor structure that does not imply
            the matrix.
    """

    assets: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    currency: str
    period: str
    source: str
    observations: int | None
    parent_id: str | None = None
    derivation: str | None = None
    factors: FactorStructure | None = field(default=None, repr=False, compare=False)
    #: :attr:`covariance_id`, derived once when the matrix is built: the digest
    #: renders every value, ``O(n^2)``, and nothing about the matrix changes.
    _identity: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names, rows = _canonical_order(self.assets, self.values, "covariance matrix")
        if names != tuple(self.assets):
            raise AnalyticsValidationError(
                "A CovarianceMatrix built directly must list its assets in sorted order; use "
                "CovarianceMatrix.of or from_rows, which put them there."
            )
        # Symmetric with a non-negative diagonal, checked in bulk; only a matrix
        # that is not is walked cell by cell, for the first cell that is wrong.
        valid = all(
            rows[row][row] >= 0.0 and tuple(map(itemgetter(row), rows)) == rows[row]
            for row in range(len(names))
        )
        for row in range(0 if valid else len(names)):
            if rows[row][row] < 0.0:
                raise AnalyticsValidationError(
                    f"{names[row]!r} has variance {rows[row][row]!r}. A variance cannot be "
                    "negative; this matrix is not a covariance of anything."
                )
            for column in range(row + 1, len(names)):
                if rows[row][column] != rows[column][row]:
                    raise AnalyticsValidationError(
                        f"The covariance of {names[row]!r} and {names[column]!r} is "
                        f"{rows[row][column]!r} one way and {rows[column][row]!r} the other. A "
                        "covariance matrix is symmetric; symmetrize it explicitly (and say how) "
                        "before it gets here, because which half is right is not a question "
                        "this type answers by picking."
                    )
        object.__setattr__(self, "values", rows)
        _require_text(self.currency, "CovarianceMatrix.currency")
        _require_text(self.period, "CovarianceMatrix.period")
        _require_text(self.source, "CovarianceMatrix.source")
        if self.observations is not None and (
            isinstance(self.observations, bool)
            or not isinstance(self.observations, int)
            or self.observations < 2
        ):
            raise AnalyticsValidationError(
                f"observations is {self.observations!r}. A covariance is undefined over fewer "
                "than two observations; pass None for a matrix that was supplied rather than "
                "estimated."
            )
        if (self.parent_id is None) != (self.derivation is None):
            raise AnalyticsValidationError(
                "A derived covariance names both its parent and how it was derived; a root "
                "matrix names neither."
            )
        if self.parent_id is not None:
            _require_text(self.parent_id, "CovarianceMatrix.parent_id")
        if self.derivation is not None:
            _require_text(self.derivation, "CovarianceMatrix.derivation")
        if self.factors is not None:
            if not isinstance(self.factors, FactorStructure):
                raise AnalyticsValidationError(
                    f"CovarianceMatrix.factors must be a FactorStructure, got {self.factors!r}."
                )
            self.factors.require_implies(self)
        object.__setattr__(self, "_identity", self._derive_identity())

    # -- construction ------------------------------------------------------- #

    @classmethod
    def from_rows(
        cls,
        assets: Sequence[str],
        rows: Sequence[Sequence[float]],
        *,
        currency: str,
        period: str,
        source: str,
        observations: int | None,
    ) -> CovarianceMatrix:
        """A matrix given as rows aligned with ``assets``, reordered canonically."""

        names, canonical = _canonical_order(assets, rows, "covariance matrix")
        return cls(names, canonical, currency, period, source, observations)

    @classmethod
    def of(
        cls,
        entries: Mapping[str, Mapping[str, float]],
        *,
        currency: str,
        period: str,
        source: str,
        observations: int | None,
    ) -> CovarianceMatrix:
        """A matrix given as a nested mapping, which must be complete.

        The shape :meth:`as_mapping` returns and
        :class:`~alphalab.portfolio_optimizer.protocol.RiskModelProtocol`
        declares. A missing cell is refused rather than read as zero: zero
        covariance is a measurement of no co-movement, and absence is not one.
        """

        names = sorted(entries)
        missing = [
            f"{row}/{column}" for row in names for column in names if column not in entries[row]
        ]
        if missing:
            raise AnalyticsValidationError(
                f"The covariance mapping has no entry for {missing[:5]}"
                f"{' and more' if len(missing) > 5 else ''}. A missing covariance is not zero."
            )
        extra = sorted({column for row in names for column in entries[row]} - set(names))
        if extra:
            raise AnalyticsValidationError(
                f"The covariance mapping has columns {extra} with no row; it must be square."
            )
        rows = [[entries[row][column] for column in names] for row in names]
        return cls.from_rows(
            names,
            rows,
            currency=currency,
            period=period,
            source=source,
            observations=observations,
        )

    @classmethod
    def sample(
        cls,
        returns: Mapping[str, Sequence[float]],
        *,
        currency: str,
        period: str,
        source: str,
    ) -> CovarianceMatrix:
        """The sample covariance of aligned return series, keyed by asset.

        Aligned means the same length and the same periods in the same order:
        nothing here re-indexes by date, because no date is carried -- the rule
        :class:`~alphalab.analytics.decomposition.PositionRisk` states. The
        estimator is :func:`~alphalab.common.statistics.sample_covariance`
        (``n - 1``), so the diagonal is exactly the variance the rest of the
        repository reports.

        Raises:
            AnalyticsValidationError: If no series is given, the series differ
                in length, one is shorter than two observations, or a return is
                not finite.
        """

        if not returns:
            raise AnalyticsValidationError("A sample covariance needs at least one return series.")
        names = sorted(returns)
        lengths = sorted({len(returns[name]) for name in names})
        if len(lengths) > 1:
            raise AnalyticsValidationError(
                f"The return series have differing lengths {lengths}. A covariance between two "
                "series of different length would be computed over a pairing that had to be "
                "guessed."
            )
        if lengths[0] < 2:
            raise AnalyticsValidationError(
                f"A sample covariance is undefined over {lengths[0]} observation(s); at least 2 "
                "are needed."
            )
        series = [
            tuple(_require_finite(value, f"return of {name!r}") for value in returns[name])
            for name in names
        ]
        return cls(
            tuple(names),
            _pairwise_sample_covariance(series),
            currency,
            period,
            source,
            lengths[0],
        )

    @classmethod
    def ledoit_wolf(
        cls,
        returns: Mapping[str, Sequence[float]],
        *,
        currency: str,
        period: str,
        source: str,
    ) -> CovarianceMatrix:
        """Ledoit-Wolf shrinkage of the sample covariance, its intensity estimated (OFE-002).

        Ledoit and Wolf, *A well-conditioned estimator for large-dimensional
        covariance matrices*, Journal of Multivariate Analysis 88 (2004): the
        convex combination ``(1 - d) * S + d * m * I`` of the sample covariance
        ``S`` (divided by ``n``, as the paper has it) and the scaled identity
        ``m * I``, ``m = tr(S) / p``, with the intensity ``d`` that minimizes the
        expected squared Frobenius loss, estimated from the data:

        ``d = min(b2 / d2, 1)``, ``d2 = ||S - m I||^2``,
        ``b2 = (1 / n^2) * sum_t ||x_t x_t' - S||^2``

        over the demeaned observations ``x_t``. The numerator is computed as
        ``(sum_t ||x_t||^4 - n ||S||^2) / n^2``, which the sum equals exactly.

        It is well conditioned whenever it is not ``S`` -- positive definite for
        any positive intensity, even with fewer observations than assets -- and
        it is a *derivation*: the parent is the sample covariance of the same
        returns, and the intensity, the target and the rescaling to ``1 / n``
        are written into :attr:`derivation`, so the identity says which
        estimator this is. The estimate rests on the observations being
        independent and identically distributed with finite fourth moments,
        which nothing here can check -- the derivation names the paper whose
        assumptions a reader should weigh.

        Raises:
            AnalyticsValidationError: As :meth:`sample` does.
        """

        parent = cls.sample(returns, currency=currency, period=period, source=source)
        names = parent.assets
        count = len(names)
        observations = parent.observations
        assert observations is not None  # estimated here
        length = observations
        demeaned: list[list[float]] = []
        for name in names:
            series = [float(value) for value in returns[name]]
            mean = math.fsum(series) / length
            demeaned.append([value - mean for value in series])
        # S with divisor n, which is the estimator the paper's formulas are in.
        s_rows = [
            [
                math.fsum(a * b for a, b in zip(demeaned[i], demeaned[j], strict=True)) / length
                for j in range(count)
            ]
            for i in range(count)
        ]
        for i in range(count):
            for j in range(i + 1, count):
                s_rows[j][i] = s_rows[i][j]
        scale = math.fsum(s_rows[i][i] for i in range(count)) / count
        frobenius = math.fsum(value * value for row in s_rows for value in row)
        distance = math.fsum(
            (s_rows[i][j] - (scale if i == j else 0.0)) ** 2
            for i in range(count)
            for j in range(count)
        )
        quartic = math.fsum(
            math.fsum(demeaned[i][t] ** 2 for i in range(count)) ** 2 for t in range(length)
        )
        spread = max(0.0, (quartic - length * frobenius) / (length * length))
        intensity = 0.0 if distance == 0.0 else min(spread / distance, 1.0)
        keep = 1.0 - intensity
        rows = tuple(
            tuple(
                keep * s_rows[i][j] + (intensity * scale if i == j else 0.0) for j in range(count)
            )
            for i in range(count)
        )
        return cls(
            names,
            rows,
            currency,
            period,
            source,
            observations,
            parent_id=parent.covariance_id,
            derivation=(
                f"ledoit-wolf 2004: (1 - {intensity!r}) * S + {intensity!r} * {scale!r} * I, "
                f"S = ({length - 1} / {length}) * parent, intensity estimated"
            ),
        )

    @classmethod
    def ewma(
        cls,
        returns: Mapping[str, Sequence[float]],
        *,
        decay: float,
        currency: str,
        period: str,
        source: str,
    ) -> CovarianceMatrix:
        """The exponentially weighted covariance of aligned returns (OFE-002).

        RiskMetrics (J.P. Morgan, *RiskMetrics Technical Document*, 1996): each
        observation weighs ``(1 - decay) * decay ** age``, ``age`` counting back
        from the newest at zero, normalized over the window so the weights sum
        to one; returns are taken as having mean zero, which is the RiskMetrics
        convention and is stated in :attr:`source` with the decay. A half-life
        of ``h`` observations is a decay of ``0.5 ** (1 / h)``.

        Raises:
            AnalyticsValidationError: As :meth:`sample` does, or if ``decay`` is
                not strictly between zero and one.
        """

        factor = _require_finite(decay, "EWMA decay")
        if not 0.0 < factor < 1.0:
            raise AnalyticsValidationError(
                f"EWMA decay is {factor!r}; it must lie strictly between 0 and 1."
            )
        # The same validation as a sample covariance, and the same order.
        shape = cls.sample(returns, currency=currency, period=period, source=source)
        names = shape.assets
        length = shape.observations
        assert length is not None
        raw = [(1.0 - factor) * factor ** (length - 1 - t) for t in range(length)]
        total = math.fsum(raw)
        weights = [weight / total for weight in raw]
        series = [[float(value) for value in returns[name]] for name in names]
        count = len(names)
        rows = [[0.0] * count for _ in range(count)]
        for i in range(count):
            for j in range(i, count):
                value = math.fsum(
                    w * a * b for w, a, b in zip(weights, series[i], series[j], strict=True)
                )
                rows[i][j] = value
                rows[j][i] = value
        return cls(
            names,
            tuple(tuple(row) for row in rows),
            currency,
            period,
            f"{source}; EWMA covariance, decay {factor!r}, zero mean (RiskMetrics 1996)",
            length,
        )

    @classmethod
    def factor_model(
        cls,
        loadings: FactorLoadings,
        factor_covariance: CovarianceMatrix,
        specific_variances: Mapping[str, float],
    ) -> CovarianceMatrix:
        """The asset covariance a factor model implies: ``B F B' + D`` (OFE-002).

        ``B`` is ``loadings`` (assets by factors), ``F`` the covariance of the
        factor returns -- whose "assets" are the factors -- and ``D`` the
        diagonal of specific (idiosyncratic) variances, one per asset. It is
        positive definite whenever every specific variance is positive, however
        few observations ``F`` came from, which is why a large universe is
        modelled this way. Currency and period are the factor covariance's; the
        matrix records it as its parent, and the loadings and the specific
        variances in its derivation.

        Writing ``n^2`` values out is ``O(n^2)`` in time and memory -- 29 s and
        1.3 GB at 4,000 assets. Over a large universe, state the covariance by
        its structure instead (:meth:`FactorStructure.of`, ``O(n k^2)``), which
        construction, :func:`euler_decomposition` and :func:`factor_risk` take
        directly (v3.13, ledger PRF-013); this is its :meth:`FactorStructure.matrix`.

        Raises:
            AnalyticsValidationError: If the factor covariance is not over
                exactly the loadings' factors, a specific variance is missing,
                extra, negative or not finite.
        """

        return FactorStructure.of(loadings, factor_covariance, specific_variances).matrix()

    # -- identity ----------------------------------------------------------- #

    @property
    def covariance_id(self) -> str:
        """The derived identity of this exact claim, in any process."""

        return self._identity

    def _derive_identity(self) -> str:
        return _digest(
            [
                COVARIANCE_SCHEME,
                f"currency={_render(self.currency)}",
                f"period={_render(self.period)}",
                f"source={_render(self.source)}",
                f"observations={_render(self.observations)}",
                f"parent={_render(self.parent_id)}",
                f"derivation={_render(self.derivation)}",
                "assets",
                *(_render(asset) for asset in self.assets),
                "values",
                # map(repr, ...) is _render, applied without a call per value.
                *(",".join(map(repr, row)) for row in self.values),
            ]
        )

    # -- reading ------------------------------------------------------------ #

    def index(self, asset: str) -> int:
        """The position of ``asset`` in :attr:`assets`.

        Raises:
            AnalyticsValidationError: If the matrix does not cover it.
        """

        position = bisect_left(self.assets, asset)
        if position == len(self.assets) or self.assets[position] != asset:
            raise AnalyticsValidationError(
                f"{asset!r} is not covered by covariance {self.covariance_id[:12]}, which "
                f"covers {len(self.assets)} assets. A missing covariance is not zero."
            )
        return position

    def covariance(self, first: str, second: str) -> float:
        """The covariance of two covered assets."""

        return self.values[self.index(first)][self.index(second)]

    def variance(self, asset: str) -> float:
        """One covered asset's variance."""

        position = self.index(asset)
        return self.values[position][position]

    def volatility(self, asset: str) -> float:
        """One covered asset's standard deviation, per :attr:`period`."""

        return math.sqrt(self.variance(asset))

    def as_mapping(self) -> Mapping[str, Mapping[str, float]]:
        """The matrix as a read-only nested mapping, rows and columns sorted."""

        return MappingProxyType(
            {
                row_asset: MappingProxyType(dict(zip(self.assets, row, strict=True)))
                for row_asset, row in zip(self.assets, self.values, strict=True)
            }
        )

    def covers(self, assets: Iterable[str]) -> bool:
        """Whether every one of ``assets`` has a row here."""

        present = set(self.assets)
        return all(asset in present for asset in assets)

    # -- derivation --------------------------------------------------------- #

    def restricted(self, assets: Iterable[str]) -> CovarianceMatrix:
        """The sub-matrix over ``assets``, recorded as derived from this one.

        Exact: the entries are the parent's entries, not re-estimated. A
        restriction to the full set of assets returns this matrix unchanged.

        Raises:
            AnalyticsValidationError: If ``assets`` is empty or names an asset
                this matrix does not cover.
        """

        wanted = sorted(set(assets))
        if not wanted:
            raise AnalyticsValidationError(
                "A covariance restricted to no assets describes nothing."
            )
        positions = [self.index(asset) for asset in wanted]
        if len(wanted) == len(self.assets):
            return self
        return CovarianceMatrix(
            tuple(wanted),
            tuple(tuple(self.values[row][column] for column in positions) for row in positions),
            self.currency,
            self.period,
            self.source,
            self.observations,
            parent_id=self.covariance_id,
            derivation=f"restricted to {len(wanted)} of {len(self.assets)} assets",
        )

    def with_ridge(self, delta: float) -> CovarianceMatrix:
        """``C + delta * I``: an explicit, recorded regularization.

        Adds ``delta`` to every variance and leaves every covariance alone, which
        makes any positive semidefinite matrix positive definite with smallest
        eigenvalue at least ``delta``. It changes the risk model -- every
        variance rises by ``delta`` -- which is why it is a derivation with its
        own identity rather than something a solver does quietly.

        Raises:
            AnalyticsValidationError: If ``delta`` is not a positive finite
                number.
        """

        amount = _require_finite(delta, "ridge delta")
        if amount <= 0.0:
            raise AnalyticsValidationError(f"ridge delta is {amount!r}; it must be positive.")
        count = len(self.assets)
        rows = tuple(
            tuple(
                self.values[row][column] + amount if row == column else self.values[row][column]
                for column in range(count)
            )
            for row in range(count)
        )
        return CovarianceMatrix(
            self.assets,
            rows,
            self.currency,
            self.period,
            self.source,
            self.observations,
            parent_id=self.covariance_id,
            derivation=f"ridge: C + {amount!r} * I",
        )

    def with_diagonal_shrinkage(self, intensity: float) -> CovarianceMatrix:
        """``(1 - a) * C + a * diag(C)``: shrinkage toward the diagonal, recorded.

        Every covariance is multiplied by ``1 - a`` and every variance is kept.
        ``a`` is the caller's choice and enters the identity; nothing here
        estimates an "optimal" intensity, because every published estimator of
        one rests on assumptions about the return distribution this module
        cannot check. Shrinkage toward the diagonal does not repair a zero
        variance -- only :meth:`with_ridge` does.

        Raises:
            AnalyticsValidationError: If ``intensity`` is not in ``(0, 1]``.
        """

        amount = _require_finite(intensity, "shrinkage intensity")
        if not 0.0 < amount <= 1.0:
            raise AnalyticsValidationError(
                f"shrinkage intensity is {amount!r}; it must lie in (0, 1]."
            )
        keep = 1.0 - amount
        count = len(self.assets)
        rows = tuple(
            tuple(
                self.values[row][column] if row == column else keep * self.values[row][column]
                for column in range(count)
            )
            for row in range(count)
        )
        # ``keep * x`` is computed once per cell and reused for its mirror, so the
        # result is exactly symmetric by construction.
        return CovarianceMatrix(
            self.assets,
            rows,
            self.currency,
            self.period,
            self.source,
            self.observations,
            parent_id=self.covariance_id,
            derivation=f"diagonal shrinkage: (1 - {amount!r}) * C + {amount!r} * diag(C)",
        )

    # -- definiteness ------------------------------------------------------- #

    def definiteness(self) -> Definiteness:
        """Classify the matrix by a pivoted Cholesky factorization. See the module docstring."""

        return _definiteness(self.assets, self.values)

    def require_positive_definite(self, purpose: str) -> Definiteness:
        """Refuse a matrix that is not positive definite, saying why and what to do.

        Returns the :class:`Definiteness` evidence when it is.

        Raises:
            AnalyticsValidationError: If the matrix is singular or indefinite. A
                singular one names its rank and any zero-variance assets; the
                remedy -- removing a redundant asset, or an explicit, recorded
                :meth:`with_ridge` or :meth:`with_diagonal_shrinkage` -- is the
                caller's to choose.
        """

        evidence = self.definiteness()
        if evidence.kind is DefinitenessKind.POSITIVE_DEFINITE:
            return evidence
        if evidence.kind is DefinitenessKind.INDEFINITE:
            raise AnalyticsValidationError(
                f"{purpose} needs a positive definite covariance, and covariance "
                f"{self.covariance_id[:12]} is indefinite: some portfolio would have negative "
                "variance under it, so it is not a covariance of anything. It was not "
                "regularized, because which correction is right is the caller's decision."
            )
        zero = (
            f" Zero-variance assets: {list(evidence.zero_variance_assets)}."
            if evidence.zero_variance_assets
            else ""
        )
        raise AnalyticsValidationError(
            f"{purpose} needs a positive definite covariance, and covariance "
            f"{self.covariance_id[:12]} is singular (rank {evidence.rank} of "
            f"{len(self.assets)}): some non-zero portfolio has zero variance under it, so the "
            f"solution need not be unique.{zero} Remove the redundant assets, or derive an "
            "explicit, recorded regularization with with_ridge() or "
            "with_diagonal_shrinkage()."
        )

    def correlation(self) -> CorrelationMatrix:
        """The correlation this covariance implies, carrying its measurement basis.

        Raises:
            AnalyticsValidationError: If an asset has zero variance -- every
                correlation with it is undefined, and zero would read as
                "measured, and unrelated".
        """

        zero = [
            asset for index, asset in enumerate(self.assets) if self.values[index][index] == 0.0
        ]
        if zero:
            raise AnalyticsValidationError(
                f"{zero} have a constant return series, so every correlation with them is "
                "undefined. Zero would read as 'measured, and unrelated'."
            )
        return CorrelationMatrix(
            assets=self.assets,
            values=_correlation_rows(self.values),
            currency=self.currency,
            period=self.period,
            observations=self.observations,
            covariance_id=self.covariance_id,
        )


@dataclass(frozen=True, slots=True)
class CorrelationMatrix:
    """Correlation of *returns*, derived from one covariance and naming it.

    Derived rather than estimated independently, so a correlation and a
    covariance can never disagree about the same pair. "Correlation" without a
    basis is not a measurement; this one is always of returns, measured in
    :attr:`currency`, per :attr:`period`, over :attr:`observations`. The
    correlation of two strategies' *exposures* is a different quantity with its
    own type (:class:`~alphalab.analytics.cross_strategy.ExposureSimilarity`).

    Attributes:
        assets: What was correlated, sorted.
        values: ``values[i][j]``, in ``[-1, 1]`` up to rounding, ``1`` on the
            diagonal.
        currency: The currency the underlying returns were measured in.
        period: The period each return spans.
        observations: The sample size, or ``None`` for a supplied covariance.
        covariance_id: The covariance it was derived from.
    """

    assets: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    currency: str
    period: str
    observations: int | None
    covariance_id: str

    def correlation(self, first: str, second: str) -> float:
        """The correlation of two covered assets."""

        try:
            row = self.assets.index(first)
            column = self.assets.index(second)
        except ValueError:
            raise AnalyticsValidationError(
                f"{first!r} or {second!r} is not in this correlation matrix."
            ) from None
        return self.values[row][column]

    def as_mapping(self) -> Mapping[str, Mapping[str, float]]:
        """The matrix as a read-only nested mapping."""

        return MappingProxyType(
            {
                row_asset: MappingProxyType(dict(zip(self.assets, row, strict=True)))
                for row_asset, row in zip(self.assets, self.values, strict=True)
            }
        )


# --------------------------------------------------------------------------- #
# Factor loadings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FactorLoadings:
    """Each asset's loading on each factor, from a stated model.

    Loadings are the output of a factor model, and which model is the caller's
    decision (ADR-0038 decision 6); AlphaLab estimates none on the execution
    path. :func:`alphalab.factor_library.loadings_from_panels` builds one from
    the factor library's cross-sectional panels, carrying each feature's
    version as its lineage.

    Attributes:
        factors: Factor identifiers, sorted.
        assets: Assets, sorted.
        values: ``values[i][k]`` is ``assets[i]``'s loading on ``factors[k]``.
            Complete: a hole is refused, because an absent loading is not zero.
        source: The model, in words -- a vendor model and version, or how the
            loadings were computed.
        lineage: Factor -> what that factor's loadings were computed from (a
            feature version, a model's factor id). One entry per factor.
        as_of: The instant the loadings describe, or ``None`` when undated.
    """

    factors: tuple[str, ...]
    assets: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    source: str
    lineage: Mapping[str, str]
    as_of: float | None

    def __post_init__(self) -> None:
        for factor in self.factors:
            _require_text(factor, "factor")
        for asset in self.assets:
            _require_text(asset, "asset")
        if not self.factors or not self.assets:
            raise AnalyticsValidationError(
                "Factor loadings over no factor or no asset describe nothing."
            )
        if list(self.factors) != sorted(set(self.factors)):
            raise AnalyticsValidationError(
                "FactorLoadings.factors must be sorted and unique; use FactorLoadings.of."
            )
        if list(self.assets) != sorted(set(self.assets)):
            raise AnalyticsValidationError(
                "FactorLoadings.assets must be sorted and unique; use FactorLoadings.of."
            )
        if len(self.values) != len(self.assets) or any(
            len(row) != len(self.factors) for row in self.values
        ):
            raise AnalyticsValidationError(
                "FactorLoadings.values must hold one row per asset and one column per factor."
            )
        rows = tuple(
            tuple(
                _require_finite(value, f"loading of {asset!r} on {factor!r}")
                for factor, value in zip(self.factors, row, strict=True)
            )
            for asset, row in zip(self.assets, self.values, strict=True)
        )
        object.__setattr__(self, "values", rows)
        _require_text(self.source, "FactorLoadings.source")
        if sorted(self.lineage) != list(self.factors):
            raise AnalyticsValidationError(
                f"FactorLoadings.lineage names {sorted(self.lineage)} for factors "
                f"{list(self.factors)}. Every factor states what it was computed from, and "
                "nothing else is named."
            )
        for factor in self.factors:
            _require_text(self.lineage[factor], f"lineage of factor {factor!r}")
        object.__setattr__(
            self,
            "lineage",
            MappingProxyType({factor: self.lineage[factor] for factor in self.factors}),
        )
        object.__setattr__(self, "as_of", _require_instant(self.as_of, "FactorLoadings.as_of"))

    @classmethod
    def of(
        cls,
        loadings: Mapping[str, Mapping[str, float]],
        *,
        source: str,
        lineage: Mapping[str, str],
        as_of: float | None,
    ) -> FactorLoadings:
        """Build from ``asset -> factor -> loading``, which must be a full rectangle.

        Raises:
            AnalyticsValidationError: If any asset lacks a loading on any factor
                another asset has. The asset and factor are named.
        """

        assets = sorted(loadings)
        factors = sorted({factor for row in loadings.values() for factor in row})
        missing = [
            f"{asset}/{factor}"
            for asset in assets
            for factor in factors
            if factor not in loadings[asset]
        ]
        if missing:
            raise AnalyticsValidationError(
                f"No loading supplied for {missing[:5]}{' and more' if len(missing) > 5 else ''}. "
                "An absent loading is not zero; zero is a measurement."
            )
        return cls(
            tuple(factors),
            tuple(assets),
            tuple(tuple(loadings[asset][factor] for factor in factors) for asset in assets),
            source,
            lineage,
            as_of,
        )

    @property
    def loadings_id(self) -> str:
        """The derived identity of these loadings."""

        return _digest(
            [
                FACTOR_LOADINGS_SCHEME,
                f"source={_render(self.source)}",
                f"as_of={_render(self.as_of)}",
                "factors",
                *(f"{_render(factor)}={_render(self.lineage[factor])}" for factor in self.factors),
                "assets",
                *(
                    f"{_render(asset)}=" + ",".join(_render(value) for value in row)
                    for asset, row in zip(self.assets, self.values, strict=True)
                ),
            ]
        )

    def require_factor(self, factor: str) -> int:
        """The column of ``factor``, refusing one this model does not have."""

        position = bisect_left(self.factors, factor)
        if position == len(self.factors) or self.factors[position] != factor:
            raise AnalyticsValidationError(
                f"Factor {factor!r} is not in loadings {self.loadings_id[:12]}, which carry "
                f"{list(self.factors)}."
            )
        return position

    def require_covers(self, assets: Iterable[str], purpose: str) -> None:
        """Refuse unless every one of ``assets`` has loadings, naming the ones that do not."""

        present = set(self.assets)
        missing = sorted(set(assets) - present)
        if missing:
            raise self._uncovered(missing, purpose)

    def _uncovered(self, missing: list[str], purpose: str) -> AnalyticsValidationError:
        return AnalyticsValidationError(
            f"{purpose} needs factor loadings for {missing}, and loadings "
            f"{self.loadings_id[:12]} carry none. An asset with no loading is not an asset "
            "with a zero loading."
        )

    def _position(self, asset: str) -> int:
        """The row of one asset, by bisection: a lookup per asset stays logarithmic."""

        position = bisect_left(self.assets, asset)
        if position == len(self.assets) or self.assets[position] != asset:
            raise self._uncovered([asset], "A loading")
        return position

    def loading(self, asset: str, factor: str) -> float:
        """One asset's loading on one factor."""

        return self.values[self._position(asset)][self.require_factor(factor)]

    def row(self, asset: str) -> tuple[tuple[str, float], ...]:
        """``((factor, loading), ...)`` for one covered asset, factors sorted."""

        return tuple(zip(self.factors, self.values[self._position(asset)], strict=True))


@dataclass(frozen=True, slots=True)
class FactorStructure:
    """A covariance stated by its factor model, ``B F B' + D`` (ledger PRF-005, PRF-013).

    :meth:`CovarianceMatrix.factor_model` writes ``B F B' + D`` out as a dense
    matrix, because most consumers of a covariance read one -- and the dense
    matrix forgets what made it cheap: ``k`` factors and a diagonal. This is that
    structure. Attached to the matrix it implies (:attr:`CovarianceMatrix.factors`)
    it lets a computation that can use it -- portfolio construction over a large
    universe -- solve its linear systems in ``O(n k^2)`` rather than ``O(n^3)``
    (v3.12).

    Since v3.13 it is also a covariance in its own right (ledger PRF-013).
    Writing ``n^2`` values out costs ``O(n^2)`` in time and memory -- 29 s and
    1.3 GB at 4,000 assets -- so a large universe never reached the
    ``O(n k^2)`` solver through the public path. Built by :meth:`of`, a
    structure costs ``O(n k^2)`` and holds nothing ``O(n^2)``;
    :class:`~alphalab.portfolio_optimizer.construction.ConstructionProblem`,
    :func:`euler_decomposition` and :func:`factor_risk` take it directly; and
    the dense values are written out only when something asks for them --
    :attr:`implied`, :meth:`rows` or :meth:`matrix`, each of which writes them
    out again, because a frozen value is not changed once built: keep what one
    returns.

    A structure and the matrix it implies are two statements of one covariance,
    and each has its own identity: :attr:`covariance_id` here is derived from the
    factor covariance, the loadings and the specific variances, and the
    matrix's renders its ``n^2`` values besides.

    Attributes:
        loadings: ``B``: each asset's loading on each factor. Its assets, sorted,
            are the structure's.
        factor_covariance: ``F``: the covariance of the factor returns, over
            exactly the loadings' factors. Its currency and period are the
            structure's.
        specific: ``D``: one specific variance per asset, in the loadings'
            asset order. Finite and not negative.

    Raises:
        AnalyticsValidationError: If the factor covariance is not over the
            loadings' factors, or the specific variances are not one per asset,
            finite and non-negative.
    """

    loadings: FactorLoadings
    factor_covariance: CovarianceMatrix
    specific: tuple[float, ...]
    #: Derived once, here, from the three above in ``O(n k^2)`` -- each factor's
    #: loadings across the assets, each asset's variance, the natural-order
    #: Cholesky pivots, the evidence they give and the identity. None of it is
    #: compared or printed.
    columns: tuple[tuple[float, ...], ...] = field(init=False, repr=False, compare=False)
    _variances: tuple[float, ...] = field(init=False, repr=False, compare=False)
    _pivots: tuple[float, ...] | None = field(init=False, repr=False, compare=False)
    _evidence: Definiteness | None = field(init=False, repr=False, compare=False)
    _identity: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.factor_covariance.assets != self.loadings.factors:
            raise AnalyticsValidationError(
                f"The factor covariance is over {list(self.factor_covariance.assets)} and the "
                f"loadings name the factors {list(self.loadings.factors)}; a factor model needs "
                "the same."
            )
        if len(self.specific) != len(self.loadings.assets):
            raise AnalyticsValidationError(
                f"{len(self.specific)} specific variances for {len(self.loadings.assets)} assets; "
                "a factor model needs one per asset."
            )
        values = tuple(
            _require_finite(value, f"specific variance of {asset!r}")
            for asset, value in zip(self.loadings.assets, self.specific, strict=True)
        )
        negative = [
            asset for asset, value in zip(self.loadings.assets, values, strict=True) if value < 0.0
        ]
        if negative:
            raise AnalyticsValidationError(
                f"The specific variances of {negative[:5]} are negative; a variance is not."
            )
        object.__setattr__(self, "specific", values)
        b = self.loadings.values
        f = self.factor_covariance.values
        k = len(self.loadings.factors)
        object.__setattr__(
            self, "columns", tuple(tuple(row[factor] for row in b) for factor in range(k))
        )
        # Each diagonal cell exactly as the dense rows compute it, so that
        # variance() and matrix().variance() are the same float.
        object.__setattr__(
            self,
            "_variances",
            tuple(
                math.fsum(
                    math.fsum(b[i][g] * f[g][h] for g in range(k)) * b[i][h] for h in range(k)
                )
                + values[i]
                for i in range(len(b))
            ),
        )
        pivots = factor_cholesky_pivots(b, f, values)
        object.__setattr__(self, "_pivots", pivots)
        object.__setattr__(self, "_evidence", self._definiteness(pivots))
        object.__setattr__(self, "_identity", self._derive_identity())

    @classmethod
    def of(
        cls,
        loadings: FactorLoadings,
        factor_covariance: CovarianceMatrix,
        specific_variances: Mapping[str, float],
    ) -> FactorStructure:
        """A factor model given its specific variances by asset (v3.13, ledger PRF-013).

        The structure :meth:`CovarianceMatrix.factor_model` writes out, not
        written out: ``O(n k^2)``, where the dense matrix is ``O(n^2)``.

        Raises:
            AnalyticsValidationError: If the factor covariance is not over
                exactly the loadings' factors, a specific variance is missing,
                extra, negative or not finite.
        """

        if factor_covariance.assets != loadings.factors:
            raise AnalyticsValidationError(
                f"The factor covariance is over {list(factor_covariance.assets)} and the loadings "
                f"name the factors {list(loadings.factors)}; a factor model needs the same."
            )
        held = set(specific_variances)
        wanted = set(loadings.assets)
        if held != wanted:
            raise AnalyticsValidationError(
                "Specific variances must cover exactly the loadings' assets: missing "
                f"{sorted(wanted - held)}, outside {sorted(held - wanted)}. A missing specific "
                "variance is not zero."
            )
        specific: list[float] = []
        for asset in loadings.assets:
            value = _require_finite(specific_variances[asset], f"specific variance of {asset!r}")
            if value < 0.0:
                raise AnalyticsValidationError(
                    f"The specific variance of {asset!r} is {value!r}; a variance is not negative."
                )
            specific.append(value)
        return cls(loadings, factor_covariance, tuple(specific))

    # -- the covariance it states ------------------------------------------- #

    @property
    def assets(self) -> tuple[str, ...]:
        """The assets, sorted: the loadings'."""

        return self.loadings.assets

    @property
    def currency(self) -> str:
        """The currency every return is measured in: the factor covariance's."""

        return self.factor_covariance.currency

    @property
    def period(self) -> str:
        """The return period each figure is per: the factor covariance's."""

        return self.factor_covariance.period

    @property
    def source(self) -> str:
        """Where the covariance came from, as the matrix it implies records it."""

        return f"factor model over {self.loadings.source}"

    @property
    def covariance_id(self) -> str:
        """The derived identity of this statement of the covariance, in any process.

        Not :meth:`matrix`'s :attr:`CovarianceMatrix.covariance_id`, which renders
        the ``n^2`` values besides: the two are different statements of one
        covariance.
        """

        return self._identity

    def _derive_identity(self) -> str:
        return _digest(
            [
                FACTOR_STRUCTURE_SCHEME,
                f"factor_covariance={self.factor_covariance.covariance_id}",
                f"loadings={self.loadings.loadings_id}",
                "specific",
                *(
                    f"{asset}={_render(value)}"
                    for asset, value in zip(self.loadings.assets, self.specific, strict=True)
                ),
            ]
        )

    def index(self, asset: str) -> int:
        """The position of ``asset`` in :attr:`assets`.

        Raises:
            AnalyticsValidationError: If the structure does not cover it.
        """

        assets = self.loadings.assets
        position = bisect_left(assets, asset)
        if position == len(assets) or assets[position] != asset:
            raise AnalyticsValidationError(
                f"{asset!r} is not covered by factor structure {self.covariance_id[:12]}, which "
                f"covers {len(assets)} assets. A missing covariance is not zero."
            )
        return position

    def variance(self, asset: str) -> float:
        """One covered asset's variance, ``b_i' F b_i + d_i``: the dense diagonal, to the bit."""

        return self._variances[self.index(asset)]

    def volatility(self, asset: str) -> float:
        """One covered asset's standard deviation, per :attr:`period`."""

        return math.sqrt(self.variance(asset))

    @property
    def derivation(self) -> str:
        """The derivation a matrix this structure implies records: what made it."""

        rendered = _digest(
            [
                "specific",
                *(
                    f"{asset}={_render(value)}"
                    for asset, value in zip(self.loadings.assets, self.specific, strict=True)
                ),
            ]
        )
        return (
            f"factor model: B F B' + D, loadings {self.loadings.loadings_id}, specific variances "
            f"{rendered}"
        )

    # -- written out, when asked for ---------------------------------------- #

    @property
    def implied(self) -> tuple[tuple[float, ...], ...]:
        """``B F B' + D`` written out: ``O(n^2)`` on every read, so keep the result."""

        return self._write_out()

    def rows(self) -> tuple[tuple[float, ...], ...]:
        """``B F B' + D`` written out: the matrix :meth:`CovarianceMatrix.factor_model` builds."""

        return self._write_out()

    def matrix(self) -> CovarianceMatrix:
        """The dense :class:`CovarianceMatrix` this structure implies, ``O(n^2)`` on every call.

        Exactly what :meth:`CovarianceMatrix.factor_model` returns for the same
        model -- the same values, provenance and identity -- carrying this
        structure.
        """

        factor_covariance = self.factor_covariance
        return CovarianceMatrix(
            tuple(self.loadings.assets),
            self._write_out(),
            factor_covariance.currency,
            factor_covariance.period,
            self.source,
            None,
            parent_id=factor_covariance.covariance_id,
            derivation=self.derivation,
            factors=self,
        )

    def _write_out(self) -> tuple[tuple[float, ...], ...]:
        """Each cell computed once, by exactly-rounded sums, and written to both halves."""

        b = self.loadings.values
        f = self.factor_covariance.values
        k = len(self.loadings.factors)
        count = len(self.loadings.assets)
        # B F, once; then (B F) B' for each pair, written to both cells.
        bf = [
            [math.fsum(b[i][g] * f[g][h] for g in range(k)) for h in range(k)] for i in range(count)
        ]
        rows = [[0.0] * count for _ in range(count)]
        for i in range(count):
            for j in range(i, count):
                value = math.fsum(bf[i][h] * b[j][h] for h in range(k))
                if i == j:
                    value += self.specific[i]
                rows[i][j] = value
                rows[j][i] = value
        return tuple(tuple(row) for row in rows)

    def require_implies(self, matrix: CovarianceMatrix) -> None:
        """Refuse a matrix this structure does not imply, value for value.

        Raises:
            AnalyticsValidationError: If the assets, units, parent, derivation or
                any value differ from what :meth:`CovarianceMatrix.factor_model`
                would build from this structure.
        """

        factor_covariance = self.factor_covariance
        if matrix.assets != self.loadings.assets:
            raise AnalyticsValidationError(
                "A factor structure attached to a covariance must be over the same assets."
            )
        if (matrix.currency, matrix.period) != (
            factor_covariance.currency,
            factor_covariance.period,
        ):
            raise AnalyticsValidationError(
                "A factor structure's covariance is in its factor covariance's currency and period."
            )
        if (
            matrix.parent_id != factor_covariance.covariance_id
            or matrix.derivation != self.derivation
        ):
            raise AnalyticsValidationError(
                "A covariance carrying a factor structure records the factor covariance as its "
                "parent and the structure's derivation; this one does not."
            )
        if matrix.values != self.rows():
            raise AnalyticsValidationError(
                "The attached factor structure does not imply these values: B F B' + D differs "
                "from the matrix."
            )

    def pivots(self) -> tuple[float, ...] | None:
        """:func:`factor_cholesky_pivots` of this structure, computed when it was built."""

        return self._pivots

    def definiteness(self) -> Definiteness | None:
        """Positive definiteness established from the structure, or ``None`` when it is not.

        ``B F B' + D`` is positive definite when every specific variance is
        positive and ``F`` is positive semidefinite; the evidence is the
        natural-order Cholesky pivots (:func:`factor_cholesky_pivots`) against
        the same ``n * eps * max(diag)`` floor the dense factorization uses.
        ``largest_pivot`` is the largest variance -- exactly the first pivot
        diagonal pivoting takes -- and ``smallest_pivot`` the smallest
        natural-order pivot, which can differ from the pivoted factorization's
        last pivot by the ordering alone. ``None`` -- never a negative verdict --
        when the structure cannot establish definiteness by itself: the dense
        factorization then decides.
        """

        return self._evidence

    def _definiteness(self, pivots: tuple[float, ...] | None) -> Definiteness | None:
        if pivots is None:
            return None
        count = len(self._variances)
        largest = max(self._variances)
        floor = count * _EPSILON * largest
        smallest = min(pivots)
        if smallest <= floor:
            return None
        return Definiteness(DefinitenessKind.POSITIVE_DEFINITE, count, smallest, largest, floor, ())


def portfolio_factor_exposures(
    weights: Mapping[str, float], loadings: FactorLoadings
) -> Mapping[str, float]:
    """``sum_i w_i * beta_if`` for every factor: a book's factor exposure.

    Every weighted asset must carry loadings; an asset without them is refused
    rather than treated as unexposed. Assets are read in sorted order and
    factors in sorted order, through :func:`_accumulate_exposures` -- the one
    implementation of this sum.

    Raises:
        AnalyticsValidationError: If an asset with a weight has no loadings.
    """

    loadings.require_covers(weights, "A factor exposure")
    return MappingProxyType(
        _accumulate_exposures((weights[asset], loadings.row(asset)) for asset in sorted(weights))
    )


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def _require_dimension(value: object) -> str:
    text = _require_text(value, "Classification.dimension")
    if not text[0].isalpha() or not all(
        character.islower() or character.isdigit() or character == "_" for character in text
    ):
        raise AnalyticsValidationError(
            f"Classification.dimension {text!r} must be a lowercase identifier such as "
            "'sector' or 'country'."
        )
    return text


@dataclass(frozen=True, slots=True)
class Classification:
    """Which bucket each asset falls in along one dimension, and who said so.

    AlphaLab ships no taxonomy (ADR-0027). A sector comes from the instrument
    registry an operator maintains -- :func:`alphalab.api.sector_classification`
    reads it -- and a country from whoever the caller trusts for one, because
    AlphaLab holds no country anywhere. Either way the classification names its
    ``source``, which enters its identity.

    Attributes:
        dimension: What is being classified: ``"sector"``, ``"country"``,
            ``"currency"``, or any lowercase identifier.
        labels: Asset -> label. Only classified assets appear: an unclassified
            asset is absent, never labelled ``"UNKNOWN"``.
        source: Who classified, in words. Required.
        as_of: The instant the classification is in effect for, or ``None``.
    """

    dimension: str
    labels: Mapping[str, str]
    source: str
    as_of: float | None

    def __post_init__(self) -> None:
        _require_dimension(self.dimension)
        if not self.labels:
            raise AnalyticsValidationError(
                f"A {self.dimension} classification of no asset classifies nothing."
            )
        ordered = {
            _require_text(asset, "classified asset"): _require_text(
                label, f"{self.dimension} label of {asset!r}"
            )
            for asset, label in sorted(self.labels.items())
        }
        object.__setattr__(self, "labels", MappingProxyType(ordered))
        _require_text(self.source, "Classification.source")
        object.__setattr__(self, "as_of", _require_instant(self.as_of, "Classification.as_of"))

    @property
    def classification_id(self) -> str:
        """The derived identity of this classification."""

        return _digest(
            [
                CLASSIFICATION_SCHEME,
                f"dimension={_render(self.dimension)}",
                f"source={_render(self.source)}",
                f"as_of={_render(self.as_of)}",
                *(f"{_render(asset)}={_render(label)}" for asset, label in self.labels.items()),
            ]
        )

    @property
    def buckets(self) -> tuple[str, ...]:
        """Every distinct label, sorted."""

        return tuple(sorted(set(self.labels.values())))

    def members(self, label: str) -> tuple[str, ...]:
        """The assets classified as ``label``, sorted. Empty for an unused label."""

        return tuple(asset for asset, value in self.labels.items() if value == label)

    def require_covers(self, assets: Iterable[str], purpose: str) -> None:
        """Refuse unless every one of ``assets`` is classified, naming those that are not.

        The refusal is the whole point: an unclassified asset silently assigned
        to a default bucket would make every limit on that bucket, and every
        other bucket, wrong by an amount nobody could see.
        """

        missing = sorted(set(assets) - set(self.labels))
        if missing:
            raise self._unclassified(missing, purpose)

    def _unclassified(self, missing: list[str], purpose: str) -> AnalyticsValidationError:
        return AnalyticsValidationError(
            f"{purpose} needs a {self.dimension} for {missing}, and classification "
            f"{self.classification_id[:12]} (from {self.source!r}) has none. An "
            "unclassified asset is not put in a default bucket; classify it, with a source."
        )

    def label(self, asset: str) -> str:
        """The label of one classified asset."""

        if asset not in self.labels:
            raise self._unclassified([asset], "A classification lookup")
        return self.labels[asset]


# --------------------------------------------------------------------------- #
# The Euler decomposition
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RiskContributions:
    """Portfolio volatility, and each asset's share of it, by Euler's theorem.

    Volatility ``sigma(w) = sqrt(w' C w)`` is homogeneous of degree one in the
    weights, so ``sigma = sum_i w_i * d sigma / d w_i``. That identity is what
    makes this a *decomposition* rather than a list of per-asset volatilities.

    Attributes:
        weights: The weights decomposed, sorted by asset. A fraction of the
            capital the covariance's returns are measured against.
        variance: ``w' C w``, per :attr:`CovarianceMatrix.period`.
        volatility: ``sqrt(variance)``.
        marginal: ``d sigma / d w_i = (C w)_i / sigma`` -- the volatility one
            more unit of weight in asset ``i`` would add, at the margin.
        total: ``w_i * marginal_i`` -- asset ``i``'s contribution. Sums to
            :attr:`volatility` up to floating-point rounding. Negative for a
            position that hedges the rest, and kept negative.
        relative: ``total_i / volatility`` -- the share of risk. Sums to one.
        covariance_id: The covariance the figures were computed under: a
            matrix's :attr:`CovarianceMatrix.covariance_id`, or a structure's
            :attr:`FactorStructure.covariance_id`.
    """

    weights: Mapping[str, float]
    variance: float
    volatility: float
    marginal: Mapping[str, float]
    total: Mapping[str, float]
    relative: Mapping[str, float]
    covariance_id: str

    @property
    def reconciliation_residual(self) -> float:
        """``fsum(total) - volatility``: what floating point left unreconciled."""

        return math.fsum(self.total.values()) - self.volatility


def euler_decomposition(
    weights: Mapping[str, float], covariance: CovarianceMatrix | FactorStructure
) -> RiskContributions:
    """Decompose the volatility of ``weights`` under ``covariance``.

    Assets with a weight must be covered by the covariance; a covariance over a
    larger universe is fine and the rest of it is not read.

    A covariance stated by its :class:`FactorStructure` is decomposed through
    its factors, in ``O(n k^2)`` and without writing it out -- ``(C w)_i`` is
    ``b_i' F (B' w) + d_i w_i`` -- by exactly-rounded sums (v3.13, ledger
    PRF-013). The figures are those of the matrix it implies to rounding, not to
    the bit: the dense sums are v3.3's, kept so that no published number moved.

    Raises:
        AnalyticsValidationError: If ``weights`` is empty, holds a non-finite
            weight, names an asset the covariance does not cover, or has zero
            volatility -- there is then no risk to decompose, and every
            contribution would be zero over zero rather than an even split.
    """

    if not weights:
        raise AnalyticsValidationError(
            "A risk decomposition of no weights is undefined, which is a refusal rather than zero."
        )
    names = sorted(weights)
    values = [_require_finite(weights[name], f"weight of {name!r}") for name in names]
    positions = [covariance.index(name) for name in names]
    if isinstance(covariance, FactorStructure):
        terms = _structured_euler(values, positions, covariance)
    else:
        rows = [[covariance.values[row][column] for column in positions] for row in positions]
        terms = _euler(values, rows)
    if not terms.contributions:
        raise AnalyticsValidationError(
            "Portfolio volatility is zero, so there is no risk to decompose. Every "
            "contribution would be zero over zero rather than an even split."
        )
    return RiskContributions(
        weights=MappingProxyType(dict(zip(names, values, strict=True))),
        variance=terms.variance,
        volatility=terms.volatility,
        marginal=MappingProxyType(
            {
                name: exposure / terms.volatility
                for name, exposure in zip(names, terms.exposures, strict=True)
            }
        ),
        total=MappingProxyType(dict(zip(names, terms.contributions, strict=True))),
        relative=MappingProxyType(
            {
                name: contribution / terms.volatility
                for name, contribution in zip(names, terms.contributions, strict=True)
            }
        ),
        covariance_id=covariance.covariance_id,
    )


# --------------------------------------------------------------------------- #
# Factor risk, and exchange rates as factors (v3.13)
# --------------------------------------------------------------------------- #


def currency_loadings(
    denominations: Mapping[str, str],
    reporting_currency: str,
    *,
    as_of: float | None,
) -> FactorLoadings:
    """Each asset's exposure to the exchange rate its value moves with, as loadings.

    An asset denominated in currency ``c`` and measured in the reporting
    currency ``R`` returns ``(1 + r_local)(1 + f_c) - 1`` in ``R``, ``f_c`` being
    the return of one unit of ``c`` in ``R``. To first order that is ``r_local +
    f_c``: the asset loads ``1`` on the factor ``FX:c``
    (:data:`EXCHANGE_RATE_FACTOR_PREFIX`) and ``0`` on every other, and an asset
    in ``R`` itself loads ``0`` on all of them. The cross term ``r_local f_c`` is
    second order and is left to the specific variance -- as is ``r_local`` itself
    unless other factors carry it.

    A factor covariance over these factors is the caller's: the covariance of
    the exchange rates' returns, measured over the same periods as the assets'
    -- AlphaLab supplies no rate (ADR-0020). :meth:`CovarianceMatrix.factor_model`
    then builds the asset covariance, and :func:`factor_risk` says how much of a
    book's volatility the exchange rates carry. To model them beside other
    factors, join these rows with the other model's into one
    :meth:`FactorLoadings.of`.

    Args:
        denominations: Asset -> the ISO 4217 code its value is in.
        reporting_currency: ``R``, the code every return is measured in.
        as_of: The instant the denominations describe, or ``None``.

    Raises:
        AnalyticsValidationError: If an asset or code is blank, a code is not
            three upper-case letters, or no asset is denominated outside the
            reporting currency -- there is then no exchange rate to model.
    """

    def code(value: object, what: str) -> str:
        text = _require_text(value, what)
        if len(text) != 3 or not text.isascii() or not text.isalpha() or not text.isupper():
            raise AnalyticsValidationError(
                f"{what} {text!r} is not an ISO 4217 code: three upper-case letters."
            )
        return text

    reporting = code(reporting_currency, "The reporting currency")
    held = {
        _require_text(asset, "asset"): code(currency, f"The currency of {asset!r}")
        for asset, currency in denominations.items()
    }
    foreign = sorted({currency for currency in held.values() if currency != reporting})
    if not foreign:
        raise AnalyticsValidationError(
            f"Every asset is denominated in {reporting}, the reporting currency: there is no "
            "exchange rate to model."
        )
    factors = [f"{EXCHANGE_RATE_FACTOR_PREFIX}{currency}" for currency in foreign]
    return FactorLoadings.of(
        {
            asset: {
                factor: 1.0 if factor == f"{EXCHANGE_RATE_FACTOR_PREFIX}{currency}" else 0.0
                for factor in factors
            }
            for asset, currency in held.items()
        },
        source=f"exchange-rate exposure by denomination, reported in {reporting}",
        lineage={
            factor: f"the return of one {factor[len(EXCHANGE_RATE_FACTOR_PREFIX) :]} in {reporting}"
            for factor in factors
        },
        as_of=as_of,
    )


@dataclass(frozen=True, slots=True)
class FactorRisk:
    """A book's volatility, divided among its covariance's factors and specific risk.

    Under a factor model ``C = B F B' + D`` the variance of weights ``w`` is
    ``b' F b + sum_i w_i^2 D_i`` with ``b = B' w``, the book's factor exposures.
    Volatility is homogeneous of degree one in the weights, so by Euler's
    theorem it is the sum of ``b_k (F b)_k / sigma`` over the factors and
    ``w_i^2 D_i / sigma`` over the assets -- each factor's contribution, and
    each asset's specific contribution. A contribution is negative where a
    factor hedges the rest of the book, and kept negative.

    Attributes:
        covariance_id: The factor-structured covariance read: a structure's
            :attr:`FactorStructure.covariance_id`, or the matrix's that carried it.
        currency: What every return was measured in.
        period: What each variance is per.
        volatility: ``sqrt(w' C w)``, per period.
        exposures: Factor -> ``b_k``.
        factors: Factor -> its contribution to :attr:`volatility`.
        specific: Asset -> its specific contribution, for every weighted asset.
        factor_total: The factors' contributions, summed exactly-rounded.
        specific_total: The specific contributions, summed likewise.
        residual: ``factor_total + specific_total - volatility``: what floating
            point left unreconciled.
    """

    covariance_id: str
    currency: str
    period: str
    volatility: float
    exposures: Mapping[str, float]
    factors: Mapping[str, float]
    specific: Mapping[str, float]
    factor_total: float
    specific_total: float
    residual: float

    def share(self, prefix: str) -> float:
        """The summed contribution of every factor whose name begins with ``prefix``.

        ``share(EXCHANGE_RATE_FACTOR_PREFIX)`` is the volatility the exchange
        rates carry, when the loadings came from :func:`currency_loadings`.
        """

        return math.fsum(
            value for factor, value in self.factors.items() if factor.startswith(prefix)
        )


def factor_risk(
    weights: Mapping[str, float], covariance: CovarianceMatrix | FactorStructure
) -> FactorRisk:
    """Divide a book's volatility among the factors of a factor-model covariance.

    ``weights`` are fractions of the capital the covariance's returns are
    measured against, for assets the covariance covers; an asset left out holds
    nothing. ``covariance`` is a :class:`FactorStructure`, or a matrix
    :meth:`CovarianceMatrix.factor_model` built, which carries one; either way
    the cost is ``O(n k^2)``.

    Raises:
        AnalyticsValidationError: If the covariance is a matrix not built from a
            factor model (it then has no factor to divide risk among),
            ``weights`` is empty or holds a non-finite weight or an asset the
            covariance does not cover, or the book's volatility is zero -- every
            contribution would then be zero over zero.
    """

    structure = covariance if isinstance(covariance, FactorStructure) else covariance.factors
    if structure is None:
        raise AnalyticsValidationError(
            f"Covariance {covariance.covariance_id[:12]} was not built from a factor model, so "
            "it has no factor to divide risk among. Build it with CovarianceMatrix.factor_model."
        )
    if not weights:
        raise AnalyticsValidationError(
            "A risk decomposition of no weights is undefined, which is a refusal rather than zero."
        )
    loadings = structure.loadings
    held = {
        asset: _require_finite(weights[asset], f"weight of {asset!r}") for asset in sorted(weights)
    }
    loadings.require_covers(held, "A factor risk decomposition")
    factors = loadings.factors
    rows = {asset: loadings.values[loadings._position(asset)] for asset in held}
    exposures = [
        math.fsum(held[asset] * rows[asset][k] for asset in held) for k in range(len(factors))
    ]
    f = structure.factor_covariance.values
    pushed = [
        math.fsum(f[k][h] * exposures[h] for h in range(len(factors))) for k in range(len(factors))
    ]
    specific_variance = {
        asset: held[asset] * held[asset] * structure.specific[loadings._position(asset)]
        for asset in held
    }
    variance = math.fsum(
        [
            *(exposures[k] * pushed[k] for k in range(len(factors))),
            *specific_variance.values(),
        ]
    )
    if not variance > 0.0:
        raise AnalyticsValidationError(
            "The book's volatility is zero, so there is no risk to divide. Every contribution "
            "would be zero over zero rather than an even split."
        )
    volatility = math.sqrt(variance)
    by_factor = {factor: exposures[k] * pushed[k] / volatility for k, factor in enumerate(factors)}
    by_asset = {asset: value / volatility for asset, value in specific_variance.items()}
    factor_total = math.fsum(by_factor.values())
    specific_total = math.fsum(by_asset.values())
    return FactorRisk(
        covariance_id=covariance.covariance_id,
        currency=covariance.currency,
        period=covariance.period,
        volatility=volatility,
        exposures=MappingProxyType(dict(zip(factors, exposures, strict=True))),
        factors=MappingProxyType(by_factor),
        specific=MappingProxyType(by_asset),
        factor_total=factor_total,
        specific_total=specific_total,
        residual=math.fsum([factor_total, specific_total, -volatility]),
    )
