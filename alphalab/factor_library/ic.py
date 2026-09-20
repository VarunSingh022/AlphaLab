"""The information coefficient, and what it refuses to report.

The IC is the correlation between a factor's cross-sectional scores at an
instant and the forward returns realized after it. It is the single most
quoted number in factor research and the easiest one to produce meaninglessly:
a correlation over four assets is not a weak signal, it is not a measurement at
all, and averaged over two hundred such instants it becomes a confident number
built from nothing.

So :func:`information_coefficient` takes a ``minimum_assets`` and *reports*
what it did with it. Instants below the threshold are counted in
:attr:`InformationCoefficient.instants_skipped` rather than dropped quietly,
and a result whose ``instants_measured`` is zero is returned as a result with
no mean -- ``None`` -- rather than as a zero that reads like a finding.

Two coefficients, because they answer different questions
----------------------------------------------------------

:attr:`InformationCoefficient.mean_pearson` measures a linear relationship
between the factor's *values* and returns. :attr:`mean_rank` -- Spearman's,
often written "rank IC" -- measures whether the factor gets the *ordering*
right, and is unaffected by any monotone transform of the factor. They are
both reported because a large gap between them is itself the finding: a factor
whose rank IC is healthy and whose Pearson IC is not is one whose extreme
values are mis-scaled rather than mis-ordered.

What is *not* reported, and why
-------------------------------

No p-value, no t-statistic, no confidence interval. The standard
``t = IC_mean / (IC_std / sqrt(n))`` treats the per-instant ICs as independent,
and overlapping forward-return windows make consecutive ICs strongly
autocorrelated by construction -- a horizon of 20 on daily data shares 19 of
its 20 days with the next instant's. The resulting t-statistic is inflated by a
factor that depends on the overlap, and reporting it would be reporting a
significance this module cannot establish.

:attr:`InformationCoefficient.hit_rate` -- the share of instants whose IC was
positive -- is reported instead. It has the same limitation as a *test*, and
makes no claim to be one. What the study layer does with overlap is stated in
:mod:`alphalab.research.purging`, which is where overlapping information is
actually handled rather than assumed away.
"""

from __future__ import annotations

from dataclasses import dataclass

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import mean, pearson_correlation, rank_correlation
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.forward_returns import ForwardReturnPanel
from alphalab.factor_library.panel import FeaturePanel

__all__ = ["InformationCoefficient", "information_coefficient"]


@dataclass(frozen=True, slots=True)
class InformationCoefficient:
    """The IC of one factor at one horizon, with the sample it rests on.

    Attributes:
        horizon: The forward horizon in periods.
        factor_lineage: The factor's version and every transform applied to it,
            so the number can be read against what it measured.
        dataset_version: The data behind both panels, or ``None``.
        instants_measured: Cross-sections that met ``minimum_assets`` and had
            dispersion in both the factor and the returns.
        instants_skipped: Cross-sections that did not, and were therefore not
            measured. A large number here is the finding.
        observations: Total asset-instant pairs the coefficients were
            computed from.
        minimum_assets: The threshold that was applied.
        mean_pearson: Mean of the per-instant Pearson correlations, or ``None``
            when nothing was measurable.
        mean_rank: Mean of the per-instant Spearman correlations, or ``None``.
        hit_rate: Share of measured instants whose rank IC was positive, or
            ``None``. Not a significance test; see the module docstring.
        per_instant_rank: Every per-instant rank IC, keyed by instant, so a
            caller can plot the series rather than take the mean on trust.
    """

    horizon: int
    factor_lineage: str
    dataset_version: str | None
    instants_measured: int
    instants_skipped: int
    observations: int
    minimum_assets: int
    mean_pearson: float | None
    mean_rank: float | None
    hit_rate: float | None
    per_instant_rank: tuple[tuple[float, float], ...]

    @property
    def is_measurable(self) -> bool:
        """Whether any cross-section met the threshold."""

        return self.instants_measured > 0


def information_coefficient(
    factor: FeaturePanel,
    returns: ForwardReturnPanel,
    minimum_assets: int = 5,
) -> InformationCoefficient:
    """Correlate a factor's cross-sections with the returns realized after them.

    Only instants present in *both* panels are measured, and within an instant
    only the assets present in both. A factor score with no realized return
    after it, and a realized return with no factor score before it, are each
    half a pair and neither is completed by substituting anything.

    Raises:
        FactorInputError: If ``minimum_assets`` is below 3 -- two points are
            perfectly correlated whatever they are, so a two-asset IC is
            always ``±1`` and never a measurement -- or if the two panels were
            computed from different dataset versions, which would correlate a
            factor with returns from other data.
    """

    if minimum_assets < 3:
        raise FactorInputError(
            f"minimum_assets must be at least 3, got {minimum_assets}. Two points are "
            "perfectly correlated whatever they are, so a two-asset IC is always plus or "
            "minus one and never a measurement."
        )
    if factor.dataset_version != returns.dataset_version:
        raise FactorInputError(
            f"The factor was computed on {factor.dataset_version!r} and the forward returns "
            f"on {returns.dataset_version!r}. Correlating them would measure one dataset's "
            "factor against another dataset's outcomes."
        )

    pearsons: list[float] = []
    spearmans: list[tuple[float, float]] = []
    observations = 0
    skipped = 0

    for stamp in factor.timestamps:
        scores = factor.cross_section(stamp)
        realized = returns.cross_section(stamp)
        shared = sorted(set(scores) & set(realized))
        if len(shared) < minimum_assets:
            skipped += 1
            continue

        xs = [scores[asset] for asset in shared]
        ys = [realized[asset] for asset in shared]
        try:
            pearson = pearson_correlation(xs, ys)
            spearman = rank_correlation(xs, ys)
        except AlphaLabValidationError:
            # A constant factor or a constant return across the cross-section.
            # Undefined, not zero; counted as unmeasured rather than averaged in.
            skipped += 1
            continue

        pearsons.append(pearson)
        spearmans.append((stamp, spearman))
        observations += len(shared)

    measured = len(spearmans)
    return InformationCoefficient(
        horizon=returns.horizon,
        factor_lineage=factor.lineage,
        dataset_version=factor.dataset_version,
        instants_measured=measured,
        instants_skipped=skipped,
        observations=observations,
        minimum_assets=minimum_assets,
        mean_pearson=mean(pearsons) if pearsons else None,
        mean_rank=mean([value for _, value in spearmans]) if spearmans else None,
        hit_rate=(sum(1 for _, value in spearmans if value > 0.0) / measured if measured else None),
        per_instant_rank=tuple(spearmans),
    )
