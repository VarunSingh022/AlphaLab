"""Where a portfolio's risk comes from, and under which stated method.

:mod:`alphalab.risk` is the pre-trade gate: it reads an order against limits and
answers *may this pass*. It measures nothing about a book that is already on.
This module is the other half -- risk as a **measurement** -- and it lives in
:mod:`alphalab.analytics` because that is where AlphaLab already measures:
``value_at_risk`` and ``conditional_var`` have been in
:mod:`alphalab.analytics.metrics` since v1, volatility in
:mod:`alphalab.analytics.returns`, drawdown in :mod:`alphalab.analytics.drawdown`.

Nothing here re-derives any of those. Every estimator is either imported from
the authority that already owns it or built from
:mod:`alphalab.common.statistics`, the one place this repository spells an
unbiased variance.

A method is named, never assumed
---------------------------------

"VaR at 95%" is not a number until the method is stated: historical simulation,
a Gaussian fit and a Cornish-Fisher expansion give three different answers from
the same returns, and they differ most exactly where it matters -- in the tail
of a skewed sample. :class:`VaRPolicy` carries the method and the confidence
together, so a figure cannot travel without the assumptions that produced it,
and :meth:`VaRPolicy.identity` renders the pair as a short string for a report
or a cache key.

Three methods are offered because three are legitimate, not because more is
better. The repository already had exactly one, historical, and it stays the
implementation of :attr:`VaRMethod.HISTORICAL` -- :meth:`VaRPolicy.var` calls
:func:`alphalab.analytics.metrics.value_at_risk` rather than reimplementing the
empirical quantile beside it.

Sign convention
---------------

Every risk figure here is reported as a **non-negative magnitude of loss**.
``var == 0.03`` means "a 3% loss", not "a 3% gain". The underlying
:func:`~alphalab.analytics.metrics.value_at_risk` returns the signed quantile of
the return distribution, which is negative for a loss; :class:`VaRPolicy`
negates it once, here, and says so. Mixing the two conventions across a codebase
produces numbers that are wrong by a sign and look plausible either way.

Degenerate samples refuse
-------------------------

A volatility over one observation, a beta against a constant benchmark and a
covariance over one pair are undefined, and :mod:`alphalab.common.statistics`
raises for each. This module does not catch those: a risk report that turned an
undefined measurement into ``0.0`` would report a riskless portfolio, which is
the most dangerous possible placeholder. What it does instead is refuse early
and name the input, so the caller learns which series was too short.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from statistics import NormalDist

from alphalab.analytics.drawdown import calculate_drawdowns
from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.metrics import conditional_var, value_at_risk
from alphalab.common.statistics import (
    linear_regression,
    percentile,
    sample_covariance,
    sample_variance,
)

__all__ = [
    "ConcentrationMetrics",
    "LeverageMetrics",
    "LiquidityRisk",
    "PositionRisk",
    "RiskDecomposition",
    "VaRMethod",
    "VaRPolicy",
    "concentration",
    "correlation_matrix",
    "covariance_matrix",
    "decompose",
    "factor_exposure",
    "gross_weights",
    "leverage",
    "liquidity_risk",
    "percentile_loss",
    "portfolio_beta",
    "portfolio_volatility",
    "risk_contributions",
    "tail_ratio",
]


class VaRMethod(Enum):
    """How a Value-at-Risk figure is estimated from a return sample."""

    #: The empirical quantile of the observed returns, linearly interpolated.
    #: Assumes only that the sample is representative; makes no distributional
    #: claim. Cannot report a loss larger than the worst one observed, which is
    #: its honest limitation rather than a bug.
    HISTORICAL = auto()

    #: A normal fit: ``mean + z * sigma``. Extrapolates beyond the sample, which
    #: is the point, and understates the tail of any skewed or fat-tailed
    #: return series, which is the cost.
    GAUSSIAN = auto()

    #: Cornish-Fisher: the Gaussian quantile adjusted for the sample's skewness
    #: and excess kurtosis. Extrapolates like the Gaussian while responding to
    #: asymmetry. The expansion is unreliable at extreme confidence on small
    #: samples, and is not offered below the sample sizes checked for here.
    CORNISH_FISHER = auto()


#: Smallest sample :attr:`VaRMethod.CORNISH_FISHER` will estimate from. Third
#: and fourth moments over fewer observations than this are dominated by the
#: individual points, and the expansion built on them is arithmetic rather than
#: a measurement.
_CORNISH_FISHER_MINIMUM = 8


@dataclass(frozen=True, slots=True)
class VaRPolicy:
    """The method and confidence a VaR or CVaR figure was produced under.

    Attributes:
        method: Which estimator.
        confidence: The confidence level, in ``(0, 1)``. ``0.95`` means the loss
            exceeded on 5% of observations.
    """

    method: VaRMethod
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 < self.confidence < 1.0:
            raise AnalyticsValidationError(
                f"confidence is {self.confidence!r} and must lie strictly in (0, 1). "
                "A confidence of 0 or 1 names a quantile at the very edge of the sample, "
                "where no method here is estimating anything."
            )

    def identity(self) -> str:
        """``"HISTORICAL@0.95"`` -- the policy as a short, stable label.

        Derived from the fields and nothing else, so two policies that produce
        the same numbers render the same string, in any process.
        """

        return f"{self.method.name}@{self.confidence:g}"

    def var(self, returns: Sequence[float]) -> float:
        """Value at Risk, as a non-negative magnitude of loss.

        Raises:
            AnalyticsValidationError: If the sample is too small for the chosen
                method. Every method needs at least two observations; Cornish-
                Fisher needs :data:`_CORNISH_FISHER_MINIMUM`.
        """

        sample = tuple(returns)
        self._require_sample(sample)

        if self.method is VaRMethod.HISTORICAL:
            # The repository's existing historical VaR, unchanged. Negated once,
            # here, to this module's loss-magnitude convention.
            return -value_at_risk(sample, self.confidence)

        deviation = math.sqrt(sample_variance(sample))
        average = sum(sample) / len(sample)
        z = _standard_normal_quantile(1.0 - self.confidence)

        if self.method is VaRMethod.GAUSSIAN:
            return -(average + z * deviation)

        adjusted = _cornish_fisher_quantile(z, sample, average, deviation)
        return -(average + adjusted * deviation)

    def cvar(self, returns: Sequence[float]) -> float:
        """Conditional VaR: the mean loss *given* the VaR threshold is breached.

        For :attr:`VaRMethod.HISTORICAL` this is the existing
        :func:`~alphalab.analytics.metrics.conditional_var`, negated to the loss
        convention. For the two parametric methods the threshold is the
        parametric VaR and the expectation is taken over the observations that
        actually fall below it -- the tail is measured, not modelled, because a
        closed-form normal shortfall would reintroduce the distributional
        assumption at exactly the point the figure exists to avoid it.

        Returns the VaR itself when no observation falls below the threshold,
        which happens when a parametric method extrapolates past the worst
        observed loss. That is the tightest true statement available: the
        conditional mean of an empty tail is undefined, and the threshold is a
        lower bound on it.
        """

        sample = tuple(returns)
        self._require_sample(sample)

        if self.method is VaRMethod.HISTORICAL:
            return -conditional_var(sample, self.confidence)

        threshold = -self.var(sample)
        tail = [value for value in sample if value <= threshold]
        if not tail:
            return -threshold
        return -(sum(tail) / len(tail))

    def _require_sample(self, sample: Sequence[float]) -> None:
        if len(sample) < 2:
            raise AnalyticsValidationError(
                f"{self.identity()} needs at least 2 observations and was given "
                f"{len(sample)}. A risk figure from one observation is not a small "
                "number, it is no number."
            )
        if self.method is VaRMethod.CORNISH_FISHER and len(sample) < _CORNISH_FISHER_MINIMUM:
            raise AnalyticsValidationError(
                f"Cornish-Fisher needs at least {_CORNISH_FISHER_MINIMUM} observations for "
                f"its third and fourth moments to mean anything; {len(sample)} were given. "
                "Use HISTORICAL, which makes no distributional claim, or GAUSSIAN."
            )


def _standard_normal_quantile(probability: float) -> float:
    """The inverse standard normal CDF.

    :meth:`statistics.NormalDist.inv_cdf` from the standard library, which
    implements Wichura's AS241 to full double precision. The standard library is
    not a *runtime dependency* -- ``dependencies = []`` stays true -- so there is
    no reason to carry a hand-rolled rational approximation accurate to only nine
    digits beside it.

    Raises:
        AnalyticsValidationError: If ``probability`` is not strictly inside
            ``(0, 1)``, where the quantile is infinite rather than large.
    """

    if not 0.0 < probability < 1.0:
        raise AnalyticsValidationError(
            f"A normal quantile is defined strictly inside (0, 1); got {probability!r}."
        )
    return NormalDist().inv_cdf(probability)


def _cornish_fisher_quantile(
    z: float, sample: Sequence[float], average: float, deviation: float
) -> float:
    """Adjust a normal quantile for the sample's skewness and excess kurtosis.

    The standard third-order expansion. Moments are taken about the sample mean
    and standardized by the **sample** standard deviation --
    :func:`~alphalab.common.statistics.sample_variance`'s ``n - 1`` estimator --
    so the skewness and kurtosis here are consistent with every other dispersion
    figure the repository reports.
    """

    if deviation == 0.0:
        raise AnalyticsValidationError(
            "Cornish-Fisher is undefined for a constant return series: it standardizes "
            "the third and fourth moments by a dispersion of zero."
        )

    count = len(sample)
    standardized = [(value - average) / deviation for value in sample]
    skewness = sum(value**3 for value in standardized) / count
    kurtosis = sum(value**4 for value in standardized) / count - 3.0

    return (
        z
        + (z**2 - 1.0) * skewness / 6.0
        + (z**3 - 3.0 * z) * kurtosis / 24.0
        - (2.0 * z**3 - 5.0 * z) * skewness**2 / 36.0
    )


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PositionRisk:
    """One position, as risk measurement sees it.

    A deliberately small projection of
    :class:`~alphalab.portfolio.position.Position`, carrying only what a risk
    figure reads. It is built by the caller rather than imported, which is what
    keeps :mod:`alphalab.analytics` free of :mod:`alphalab.portfolio` -- the
    same separation :class:`~alphalab.analytics.attribution.TradeRecord` keeps
    from :class:`~alphalab.execution.report.ExecutionReport`.

    Attributes:
        asset_id: The asset.
        market_value: Signed market value: negative for a short. In
            ``currency``.
        currency: Currency ``market_value`` is expressed in. Every position in
            one decomposition must share it; :func:`decompose` refuses a mixed
            book rather than adding figures in different units, which is
            ADR-0020's rule.
        returns: The asset's own return series, aligned with every other
            position's and with the portfolio's. Aligned means same length and
            same periods, in the same order -- nothing here re-indexes by date,
            because no date is carried.
    """

    asset_id: str
    market_value: Decimal
    currency: str
    returns: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ConcentrationMetrics:
    """How unevenly exposure is spread.

    Attributes:
        herfindahl: Sum of squared gross weights, in ``(0, 1]``. ``1`` is one
            position; ``1/n`` is ``n`` equal ones.
        effective_positions: ``1 / herfindahl`` -- the number of equally sized
            positions that would be this concentrated. The reading of the index
            that is actually interpretable.
        largest_weight: The largest single gross weight.
        largest_asset_id: Which asset carries it.
    """

    herfindahl: float
    effective_positions: float
    largest_weight: float
    largest_asset_id: str


@dataclass(frozen=True, slots=True)
class LeverageMetrics:
    """Gross and net exposure against capital.

    Attributes:
        gross_exposure: Sum of absolute market values.
        net_exposure: Signed sum of market values.
        gross_leverage: ``gross_exposure / equity``.
        net_leverage: ``net_exposure / equity``.
        long_exposure: Sum of the positive market values.
        short_exposure: Sum of the negative market values, as a magnitude.
    """

    gross_exposure: Decimal
    net_exposure: Decimal
    gross_leverage: Decimal
    net_leverage: Decimal
    long_exposure: Decimal
    short_exposure: Decimal


@dataclass(frozen=True, slots=True)
class LiquidityRisk:
    """How long the book would take to leave, at a stated participation.

    Attributes:
        days_to_liquidate: Per asset, ``units / (adv * participation_limit)``.
        worst_asset_id: The name that takes longest.
        worst_days: How long that name takes. The figure that matters: a book
            is not liquid because its average name is.
        participation_limit: The assumption the figures were computed under,
            echoed back so they cannot be read without it.
    """

    days_to_liquidate: Mapping[str, float]
    worst_asset_id: str
    worst_days: float
    participation_limit: Decimal


@dataclass(frozen=True, slots=True)
class RiskDecomposition:
    """Every measurement one portfolio produced, under one stated policy.

    Attributes:
        policy: The VaR/CVaR method and confidence. Carried so no figure below
            can be quoted without it.
        volatility: Portfolio return standard deviation, per period. Not
            annualized -- see :func:`~alphalab.analytics.returns.annualized_volatility`
            for that, which is the authority for the scaling convention.
        value_at_risk: Loss magnitude at ``policy``.
        conditional_value_at_risk: Mean loss beyond it.
        tail_ratio: ``cvar / var``. How much worse the tail is than its own
            threshold; ``1.0`` is a tail with no depth beyond the cut.
        beta: Against the supplied benchmark, or ``None`` when none was given.
        max_drawdown: From the supplied equity curve, via
            :func:`~alphalab.analytics.drawdown.calculate_drawdowns`.
        concentration: Exposure spread.
        leverage: Gross and net.
        risk_contributions: Each position's share of :attr:`volatility`. These
            sum to :attr:`volatility` exactly, up to floating point -- that is
            the property that makes this a decomposition rather than a list.
        factor_exposure: Portfolio loading per factor, or an empty mapping when
            no loadings were supplied.
        liquidity: Days to liquidate, or ``None`` when no volumes were supplied.
    """

    policy: VaRPolicy
    volatility: float
    value_at_risk: float
    conditional_value_at_risk: float
    tail_ratio: float
    beta: float | None
    max_drawdown: float
    concentration: ConcentrationMetrics
    leverage: LeverageMetrics
    risk_contributions: Mapping[str, float]
    factor_exposure: Mapping[str, float]
    liquidity: LiquidityRisk | None


# --------------------------------------------------------------------------- #
# Estimators
# --------------------------------------------------------------------------- #


def _require_positions(positions: Sequence[PositionRisk]) -> None:
    if not positions:
        raise AnalyticsValidationError(
            "Risk decomposition over an empty book is undefined. A portfolio with no "
            "positions has no risk to decompose, which is a refusal rather than zero."
        )
    names = [position.asset_id for position in positions]
    if len(set(names)) != len(names):
        repeated = sorted({name for name in names if names.count(name) > 1})
        raise AnalyticsValidationError(
            f"Duplicate assets in the risk book: {repeated}. One position counted twice "
            "would overstate its weight and its risk contribution together."
        )
    currencies = sorted({position.currency for position in positions})
    if len(currencies) > 1:
        raise AnalyticsValidationError(
            f"The book is denominated in {currencies}. Market values in different "
            "currencies cannot be added into a weight without an exchange rate, and "
            "AlphaLab does not invent one (ADR-0020). Convert the positions to one "
            "reporting currency before decomposing them."
        )
    lengths = sorted({len(position.returns) for position in positions})
    if len(lengths) > 1:
        raise AnalyticsValidationError(
            f"Position return series have differing lengths {lengths}. A covariance "
            "between two series of different length would be computed over a pairing "
            "that had to be guessed."
        )


def gross_weights(positions: Sequence[PositionRisk]) -> Mapping[str, float]:
    """Each position's share of gross exposure, ordered by ``asset_id``.

    Gross rather than net: a long and a short of equal size net to nothing and
    are not a portfolio without risk. Weights are signed, and their absolute
    values sum to one.

    Raises:
        AnalyticsValidationError: If the book is empty, duplicated, mixed in
            currency, or has zero gross exposure -- there is no weight to take a
            share of.
    """

    _require_positions(positions)
    gross = sum((abs(position.market_value) for position in positions), Decimal("0"))
    if gross == 0:
        raise AnalyticsValidationError(
            "Every position has zero market value, so there is no exposure to weight. "
            "A flat book's weights are undefined, not uniform."
        )
    return {
        position.asset_id: float(position.market_value / gross)
        for position in sorted(positions, key=lambda item: item.asset_id)
    }


def covariance_matrix(positions: Sequence[PositionRisk]) -> Mapping[str, Mapping[str, float]]:
    """Pairwise sample covariance of the positions' return series.

    Symmetric by construction: each pair is computed once and written to both
    cells, so the matrix cannot disagree with itself. The diagonal is exactly
    :func:`~alphalab.common.statistics.sample_variance` of that asset's returns.

    Raises:
        AnalyticsValidationError: Through :func:`_require_positions`, or from
            :mod:`alphalab.common.statistics` when a series is shorter than two
            observations.
    """

    _require_positions(positions)
    ordered = sorted(positions, key=lambda item: item.asset_id)
    matrix: dict[str, dict[str, float]] = {position.asset_id: {} for position in ordered}
    for outer, first in enumerate(ordered):
        for second in ordered[outer:]:
            value = sample_covariance(first.returns, second.returns)
            matrix[first.asset_id][second.asset_id] = value
            matrix[second.asset_id][first.asset_id] = value
    return matrix


def correlation_matrix(positions: Sequence[PositionRisk]) -> Mapping[str, Mapping[str, float]]:
    """Pairwise correlation, derived from :func:`covariance_matrix`.

    Derived rather than computed independently, so a correlation and a
    covariance can never disagree about the same pair.

    Raises:
        AnalyticsValidationError: If any asset's returns are constant. A
            correlation against a constant series is undefined, and zero would
            read as a measured absence of relationship -- the rule
            :func:`~alphalab.common.statistics.pearson_correlation` already
            applies.
    """

    covariance = covariance_matrix(positions)
    for asset_id, row in covariance.items():
        if row[asset_id] == 0.0:
            raise AnalyticsValidationError(
                f"{asset_id} has a constant return series, so every correlation with it "
                "is undefined. Zero would read as 'measured, and unrelated'."
            )
    return {
        first: {
            second: covariance[first][second]
            / math.sqrt(covariance[first][first] * covariance[second][second])
            for second in covariance
        }
        for first in covariance
    }


def portfolio_volatility(positions: Sequence[PositionRisk]) -> float:
    """``sqrt(w' C w)`` -- per-period standard deviation of the weighted book.

    Computed from the covariance matrix and the gross weights rather than from a
    synthesized portfolio return series, because the two agree only when the
    weights were constant over the sample, and this is the one that states its
    assumption: it is the volatility of *today's* book, held fixed, over the
    sample's covariance.
    """

    weights = gross_weights(positions)
    covariance = covariance_matrix(positions)
    variance = sum(
        weights[first] * covariance[first][second] * weights[second]
        for first in weights
        for second in weights
    )
    # A tiny negative can only arise from floating-point cancellation on a
    # near-singular matrix; the true quadratic form of a covariance matrix is
    # non-negative. Clamping at zero is the numerically honest reading, and it
    # is narrow enough that a genuinely negative variance would be a bug rather
    # than a rounding artefact.
    return math.sqrt(max(0.0, variance))


def risk_contributions(positions: Sequence[PositionRisk]) -> Mapping[str, float]:
    """Each position's contribution to portfolio volatility.

    ``CTR_i = w_i * (C w)_i / sigma_p``. These sum to ``sigma_p`` exactly -- the
    Euler decomposition of a homogeneous-degree-one risk measure -- which is
    what makes this a decomposition and not merely a list of per-asset
    volatilities. A negative contribution is real and is kept: a position that
    hedges the rest genuinely removes risk.

    Raises:
        AnalyticsValidationError: If portfolio volatility is zero, leaving
            nothing to apportion.
    """

    weights = gross_weights(positions)
    covariance = covariance_matrix(positions)
    total = portfolio_volatility(positions)
    if total == 0.0:
        raise AnalyticsValidationError(
            "Portfolio volatility is zero, so there is no risk to decompose. Every "
            "contribution would be zero over zero rather than an even split."
        )
    return {
        asset_id: weights[asset_id]
        * sum(covariance[asset_id][other] * weights[other] for other in weights)
        / total
        for asset_id in weights
    }


def concentration(positions: Sequence[PositionRisk]) -> ConcentrationMetrics:
    """Herfindahl concentration of gross weights."""

    weights = gross_weights(positions)
    index = sum(weight * weight for weight in weights.values())
    largest = max(weights, key=lambda asset_id: (abs(weights[asset_id]), asset_id))
    return ConcentrationMetrics(
        herfindahl=index,
        effective_positions=1.0 / index,
        largest_weight=abs(weights[largest]),
        largest_asset_id=largest,
    )


def leverage(positions: Sequence[PositionRisk], equity: Decimal) -> LeverageMetrics:
    """Gross and net exposure, against the equity supporting them.

    ``equity`` is required and has no default: leverage is exposure *over
    capital*, and a leverage figure computed against an assumed capital base is
    a ratio of one real number to one invented one.

    Raises:
        AnalyticsValidationError: If ``equity`` is not positive. Leverage
            against zero or negative equity is not a large number, it is
            undefined -- an account in deficit has no capital to be levered
            against.
    """

    _require_positions(positions)
    if equity <= 0:
        raise AnalyticsValidationError(
            f"equity is {equity}; leverage is exposure over capital and is undefined "
            "when there is no capital. An account in deficit needs reporting as such, "
            "not as a very large ratio."
        )

    longs = sum(
        (position.market_value for position in positions if position.market_value > 0),
        Decimal("0"),
    )
    shorts = sum(
        (-position.market_value for position in positions if position.market_value < 0),
        Decimal("0"),
    )
    gross = longs + shorts
    net = longs - shorts
    return LeverageMetrics(
        gross_exposure=gross,
        net_exposure=net,
        gross_leverage=gross / equity,
        net_leverage=net / equity,
        long_exposure=longs,
        short_exposure=shorts,
    )


def portfolio_beta(portfolio_returns: Sequence[float], benchmark: Sequence[float]) -> float:
    """Ordinary-least-squares beta of the portfolio on the benchmark.

    The slope from :func:`~alphalab.common.statistics.linear_regression`, which
    is the repository's one regression. Using it rather than ``cov/var`` keeps
    beta, a factor neutralization's beta and a research residual built from the
    same arithmetic.

    Raises:
        AlphaLabValidationError: From the regression, if the series differ in
            length, are shorter than two, or the benchmark is constant.
    """

    return linear_regression(list(portfolio_returns), list(benchmark)).slope


def factor_exposure(
    positions: Sequence[PositionRisk], loadings: Mapping[str, Mapping[str, float]]
) -> Mapping[str, float]:
    """Weighted factor loadings of the book, ordered by factor.

    ``loadings`` maps ``asset_id`` to that asset's loading per factor, and is
    supplied by the caller: AlphaLab holds no factor model on the execution
    path, and inventing loadings would be inventing the exposure they produce.

    Only assets present in ``loadings`` contribute. An asset with no entry is
    **not** treated as having zero loading -- that would claim a measurement --
    so the result names which assets were covered through
    :func:`decompose`'s report rather than silently absorbing the gap.

    Raises:
        AnalyticsValidationError: If ``loadings`` names an asset the book does
            not hold, which means the caller has aligned the wrong two things.
    """

    weights = gross_weights(positions)
    unknown = sorted(set(loadings) - set(weights))
    if unknown:
        raise AnalyticsValidationError(
            f"Factor loadings supplied for assets not in the book: {unknown}. The "
            "loadings and the positions describe different portfolios."
        )

    exposure: dict[str, float] = {}
    for asset_id, factors in sorted(loadings.items()):
        for factor, loading in sorted(factors.items()):
            exposure[factor] = exposure.get(factor, 0.0) + weights[asset_id] * loading
    return dict(sorted(exposure.items()))


def liquidity_risk(
    positions: Sequence[PositionRisk],
    units: Mapping[str, Decimal],
    average_daily_volume: Mapping[str, Decimal],
    participation_limit: Decimal,
) -> LiquidityRisk:
    """Days to liquidate each name at a stated participation limit.

    ``units / (adv * participation_limit)``. Every input is required: the
    participation limit is the assumption that decides the answer, and there is
    no conventional value for it.

    Raises:
        AnalyticsValidationError: If the limit is outside ``(0, 1]``, if a
            position has no volume or unit count supplied, or if a supplied
            volume is not positive. A name with no volume does not take a long
            time to sell; how long it takes is unknown, and that is reported as
            a refusal rather than as infinity.
    """

    _require_positions(positions)
    if not Decimal("0") < participation_limit <= Decimal("1"):
        raise AnalyticsValidationError(
            f"participation_limit is {participation_limit} and must lie in (0, 1]."
        )

    missing = sorted(
        position.asset_id
        for position in positions
        if position.asset_id not in units or position.asset_id not in average_daily_volume
    )
    if missing:
        raise AnalyticsValidationError(
            f"No unit count or volume supplied for {missing}. Days-to-liquidate without "
            "a volume is not a large number, it is an unmeasured one."
        )

    days: dict[str, float] = {}
    for position in sorted(positions, key=lambda item: item.asset_id):
        volume = average_daily_volume[position.asset_id]
        if volume <= 0:
            raise AnalyticsValidationError(
                f"{position.asset_id} has average daily volume {volume}. A name that "
                "trades nothing cannot be liquidated in any number of days."
            )
        days[position.asset_id] = float(
            abs(units[position.asset_id]) / (volume * participation_limit)
        )

    worst = max(days, key=lambda asset_id: (days[asset_id], asset_id))
    return LiquidityRisk(
        days_to_liquidate=days,
        worst_asset_id=worst,
        worst_days=days[worst],
        participation_limit=participation_limit,
    )


def tail_ratio(policy: VaRPolicy, returns: Sequence[float]) -> float:
    """``cvar / var`` -- how much deeper the tail runs than its own threshold.

    ``1.0`` means everything beyond the cut sits exactly at it. Larger means a
    fat tail: the losses that breach VaR breach it by a lot.

    Raises:
        AnalyticsValidationError: If VaR is zero or negative, which happens when
            the chosen quantile of the sample is a gain. There is no loss tail
            to take a ratio against, and the caller is asking about a confidence
            level this sample does not reach.
    """

    var = policy.var(returns)
    if var <= 0.0:
        raise AnalyticsValidationError(
            f"{policy.identity()} puts the threshold at a gain (VaR {var:.6g}), so there "
            "is no loss tail to measure a ratio against. Raise the confidence level or "
            "supply a sample that reaches a loss at this one."
        )
    return policy.cvar(returns) / var


def percentile_loss(returns: Sequence[float], fraction: float) -> float:
    """The loss magnitude at a given percentile of the return sample.

    A thin, named wrapper over :func:`~alphalab.common.statistics.percentile`
    that applies this module's sign convention once. Offered so that a caller
    reporting a tail quantile alongside VaR uses the repository's one
    interpolation convention rather than a second one.
    """

    return -percentile(list(returns), fraction)


# --------------------------------------------------------------------------- #
# The whole picture
# --------------------------------------------------------------------------- #


def decompose(
    positions: Sequence[PositionRisk],
    portfolio_returns: Sequence[float],
    equity: Decimal,
    equity_curve: Sequence[float],
    policy: VaRPolicy,
    benchmark: Sequence[float] | None = None,
    loadings: Mapping[str, Mapping[str, float]] | None = None,
    units: Mapping[str, Decimal] | None = None,
    average_daily_volume: Mapping[str, Decimal] | None = None,
    participation_limit: Decimal | None = None,
) -> RiskDecomposition:
    """Every measurement this module offers, for one book, under one policy.

    The required arguments are the ones every figure needs. The optional ones
    each switch on exactly one measurement and are ``None`` when the caller has
    no data for it: no benchmark means no beta, no loadings mean an empty factor
    exposure, and liquidity needs all three of ``units``,
    ``average_daily_volume`` and ``participation_limit`` or none of them.

    ``None`` means *unmeasured*, and the result says so by carrying ``None`` or
    an empty mapping. Nothing here substitutes a figure for missing data.

    Raises:
        AnalyticsValidationError: If the book is empty, duplicated or mixed in
            currency; if a sample is too short for the policy; or if the
            liquidity inputs are supplied only in part.
    """

    _require_positions(positions)

    liquidity_inputs = (units, average_daily_volume, participation_limit)
    if any(item is not None for item in liquidity_inputs) and not all(
        item is not None for item in liquidity_inputs
    ):
        raise AnalyticsValidationError(
            "Liquidity risk needs units, average_daily_volume and participation_limit "
            "together. Supplying some of them would produce a figure resting on a "
            "default for the rest, and the participation limit in particular has no "
            "conventional value to fall back on."
        )

    liquidity = None
    if units is not None and average_daily_volume is not None and participation_limit is not None:
        liquidity = liquidity_risk(positions, units, average_daily_volume, participation_limit)

    return RiskDecomposition(
        policy=policy,
        volatility=portfolio_volatility(positions),
        value_at_risk=policy.var(portfolio_returns),
        conditional_value_at_risk=policy.cvar(portfolio_returns),
        tail_ratio=tail_ratio(policy, portfolio_returns),
        beta=None if benchmark is None else portfolio_beta(portfolio_returns, benchmark),
        max_drawdown=calculate_drawdowns(tuple(equity_curve)).max_drawdown,
        concentration=concentration(positions),
        leverage=leverage(positions, equity),
        risk_contributions=risk_contributions(positions),
        factor_exposure={} if loadings is None else factor_exposure(positions, loadings),
        liquidity=liquidity,
    )
