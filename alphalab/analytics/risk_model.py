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
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Final

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.common.statistics import sample_covariance

__all__ = [
    "CLASSIFICATION_SCHEME",
    "COVARIANCE_SCHEME",
    "FACTOR_LOADINGS_SCHEME",
    "Classification",
    "CorrelationMatrix",
    "CovarianceMatrix",
    "Definiteness",
    "DefinitenessKind",
    "FactorLoadings",
    "RiskContributions",
    "euler_decomposition",
    "herfindahl_index",
    "portfolio_factor_exposures",
]

#: Scheme tags, and the first line of each canonical key.
COVARIANCE_SCHEME: Final = "alphalab.covariance.v1"
FACTOR_LOADINGS_SCHEME: Final = "alphalab.factor_loadings.v1"
CLASSIFICATION_SCHEME: Final = "alphalab.classification.v1"

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
    canonical = tuple(
        tuple(
            _require_finite(rows[row][column], f"{what}[{names[row]!r}][{names[column]!r}]")
            for column in order
        )
        for row in order
    )
    return tuple(names[index] for index in order), canonical


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

    Raises:
        AnalyticsValidationError: On a non-canonical order, a ragged, asymmetric
            or non-finite matrix, a negative variance, a blank currency, period
            or source, fewer than two observations, or a derivation without a
            parent (or the reverse).
    """

    assets: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]
    currency: str
    period: str
    source: str
    observations: int | None
    parent_id: str | None = None
    derivation: str | None = None

    def __post_init__(self) -> None:
        names, rows = _canonical_order(self.assets, self.values, "covariance matrix")
        if names != tuple(self.assets):
            raise AnalyticsValidationError(
                "A CovarianceMatrix built directly must list its assets in sorted order; use "
                "CovarianceMatrix.of or from_rows, which put them there."
            )
        for row in range(len(names)):
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

    # -- identity ----------------------------------------------------------- #

    @property
    def covariance_id(self) -> str:
        """The derived identity of this exact claim, in any process."""

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
                *(",".join(_render(value) for value in row) for row in self.values),
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
        covariance_id: The covariance the figures were computed under.
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
    weights: Mapping[str, float], covariance: CovarianceMatrix
) -> RiskContributions:
    """Decompose the volatility of ``weights`` under ``covariance``.

    Assets with a weight must be covered by the covariance; a covariance over a
    larger universe is fine and the rest of it is not read.

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
