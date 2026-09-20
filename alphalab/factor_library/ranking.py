"""Cross-sectional ranking, with the tie rule and the missing rule both stated.

Ranking is where a factor stops being a number and starts being a position in a
universe, and it is also where two implementations that look identical quietly
disagree. Two assets with the same score have no natural order; an asset with
no score at all is not the lowest-scoring asset. This module makes a caller say
what they mean about both, and then does exactly that.

Ties
----

:class:`~alphalab.common.statistics.RankMethod` is the parameter, and there is
no default that hides it: ``AVERAGE`` is what a rank correlation is defined
against, ``MIN`` is competition ranking, and ``ORDINAL`` is what a strict
quantile split needs. The choice is recorded on the returned panel's
:class:`~alphalab.factor_library.panel.FactorTransform`, so a diagnostic
downstream can say which convention produced the ranks it measured.

Missing values
--------------

An asset absent from a cross-section stays absent. It is not ranked last, not
ranked at the median, and not given the universe mean -- each of those is a
score invented on the asset's behalf, and each biases a long/short study in a
different direction. The cross-section simply has fewer members, and
:attr:`FactorRanking.counts` reports how many so a reader can see it thin.

Normalization
-------------

:func:`rank_panel` reports raw ranks, 1 to ``n``, which are not comparable
across instants whose universe size differs. :func:`percentile_rank_panel`
reports ``(rank - 1) / (n - 1)`` in ``[0, 1]``, which is. Both are offered
because both are wanted, and neither is presented as the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from alphalab.common.statistics import RankMethod, TieBreak, bucket_index, ranks
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.panel import FactorTransform, FeaturePanel

__all__ = [
    "FactorRanking",
    "bucket_panel",
    "percentile_rank_panel",
    "rank_panel",
]


@dataclass(frozen=True, slots=True)
class FactorRanking:
    """A ranked panel and the size of every cross-section it was ranked in.

    Attributes:
        panel: The ranked values, carrying the transform that produced them.
        counts: Instant to the number of assets ranked at it. A thinning
            universe is visible here and nowhere else, because the ranks
            themselves always run 1..n whatever n is.
        method: The tie convention the ranks were produced under.
    """

    panel: FeaturePanel
    counts: Mapping[float, int]
    method: RankMethod

    @property
    def minimum_cross_section(self) -> int:
        """The smallest universe any instant was ranked in."""

        return min(self.counts.values()) if self.counts else 0


def _rank_rows(
    panel: FeaturePanel, method: RankMethod, minimum_assets: int, percentile: bool
) -> tuple[dict[float, dict[str, float]], dict[float, int]]:
    if minimum_assets < 2:
        raise FactorInputError(
            f"minimum_assets must be at least 2, got {minimum_assets}. A cross-section of "
            "one has nothing to rank against."
        )

    rows: dict[float, dict[str, float]] = {}
    counts: dict[float, int] = {}

    for stamp in panel.timestamps:
        section = panel.cross_section(stamp)
        if len(section) < minimum_assets:
            continue
        assets = sorted(section)
        values = [section[asset] for asset in assets]
        ordered = ranks(values, method)
        if percentile:
            span = len(assets) - 1
            ordered = tuple((rank - 1.0) / span for rank in ordered)
        rows[stamp] = dict(zip(assets, ordered, strict=True))
        counts[stamp] = len(assets)

    if not rows:
        raise FactorInputError(
            f"No instant in the panel held at least {minimum_assets} assets, so nothing "
            f"could be ranked. The largest cross-section was "
            f"{max((len(panel.cross_section(s)) for s in panel.timestamps), default=0)}."
        )
    return rows, counts


def rank_panel(
    panel: FeaturePanel,
    method: RankMethod = RankMethod.AVERAGE,
    minimum_assets: int = 2,
) -> FactorRanking:
    """Rank every cross-section from 1 (smallest value) upward.

    Instants holding fewer than ``minimum_assets`` are omitted rather than
    ranked in a universe too small to mean anything; the omission is visible as
    a missing key in :attr:`FactorRanking.counts`.

    Raises:
        FactorInputError: If ``minimum_assets`` is below 2, or if no instant in
            the panel held that many assets.
    """

    rows, counts = _rank_rows(panel, method, minimum_assets, percentile=False)
    ranked = panel.derive(
        rows, FactorTransform("rank", f"method={method.name},minimum_assets={minimum_assets}")
    )
    return FactorRanking(panel=ranked, counts=counts, method=method)


def percentile_rank_panel(
    panel: FeaturePanel,
    method: RankMethod = RankMethod.AVERAGE,
    minimum_assets: int = 2,
) -> FactorRanking:
    """Rank every cross-section onto ``[0, 1]``, lowest value at 0.

    ``(rank - 1) / (n - 1)``, which makes two instants with different universe
    sizes comparable -- the thing a raw rank is not.

    Raises:
        FactorInputError: For the same reasons :func:`rank_panel` does.
    """

    rows, counts = _rank_rows(panel, method, minimum_assets, percentile=True)
    ranked = panel.derive(
        rows,
        FactorTransform("percentile_rank", f"method={method.name},minimum_assets={minimum_assets}"),
    )
    return FactorRanking(panel=ranked, counts=counts, method=method)


def bucket_panel(
    panel: FeaturePanel,
    buckets: int,
    tie_break: TieBreak = TieBreak.LOW,
) -> FeaturePanel:
    """Sort every cross-section into ``buckets`` quantile buckets, 0 lowest.

    The panel a quantile study reads: bucket 0 is the assets a factor scores
    lowest at that instant and bucket ``buckets - 1`` the ones it scores
    highest. Instants holding fewer assets than buckets are omitted -- a
    bucketing that leaves buckets empty reports quantiles nothing was measured
    in, which :func:`~alphalab.common.statistics.bucket_index` refuses.

    Raises:
        FactorInputError: If ``buckets`` is below 2, or if no instant held
            enough assets to fill them.
    """

    if buckets < 2:
        raise FactorInputError(
            f"buckets must be at least 2, got {buckets}. One bucket is the universe."
        )

    rows: dict[float, dict[str, float]] = {}
    for stamp in panel.timestamps:
        section = panel.cross_section(stamp)
        if len(section) < buckets:
            continue
        assets = sorted(section)
        assigned = bucket_index([section[asset] for asset in assets], buckets, tie_break)
        rows[stamp] = {asset: float(index) for asset, index in zip(assets, assigned, strict=True)}

    if not rows:
        raise FactorInputError(
            f"No instant in the panel held the {buckets} assets needed to fill {buckets} buckets."
        )

    return panel.derive(
        rows, FactorTransform("bucket", f"buckets={buckets},tie_break={tie_break.name}")
    )
