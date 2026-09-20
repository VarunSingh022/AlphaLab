"""How much a factor's portfolio would have to trade, under a stated convention.

Turnover is a cost estimate, and the number depends entirely on a convention
nobody agrees on. Is it the sum of absolute weight changes, or half of it? Are
weights the ranks, the percentile ranks, or a long/short book built from
quantiles? Does an asset leaving the universe count as a trade or as an
absence?

Every one of those is a choice, so every one of them is a parameter here and
none of them has a silently applied default. :class:`TurnoverConvention` names
the two that matter most and :attr:`FactorTurnover.convention` records which
was used, because a turnover of 0.4 under one convention is a turnover of 0.8
under another and the two are quoted interchangeably in the literature.

Weights are supplied, not invented
----------------------------------

:func:`factor_turnover` measures the turnover of *weights*, and a factor score
is not a weight. :func:`weights_from_buckets` is offered as the one explicit
construction -- equal-weighted long in the top bucket, short in the bottom,
each side summing to one -- and it is a function a caller calls deliberately
rather than something applied inside the measurement. A study that weights
differently passes its own rows and the convention records nothing about how
they were built, which is honest: this module measures change, it does not
decide allocation.

Entries and exits
-----------------

An asset present at one instant and absent at the next has its weight go to
zero, and that is a trade -- the position had to be closed. Treating it as
absent instead would report a factor that drops half its universe every month
as having no turnover at all. :attr:`FactorTurnover.entries` and
:attr:`~FactorTurnover.exits` count them separately so the composition of the
number is visible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from itertools import pairwise

from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.panel import FeaturePanel

__all__ = [
    "FactorTurnover",
    "TurnoverConvention",
    "factor_turnover",
    "weights_from_buckets",
]


class TurnoverConvention(Enum):
    """Which of the two standard definitions to report."""

    #: ``sum(|w[t] - w[t-1]|)``. Counts both sides of a swap.
    ABSOLUTE_CHANGE = auto()
    #: ``sum(|w[t] - w[t-1]|) / 2``. The share of the book replaced, which is
    #: what a cost model usually wants because one swap is one round trip.
    ONE_WAY = auto()


@dataclass(frozen=True, slots=True)
class FactorTurnover:
    """Period-by-period turnover of a weighting, and how it was defined.

    Attributes:
        convention: The definition applied.
        mean_turnover: Average per-period turnover over ``periods_measured``.
        per_period: Instant to the turnover realized moving *into* it, so the
            first instant of the panel is absent -- there is no prior book to
            have traded from.
        periods_measured: How many transitions were measured.
        entries: Total asset-periods where a weight appeared from nothing.
        exits: Total asset-periods where a weight went to nothing.
    """

    convention: TurnoverConvention
    mean_turnover: float
    per_period: Mapping[float, float]
    periods_measured: int
    entries: int
    exits: int


def weights_from_buckets(bucketed: FeaturePanel, buckets: int) -> dict[float, dict[str, float]]:
    """Equal-weighted long/short weights from a bucketed panel.

    Long the top bucket, short the bottom, each side summing to 1.0 in absolute
    value so the book is dollar neutral by construction. Assets in the middle
    buckets get no weight and are simply absent.

    ``bucketed`` is what :func:`~alphalab.factor_library.ranking.bucket_panel`
    returns; ``buckets`` must be the same number it was built with, and is
    required rather than inferred because a panel in which the top bucket
    happened to be empty at one instant would otherwise be read as having fewer
    buckets there.

    Raises:
        FactorInputError: If ``buckets`` is below 2, or if the panel holds a
            bucket index outside ``[0, buckets - 1]``, which means it was built
            with a different number.
    """

    if buckets < 2:
        raise FactorInputError(f"buckets must be at least 2, got {buckets}.")

    top = float(buckets - 1)
    rows: dict[float, dict[str, float]] = {}

    for stamp in bucketed.timestamps:
        section = bucketed.cross_section(stamp)
        for asset, index in section.items():
            if not 0.0 <= index <= top:
                raise FactorInputError(
                    f"{asset} is in bucket {index!r} at {stamp!r}, outside [0, {top}]. The "
                    f"panel was not bucketed into {buckets} buckets."
                )
        longs = sorted(asset for asset, index in section.items() if index == top)
        shorts = sorted(asset for asset, index in section.items() if index == 0.0)
        if not longs or not shorts:
            continue
        row = {asset: 1.0 / len(longs) for asset in longs}
        row.update({asset: -1.0 / len(shorts) for asset in shorts})
        rows[stamp] = row

    if not rows:
        raise FactorInputError(
            "No instant held both a top and a bottom bucket, so no long/short book could be formed."
        )
    return rows


def factor_turnover(
    weights: Mapping[float, Mapping[str, float]],
    convention: TurnoverConvention = TurnoverConvention.ONE_WAY,
) -> FactorTurnover:
    """Measure how much a weighting changes from each instant to the next.

    An asset present in one book and not the next contributes its full weight,
    because closing a position is a trade. Instants are compared in ascending
    timestamp order regardless of the mapping's own order.

    Raises:
        FactorInputError: If fewer than two instants are given -- turnover is a
            property of a transition, and one book has not moved.
    """

    instants = sorted(weights)
    if len(instants) < 2:
        raise FactorInputError(
            f"Turnover needs at least 2 instants to measure a change between, got "
            f"{len(instants)}. A single book has not traded."
        )

    per_period: dict[float, float] = {}
    entries = 0
    exits = 0

    for previous, current in pairwise(instants):
        before = weights[previous]
        after = weights[current]
        traded = 0.0
        for asset in set(before) | set(after):
            was = before.get(asset, 0.0)
            now = after.get(asset, 0.0)
            traded += abs(now - was)
            if asset not in before:
                entries += 1
            if asset not in after:
                exits += 1
        per_period[current] = (
            traded if convention is TurnoverConvention.ABSOLUTE_CHANGE else traded / 2.0
        )

    return FactorTurnover(
        convention=convention,
        mean_turnover=sum(per_period.values()) / len(per_period),
        per_period=per_period,
        periods_measured=len(per_period),
        entries=entries,
        exits=exits,
    )
