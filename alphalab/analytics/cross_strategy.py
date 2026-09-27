"""Cross-strategy risk: how much independent strategies resemble, overlap and crowd each other.

Two strategies that each look diversified can be the same bet twice. This module
measures that, and each measurement says exactly what is being compared --
"correlation" without a basis is not a measurement:

=========================== ====================================================
measurement                 what is compared
=========================== ====================================================
return correlation          return series over time --
                            :func:`strategy_return_correlation`
exposure correlation        exposure vectors across instruments --
                            :func:`strategy_overlap`
position overlap            the instruments and amounts two strategies share --
                            :func:`strategy_overlap`
factor exposure overlap     factor exposure vectors -- :func:`factor_crowding`
factor crowding             how aligned strategies are on one factor --
                            :func:`factor_crowding`
common exposure             what several strategies hold in one bucket --
                            :func:`common_exposures`
capital concentration       how capital is spread over strategies or pools --
                            :func:`capital_concentration`
capital overlap             which capital pools several strategies draw on --
                            :func:`capital_overlap`
=========================== ====================================================

Nothing is re-derived
---------------------

A return correlation is :meth:`CovarianceMatrix.sample
<alphalab.analytics.risk_model.CovarianceMatrix.sample>` followed by
:meth:`~alphalab.analytics.risk_model.CovarianceMatrix.correlation` -- the same
estimator and the same derivation every asset correlation uses. An exposure
correlation is :func:`alphalab.common.statistics.pearson_correlation`. A factor
exposure is :func:`~alphalab.analytics.risk_model.portfolio_factor_exposures`,
and every concentration figure is
:func:`~alphalab.analytics.risk_model.herfindahl_index`. Exposures arrive as the
lines :func:`alphalab.portfolio.multi_strategy.value_book` produces, already in
one reporting currency through the canonical FX authority; capital arrives in
one currency or is refused.

What "crowding" means here, and does not
----------------------------------------

:class:`FactorCrowding` measures crowding **within the portfolio**: whether its
strategies' exposures to one factor point the same way (``alignment`` near one)
or offset (near zero), and how concentrated that exposure is among them. It
says nothing about how crowded a factor is in the market -- other investors'
positions -- which no data given here can establish.

Undefined is reported, not zeroed
---------------------------------

An exposure correlation over fewer than two instruments, or against a constant
vector, is undefined; a cosine similarity against a zero vector is undefined.
Each is ``None``, never ``0.0``, which would read as "measured, and unrelated".
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import Enum, auto
from itertools import combinations
from types import MappingProxyType
from typing import Final

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_budget import ExposureLine
from alphalab.analytics.risk_model import (
    Classification,
    CorrelationMatrix,
    CovarianceMatrix,
    FactorLoadings,
    herfindahl_index,
    portfolio_factor_exposures,
)
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import pearson_correlation

__all__ = [
    "CapitalConcentration",
    "CommonDimension",
    "CommonExposure",
    "CommonExposureReport",
    "ExposureSimilarity",
    "FactorCrowding",
    "FactorCrowdingReport",
    "FactorExposureOverlap",
    "SharedCapitalPool",
    "StrategyCorrelation",
    "StrategyReturns",
    "capital_concentration",
    "capital_overlap",
    "common_exposures",
    "factor_crowding",
    "strategy_overlap",
    "strategy_return_correlation",
]

_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalyticsValidationError(
            f"{what} must be a non-blank, unpadded string, got {value!r}."
        )
    return value


def _exposures_by_strategy(
    lines: Iterable[ExposureLine],
) -> dict[str, dict[str, Decimal]]:
    """Strategy -> asset -> reporting value, refusing an unattributed or duplicated line."""

    by_strategy: dict[str, dict[str, Decimal]] = {}
    for line in lines:
        strategy = line.strategy_id
        if not strategy:
            raise AnalyticsValidationError(
                f"The line holding {line.asset_id!r} names no strategy. Cross-strategy risk "
                "compares strategies, and an unattributed line belongs to none of them."
            )
        holdings = by_strategy.setdefault(strategy, {})
        if line.asset_id in holdings:
            raise AnalyticsValidationError(
                f"Strategy {strategy!r} holds {line.asset_id!r} on two lines."
            )
        value = line.reporting_value
        if not isinstance(value, Decimal) or not value.is_finite():
            raise AnalyticsValidationError(
                f"The line {strategy}/{line.asset_id} carries {value!r}."
            )
        holdings[line.asset_id] = value
    return {strategy: by_strategy[strategy] for strategy in sorted(by_strategy)}


def _cosine(first: Sequence[float], second: Sequence[float]) -> float | None:
    dot = math.fsum(a * b for a, b in zip(first, second, strict=True))
    norm_first = math.sqrt(math.fsum(a * a for a in first))
    norm_second = math.sqrt(math.fsum(b * b for b in second))
    if norm_first == 0.0 or norm_second == 0.0:
        return None
    return dot / (norm_first * norm_second)


# --------------------------------------------------------------------------- #
# Return correlation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StrategyReturns:
    """One strategy's return series, and what it is measured in.

    Attributes:
        strategy_id: The strategy.
        returns: Per-period returns, aligned with every other strategy's --
            the same length and the same periods, in the same order.
        currency: The currency the returns are measured in.
        period: The period each return spans.
    """

    strategy_id: str
    returns: tuple[float, ...]
    currency: str
    period: str

    def __post_init__(self) -> None:
        _text(self.strategy_id, "StrategyReturns.strategy_id")
        _text(self.currency, "StrategyReturns.currency")
        _text(self.period, "StrategyReturns.period")
        object.__setattr__(self, "returns", tuple(self.returns))


@dataclass(frozen=True, slots=True)
class StrategyCorrelation:
    """Strategies compared by their *returns*.

    Attributes:
        covariance: The sample covariance of the strategies' returns -- a
            :class:`~alphalab.analytics.risk_model.CovarianceMatrix` whose
            "assets" are strategies.
        correlation: The correlation derived from it, carrying its basis:
            returns, measured in the stated currency, per the stated period,
            over the stated number of observations.
    """

    covariance: CovarianceMatrix
    correlation: CorrelationMatrix


def strategy_return_correlation(
    returns: Sequence[StrategyReturns], *, source: str
) -> StrategyCorrelation:
    """Correlate strategies by their return series.

    Args:
        returns: At least two strategies, one series each, all in one currency
            and one period.
        source: Where the series came from, in words (the runs, the dataset);
            it becomes the covariance's source.

    Raises:
        AnalyticsValidationError: On fewer than two strategies, a strategy
            listed twice, mixed currencies or periods, misaligned series, or a
            strategy whose returns never varied -- its correlation with anything
            is undefined.
    """

    if len(returns) < 2:
        raise AnalyticsValidationError("A correlation between strategies needs at least two.")
    names = [entry.strategy_id for entry in returns]
    if len(set(names)) != len(names):
        raise AnalyticsValidationError(f"A strategy is listed twice in {sorted(names)}.")
    currencies = sorted({entry.currency for entry in returns})
    periods = sorted({entry.period for entry in returns})
    if len(currencies) > 1 or len(periods) > 1:
        raise AnalyticsValidationError(
            f"The strategies' returns are measured in {currencies} per {periods}. Returns in "
            "different currencies or periods are different quantities and are not correlated."
        )
    covariance = CovarianceMatrix.sample(
        {entry.strategy_id: entry.returns for entry in returns},
        currency=currencies[0],
        period=periods[0],
        source=_text(source, "source"),
    )
    return StrategyCorrelation(covariance, covariance.correlation())


# --------------------------------------------------------------------------- #
# Position overlap and exposure correlation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExposureSimilarity:
    """Two strategies compared by what they *hold*, in the reporting currency.

    Attributes:
        first: One strategy (the alphabetically earlier).
        second: The other.
        shared_instruments: Instruments both hold with non-zero exposure,
            sorted.
        jaccard: ``shared / union`` of the instruments each holds: how much
            their universes coincide, ignoring size.
        same_direction_overlap: Over shared instruments held the same way,
            ``sum min(|e1|, |e2|)``: the exposure the two duplicate.
        opposing_overlap: Over shared instruments held opposite ways, the same
            sum: the exposure they hold against each other.
        first_overlap_share: ``(same + opposing) / gross of first``.
        second_overlap_share: ``(same + opposing) / gross of second``.
        cosine_similarity: ``e1 . e2 / (|e1| |e2|)`` over every instrument
            either holds -- uncentered, so two identical books read ``1`` and
            a book and its short read ``-1``. ``None`` if either is flat.
        exposure_correlation: The Pearson correlation of the same two vectors,
            instrument by instrument -- centered, so it compares the *pattern*
            of exposure across the union rather than its level. ``None`` over
            fewer than two instruments or against a constant vector.
    """

    first: str
    second: str
    shared_instruments: tuple[str, ...]
    jaccard: float
    same_direction_overlap: Decimal
    opposing_overlap: Decimal
    first_overlap_share: float
    second_overlap_share: float
    cosine_similarity: float | None
    exposure_correlation: float | None


def _similarity(
    first: str,
    second: str,
    left: Mapping[str, Decimal],
    right: Mapping[str, Decimal],
) -> ExposureSimilarity:
    held_left = {asset for asset, value in left.items() if value != 0}
    held_right = {asset for asset, value in right.items() if value != 0}
    shared = sorted(held_left & held_right)
    union = sorted(held_left | held_right)
    same = Decimal(0)
    opposing = Decimal(0)
    for asset in shared:
        amount = min(abs(left[asset]), abs(right[asset]))
        if (left[asset] > 0) == (right[asset] > 0):
            same += amount
        else:
            opposing += amount
    gross_left = sum((abs(value) for value in left.values()), Decimal(0))
    gross_right = sum((abs(value) for value in right.values()), Decimal(0))
    overlap = same + opposing
    vector_left = [float(left.get(asset, Decimal(0))) for asset in union]
    vector_right = [float(right.get(asset, Decimal(0))) for asset in union]
    correlation: float | None
    try:
        correlation = pearson_correlation(vector_left, vector_right) if len(union) >= 2 else None
    except AlphaLabValidationError:
        correlation = None
    return ExposureSimilarity(
        first=first,
        second=second,
        shared_instruments=tuple(shared),
        jaccard=len(shared) / len(union) if union else 0.0,
        same_direction_overlap=same,
        opposing_overlap=opposing,
        first_overlap_share=float(_CONTEXT.divide(overlap, gross_left)) if gross_left else 0.0,
        second_overlap_share=float(_CONTEXT.divide(overlap, gross_right)) if gross_right else 0.0,
        cosine_similarity=_cosine(vector_left, vector_right),
        exposure_correlation=correlation,
    )


def strategy_overlap(lines: Iterable[ExposureLine]) -> tuple[ExposureSimilarity, ...]:
    """Every pair of strategies, compared by what they hold.

    ``S (S - 1) / 2`` pairs for ``S`` strategies, each costing the size of the
    two books it compares. Pairs are ordered by ``(first, second)``.

    Raises:
        AnalyticsValidationError: On an unattributed or duplicated line, or
            fewer than two strategies.
    """

    by_strategy = _exposures_by_strategy(lines)
    if len(by_strategy) < 2:
        raise AnalyticsValidationError(
            f"Overlap compares strategies, and the lines hold {list(by_strategy)}."
        )
    return tuple(
        _similarity(first, second, by_strategy[first], by_strategy[second])
        for first, second in combinations(by_strategy, 2)
    )


# --------------------------------------------------------------------------- #
# Factor crowding and factor exposure overlap
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FactorCrowding:
    """How the portfolio's strategies line up on one factor.

    Each strategy's exposure is ``sum_i (value_i / capital) * beta_i`` over its
    own lines, with capital the portfolio's, so the strategies' exposures add up
    to the portfolio's.

    Attributes:
        factor: The factor.
        by_strategy: Strategy -> its exposure, sorted.
        aggregate_exposure: The portfolio's exposure: the sum.
        gross_exposure: The sum of the magnitudes.
        alignment: ``|aggregate| / gross``, in ``[0, 1]``: one when every
            strategy leans the same way, near zero when they offset. ``None``
            when no strategy is exposed.
        concentration: The Herfindahl index of ``|exposure| / gross`` across
            strategies: one when a single strategy carries all of it. ``None``
            when no strategy is exposed.
        aligned_strategies: Strategies whose exposure has the aggregate's sign,
            sorted. Empty when the aggregate is zero.
    """

    factor: str
    by_strategy: Mapping[str, float]
    aggregate_exposure: float
    gross_exposure: float
    alignment: float | None
    concentration: float | None
    aligned_strategies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FactorExposureOverlap:
    """Two strategies compared by their factor exposure vectors.

    Attributes:
        first: One strategy.
        second: The other.
        cosine_similarity: Of their exposure vectors across every factor;
            ``None`` if either has no factor exposure.
    """

    first: str
    second: str
    cosine_similarity: float | None


@dataclass(frozen=True, slots=True)
class FactorCrowdingReport:
    """Factor crowding and factor overlap, under one factor model.

    Attributes:
        loadings_id: The factor model read.
        capital: What exposures are fractions of.
        factors: One entry per factor, sorted.
        pairs: Every pair of strategies, by factor exposure.
    """

    loadings_id: str
    capital: Decimal
    factors: tuple[FactorCrowding, ...]
    pairs: tuple[FactorExposureOverlap, ...]


def factor_crowding(
    lines: Iterable[ExposureLine], loadings: FactorLoadings, *, capital: Decimal
) -> FactorCrowdingReport:
    """Measure, per factor, how aligned and how concentrated the strategies' exposures are.

    Args:
        lines: Exposure lines in one reporting currency.
        loadings: The factor model, covering every held asset. Supplied by the
            caller -- :func:`alphalab.factor_library.loadings_from_panels`
            builds one from the factor library's panels.
        capital: The portfolio capital exposures are fractions of. Positive.

    Raises:
        AnalyticsValidationError: On an unattributed or duplicated line, a
            non-positive capital, or a held asset without loadings.
    """

    if not isinstance(capital, Decimal) or not capital.is_finite() or capital <= 0:
        raise AnalyticsValidationError(f"capital is {capital!r}; it must be a positive Decimal.")
    by_strategy = _exposures_by_strategy(lines)
    if not by_strategy:
        raise AnalyticsValidationError("Factor crowding over no exposure is undefined.")
    exposures: dict[str, Mapping[str, float]] = {}
    for strategy, holdings in by_strategy.items():
        weights = {
            asset: float(_CONTEXT.divide(value, capital)) for asset, value in holdings.items()
        }
        exposures[strategy] = portfolio_factor_exposures(weights, loadings)

    factors = []
    for factor in loadings.factors:
        per_strategy = {strategy: exposures[strategy][factor] for strategy in exposures}
        aggregate = math.fsum(per_strategy.values())
        gross = math.fsum(abs(value) for value in per_strategy.values())
        factors.append(
            FactorCrowding(
                factor=factor,
                by_strategy=MappingProxyType(per_strategy),
                aggregate_exposure=aggregate,
                gross_exposure=gross,
                alignment=abs(aggregate) / gross if gross > 0.0 else None,
                concentration=(
                    herfindahl_index(abs(value) / gross for value in per_strategy.values())
                    if gross > 0.0
                    else None
                ),
                aligned_strategies=tuple(
                    strategy
                    for strategy, value in per_strategy.items()
                    if aggregate != 0.0 and value != 0.0 and (value > 0.0) == (aggregate > 0.0)
                ),
            )
        )
    pairs = tuple(
        FactorExposureOverlap(
            first,
            second,
            _cosine(
                [exposures[first][factor] for factor in loadings.factors],
                [exposures[second][factor] for factor in loadings.factors],
            ),
        )
        for first, second in combinations(exposures, 2)
    )
    return FactorCrowdingReport(loadings.loadings_id, capital, tuple(factors), pairs)


# --------------------------------------------------------------------------- #
# Common exposures
# --------------------------------------------------------------------------- #


class CommonDimension(Enum):
    """What :func:`common_exposures` buckets by."""

    #: The instrument itself.
    INSTRUMENT = auto()
    #: The currency the holding is denominated in.
    CURRENCY = auto()
    #: A :class:`~alphalab.analytics.risk_model.Classification` -- sector,
    #: country or any other. Factor exposure is :func:`factor_crowding`.
    CLASSIFICATION = auto()


@dataclass(frozen=True, slots=True)
class CommonExposure:
    """What every strategy holds in one bucket, in the reporting currency.

    Attributes:
        bucket: The label.
        by_strategy: Strategy -> its net exposure in the bucket, sorted; only
            strategies with a line in it.
        net_exposure: The portfolio's net exposure in the bucket.
        gross_exposure: The sum of every line's magnitude.
        shared: Whether two or more strategies hold it.
        opposing: Whether one strategy is net long it and another net short.
    """

    bucket: str
    by_strategy: Mapping[str, Decimal]
    net_exposure: Decimal
    gross_exposure: Decimal
    shared: bool
    opposing: bool


@dataclass(frozen=True, slots=True)
class CommonExposureReport:
    """Every bucket of one dimension, and which are shared.

    Attributes:
        dimension: What was bucketed by.
        label: ``"instrument"``, ``"currency"`` or the classification's
            dimension.
        source: ``"line"``, or the classification's source.
        buckets: Every bucket held, sorted.
    """

    dimension: CommonDimension
    label: str
    source: str
    buckets: tuple[CommonExposure, ...]

    @property
    def shared(self) -> tuple[CommonExposure, ...]:
        """The buckets two or more strategies hold."""

        return tuple(bucket for bucket in self.buckets if bucket.shared)


def common_exposures(
    lines: Iterable[ExposureLine],
    *,
    by: CommonDimension,
    classification: Classification | None,
) -> CommonExposureReport:
    """Aggregate every strategy's exposure by one dimension and mark what is shared.

    Raises:
        AnalyticsValidationError: If ``by`` is ``CLASSIFICATION`` without a
            classification (or the reverse), or the classification does not
            cover a held asset -- an unclassified holding is refused, never
            pooled under a default label.
    """

    materialized = list(lines)
    by_strategy = _exposures_by_strategy(materialized)
    currency_of = {line.asset_id: line.currency for line in materialized}
    if (by is CommonDimension.CLASSIFICATION) != (classification is not None):
        raise AnalyticsValidationError(
            "A classification is given exactly when bucketing by CLASSIFICATION."
        )
    if classification is not None:
        classification.require_covers(currency_of, "A common-exposure breakdown")
    label = (
        "instrument"
        if by is CommonDimension.INSTRUMENT
        else "currency"
        if by is CommonDimension.CURRENCY
        else classification.dimension
        if classification is not None
        else ""
    )

    def bucket_of(asset: str) -> str:
        if by is CommonDimension.INSTRUMENT:
            return asset
        if by is CommonDimension.CURRENCY:
            return currency_of[asset]
        assert classification is not None
        return classification.labels[asset]

    nets: dict[str, dict[str, Decimal]] = {}
    gross: dict[str, Decimal] = {}
    for strategy, holdings in by_strategy.items():
        for asset, value in holdings.items():
            bucket = bucket_of(asset)
            per = nets.setdefault(bucket, {})
            per[strategy] = per.get(strategy, Decimal(0)) + value
            gross[bucket] = gross.get(bucket, Decimal(0)) + abs(value)
    buckets = tuple(
        CommonExposure(
            bucket=bucket,
            by_strategy=MappingProxyType(dict(sorted(nets[bucket].items()))),
            net_exposure=sum(nets[bucket].values(), Decimal(0)),
            gross_exposure=gross[bucket],
            shared=len(nets[bucket]) >= 2,
            opposing=any(value > 0 for value in nets[bucket].values())
            and any(value < 0 for value in nets[bucket].values()),
        )
        for bucket in sorted(nets)
    )
    return CommonExposureReport(
        dimension=by,
        label=label,
        source="line" if classification is None else classification.source,
        buckets=buckets,
    )


# --------------------------------------------------------------------------- #
# Capital concentration and overlap
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CapitalConcentration:
    """How capital is spread across the buckets of one dimension.

    Attributes:
        dimension: What the buckets are -- ``"strategy"``, ``"market"``,
            ``"broker"``, ``"account"``, ``"currency"``.
        currency: What every amount is in.
        amounts: Bucket -> capital, sorted.
        total: The sum.
        shares: Bucket -> ``amount / total``.
        herfindahl: The Herfindahl index of the shares.
        effective_count: ``1 / herfindahl``: the number of equal buckets that
            would be this concentrated.
        largest: The bucket with the largest share (ties to the earlier label).
        largest_share: Its share.
    """

    dimension: str
    currency: str
    amounts: Mapping[str, Decimal]
    total: Decimal
    shares: Mapping[str, float]
    herfindahl: float
    effective_count: float
    largest: str
    largest_share: float


def capital_concentration(
    amounts: Mapping[str, Decimal], *, currency: str, dimension: str
) -> CapitalConcentration:
    """Concentration of capital across buckets that are already in one currency.

    Capital in several currencies is converted before it gets here -- by the
    capital allocation result's translated figures or through
    :class:`~alphalab.portfolio.fx.FxRates` -- because adding a yen to a dollar
    is the figure ADR-0020 removed.

    Raises:
        AnalyticsValidationError: On no bucket, a negative or non-finite amount,
            or a zero total.
    """

    _text(currency, "currency")
    _text(dimension, "dimension")
    if not amounts:
        raise AnalyticsValidationError("Capital concentration over no bucket is undefined.")
    ordered: dict[str, Decimal] = {}
    for bucket, amount in sorted(amounts.items()):
        _text(bucket, "capital bucket")
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            raise AnalyticsValidationError(
                f"The capital of {bucket!r} is {amount!r}; capital is a non-negative Decimal."
            )
        ordered[bucket] = amount
    total = sum(ordered.values(), Decimal(0))
    if total == 0:
        raise AnalyticsValidationError(
            "No capital is allocated, so its concentration is undefined rather than zero."
        )
    shares = {bucket: float(_CONTEXT.divide(amount, total)) for bucket, amount in ordered.items()}
    index = herfindahl_index(shares.values())
    largest = next(iter(shares))
    for (
        bucket,
        share,
    ) in shares.items():  # sorted; a strictly larger share wins, so ties keep the earlier
        if share > shares[largest]:
            largest = bucket
    return CapitalConcentration(
        dimension=dimension,
        currency=currency,
        amounts=MappingProxyType(ordered),
        total=total,
        shares=MappingProxyType(shares),
        herfindahl=index,
        effective_count=1.0 / index,
        largest=largest,
        largest_share=shares[largest],
    )


@dataclass(frozen=True, slots=True)
class SharedCapitalPool:
    """One capital pool and the strategies that draw on it.

    Attributes:
        pool: The pool -- an account, a broker, a market.
        by_strategy: Strategy -> capital it draws from the pool, sorted.
        total: The sum.
        shared: Whether two or more strategies draw on it.
    """

    pool: str
    by_strategy: Mapping[str, Decimal]
    total: Decimal
    shared: bool


def capital_overlap(
    allocations: Mapping[str, Mapping[str, Decimal]], *, currency: str
) -> tuple[SharedCapitalPool, ...]:
    """Which pools of capital several strategies share.

    Args:
        allocations: Strategy -> pool -> capital, in ``currency``. A pool two
            strategies draw on is shared capital: a drawdown in one reaches the
            other through the margin they stand on together.
        currency: What every amount is in.

    Raises:
        AnalyticsValidationError: On a negative or non-finite amount.
    """

    _text(currency, "currency")
    pools: dict[str, dict[str, Decimal]] = {}
    for strategy, by_pool in sorted(allocations.items()):
        _text(strategy, "strategy")
        for pool, amount in sorted(by_pool.items()):
            _text(pool, "capital pool")
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
                raise AnalyticsValidationError(
                    f"{strategy!r} draws {amount!r} from {pool!r}; capital is non-negative."
                )
            if amount == 0:
                continue
            pools.setdefault(pool, {})[strategy] = amount
    return tuple(
        SharedCapitalPool(
            pool=pool,
            by_strategy=MappingProxyType(dict(sorted(pools[pool].items()))),
            total=sum(pools[pool].values(), Decimal(0)),
            shared=len(pools[pool]) >= 2,
        )
        for pool in sorted(pools)
    )
