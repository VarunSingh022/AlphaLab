"""Does the signal predict returns -- at what horizon, for whom, and when?

A signal diagnostic answers a question with a sample size attached, or it
declines to answer. That is the whole design here. Every quantity on
:class:`SignalDiagnostics` is accompanied by the number of observations behind
it, and every one of them is ``None`` rather than ``0.0`` when there was not
enough to measure -- because a zero information coefficient means "measured,
and unrelated", which is a finding, and an absent one means "not measured",
which is not.

No single score
---------------

The roadmap asks for detailed diagnostics rather than an opaque "signal score",
and this module has none. There is no weighted blend of the rank IC, the
quantile spread and the hit rate into one number out of a hundred. Such a
number is not a measurement: the weights are a judgement, and once blended the
reader cannot tell a factor with a strong monotone quantile profile and a weak
IC from its opposite, which are different factors with different failure modes.

What is reported, and what each one is for
------------------------------------------

* **rank IC** -- the ordering relationship, via
  :func:`~alphalab.factor_library.ic.information_coefficient`, reused rather
  than recomputed so a signal's IC and a factor's IC are the same number
  computed the same way.
* **quantile profile** -- the mean and median forward return of each bucket,
  with its own count. This is where a factor that only works in its extremes
  becomes visible, which an IC alone hides.
* **monotonicity** -- the rank correlation between bucket index and bucket mean
  return. A factor with a strong spread and a non-monotone middle is a
  different proposition from one that grades smoothly.
* **spread** -- top bucket mean less bottom bucket mean, the crudest and most
  quoted number, reported last and never alone.

Conditioning is the caller's labels
-----------------------------------

:func:`conditional_diagnostics` partitions instants by a label the caller
supplies -- a regime, a volatility bucket, a session, a day of week -- and runs
the whole diagnostic within each. AlphaLab defines no regime taxonomy of its
own for this: which states the world is in is a research question, and a
library that shipped an answer would be making it for everybody. A caller that
wants a volatility regime computes one as a feature and buckets it, which is
what :func:`~alphalab.factor_library.ranking.bucket_panel` is for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import TieBreak, bucket_index, mean, median, rank_correlation
from alphalab.factor_library.forward_returns import ForwardReturnPanel, forward_returns
from alphalab.factor_library.ic import InformationCoefficient, information_coefficient
from alphalab.factor_library.observations import ObservationFrame
from alphalab.factor_library.panel import FactorTransform, FeaturePanel
from alphalab.research.exceptions import ResearchValidationError

__all__ = [
    "QuantileBucket",
    "SignalDiagnostics",
    "conditional_diagnostics",
    "signal_diagnostics",
    "signal_horizons",
]


@dataclass(frozen=True, slots=True)
class QuantileBucket:
    """One quantile of a signal, and what happened after it.

    Attributes:
        bucket: Index from 0 (lowest signal) upward.
        observations: Asset-instant pairs that landed here.
        instants: Distinct instants that contributed at least one.
        mean_forward_return: Mean realized forward return of those pairs.
        median_forward_return: Median of the same, which a handful of extreme
            outcomes cannot move the way a mean can.
    """

    bucket: int
    observations: int
    instants: int
    mean_forward_return: float
    median_forward_return: float


@dataclass(frozen=True, slots=True)
class SignalDiagnostics:
    """Everything one signal/horizon pair can be said to have shown.

    Attributes:
        signal_lineage: The signal's feature version and transform chain.
        dataset_version: The data behind it, or ``None``.
        horizon: The forward horizon in periods.
        label: Which conditioning slice this is, or ``""`` for the whole
            sample. Set by :func:`conditional_diagnostics`.
        observations: Asset-instant pairs with both a signal and a realized
            return.
        instants: Distinct instants those pairs came from.
        rank_ic: The full information coefficient result, including its own
            sample counts and its per-instant series.
        quantiles: The bucket profile, ascending, or empty when the sample
            never held enough assets to fill the buckets.
        buckets_requested: How many buckets were asked for.
        monotonicity: Rank correlation between bucket index and bucket mean
            return, in ``[-1, 1]``, or ``None`` when fewer than three buckets
            were populated. ``1.0`` is a perfectly graded factor.
        spread: Top bucket mean less bottom bucket mean, or ``None`` when the
            profile is empty.
    """

    signal_lineage: str
    dataset_version: str | None
    horizon: int
    label: str
    observations: int
    instants: int
    rank_ic: InformationCoefficient
    quantiles: tuple[QuantileBucket, ...]
    buckets_requested: int
    monotonicity: float | None
    spread: float | None

    @property
    def is_measurable(self) -> bool:
        """Whether anything at all could be measured on this sample."""

        return self.rank_ic.is_measurable or bool(self.quantiles)

    def describe(self) -> str:
        """One line for a report, naming the sample the numbers rest on."""

        slice_name = f"[{self.label}] " if self.label else ""
        rank = "-" if self.rank_ic.mean_rank is None else f"{self.rank_ic.mean_rank:+.4f}"
        spread = "-" if self.spread is None else f"{self.spread:+.4%}"
        return (
            f"{slice_name}h={self.horizon}: rank_ic={rank} spread={spread} "
            f"n={self.observations} over {self.instants} instant(s)"
        )


def _quantile_profile(
    signal: FeaturePanel,
    returns: ForwardReturnPanel,
    buckets: int,
    tie_break: TieBreak,
    instants: Sequence[float],
) -> tuple[QuantileBucket, ...]:
    """Bucket each cross-section, then pool the forward returns per bucket.

    Bucketing happens *within* each instant, so a bucket always means "the
    lowest fifth of the universe that day" rather than "the lowest fifth of all
    values ever seen", which would drift with the level of the factor.
    """

    pooled: dict[int, list[float]] = {index: [] for index in range(buckets)}
    contributing: dict[int, set[float]] = {index: set() for index in range(buckets)}

    for stamp in instants:
        scores = signal.cross_section(stamp)
        realized = returns.cross_section(stamp)
        shared = sorted(set(scores) & set(realized))
        if len(shared) < buckets:
            continue
        assigned = bucket_index([scores[asset] for asset in shared], buckets, tie_break)
        for asset, index in zip(shared, assigned, strict=True):
            pooled[index].append(realized[asset])
            contributing[index].add(stamp)

    if not any(pooled.values()):
        return ()

    return tuple(
        QuantileBucket(
            bucket=index,
            observations=len(pooled[index]),
            instants=len(contributing[index]),
            mean_forward_return=mean(pooled[index]),
            median_forward_return=median(pooled[index]),
        )
        for index in range(buckets)
        if pooled[index]
    )


def signal_diagnostics(
    signal: FeaturePanel,
    returns: ForwardReturnPanel,
    buckets: int = 5,
    minimum_assets: int = 5,
    tie_break: TieBreak = TieBreak.LOW,
    label: str = "",
    instants: Sequence[float] | None = None,
) -> SignalDiagnostics:
    """Measure one signal against one horizon's realized forward returns.

    ``instants`` restricts the measurement to a subset of the signal's instants
    -- what :func:`conditional_diagnostics` uses to slice by regime, and what a
    fold uses to evaluate on its own validation window. ``None`` measures
    everything the two panels share.

    Raises:
        ResearchValidationError: If ``buckets`` is below 2, or if the signal
            and the returns were computed from different dataset versions.
    """

    if buckets < 2:
        raise ResearchValidationError(
            f"buckets must be at least 2, got {buckets}. One bucket is the universe, and "
            "its spread against itself is zero by construction."
        )
    if signal.dataset_version != returns.dataset_version:
        raise ResearchValidationError(
            f"The signal was computed on {signal.dataset_version!r} and the forward returns "
            f"on {returns.dataset_version!r}. Measuring one against the other would report "
            "a relationship between two different datasets."
        )

    chosen = tuple(signal.timestamps) if instants is None else tuple(sorted(set(instants)))

    observations = 0
    contributing = 0
    for stamp in chosen:
        shared = set(signal.cross_section(stamp)) & set(returns.cross_section(stamp))
        if shared:
            observations += len(shared)
            contributing += 1

    restricted = signal if instants is None else _restrict(signal, chosen)
    rank_ic = information_coefficient(restricted, returns, minimum_assets)
    profile = _quantile_profile(restricted, returns, buckets, tie_break, chosen)

    monotonicity: float | None = None
    if len(profile) >= 3:
        try:
            monotonicity = rank_correlation(
                [float(item.bucket) for item in profile],
                [item.mean_forward_return for item in profile],
            )
        except AlphaLabValidationError:
            # Every bucket returned exactly the same mean: no ordering to
            # correlate with, which is undefined rather than zero.
            monotonicity = None

    return SignalDiagnostics(
        signal_lineage=signal.lineage,
        dataset_version=signal.dataset_version,
        horizon=returns.horizon,
        label=label,
        observations=observations,
        instants=contributing,
        rank_ic=rank_ic,
        quantiles=profile,
        buckets_requested=buckets,
        monotonicity=monotonicity,
        spread=(
            profile[-1].mean_forward_return - profile[0].mean_forward_return
            if len(profile) >= 2
            else None
        ),
    )


def _restrict(panel: FeaturePanel, instants: Sequence[float]) -> FeaturePanel:
    """A panel holding only ``instants``, with its transform chain preserved."""

    wanted = set(instants)
    rows = {stamp: row for stamp, row in panel.rows.items() if stamp in wanted}
    if not rows:
        raise ResearchValidationError(
            f"None of the {len(wanted)} requested instant(s) are in the signal panel, so "
            "there is nothing to measure on this slice."
        )
    return panel.derive(rows, FactorTransform("restrict", f"instants={len(rows)}"))


def signal_horizons(
    signal: FeaturePanel,
    prices: ObservationFrame,
    horizons: Sequence[int],
    buckets: int = 5,
    minimum_assets: int = 5,
) -> dict[int, SignalDiagnostics]:
    """Run the full diagnostic at each of several forward horizons.

    The horizon answer to "at what horizon does this predict?", where
    :func:`~alphalab.factor_library.decay.factor_decay` answers the narrower IC
    version of the same question. Both are offered: decay is cheaper and is
    what a factor sweep wants, this is what a single signal's write-up wants.

    Raises:
        ResearchValidationError: If ``horizons`` is empty or repeats a value.
    """

    if not horizons:
        raise ResearchValidationError("A horizon study needs at least one horizon.")
    if len(set(horizons)) != len(horizons):
        raise ResearchValidationError(
            f"The horizons {sorted(horizons)} repeat one; each is measured once."
        )

    return {
        horizon: signal_diagnostics(
            signal, forward_returns(prices, horizon), buckets, minimum_assets
        )
        for horizon in sorted(horizons)
    }


def conditional_diagnostics(
    signal: FeaturePanel,
    returns: ForwardReturnPanel,
    regimes: Mapping[float, str],
    buckets: int = 5,
    minimum_assets: int = 5,
    minimum_instants: int = 2,
) -> dict[str, SignalDiagnostics]:
    """Run the diagnostic separately within each labelled slice of time.

    ``regimes`` maps instant to a label the caller chose. Instants the mapping
    does not name are excluded from every slice rather than pooled into an
    "other" bucket, for the same reason
    :func:`~alphalab.factor_library.neutralization.neutralize_group` refuses an
    unnamed asset: a category nobody defined is not a finding about it.

    Slices holding fewer than ``minimum_instants`` are omitted entirely. A
    regime that occurred twice does not support a conditional claim, and
    reporting one with its ``instants`` attached is a weaker protection than
    not reporting it -- readers compare the numbers and skip the counts.

    Raises:
        ResearchValidationError: If ``regimes`` is empty, if ``minimum_instants``
            is below 1, or if no slice met the threshold.
    """

    if not regimes:
        raise ResearchValidationError(
            "Conditional diagnostics need a label for each instant and were given none."
        )
    if minimum_instants < 1:
        raise ResearchValidationError(
            f"minimum_instants must be at least 1, got {minimum_instants}."
        )

    grouped: dict[str, list[float]] = {}
    for stamp in signal.timestamps:
        found = regimes.get(stamp)
        if found is not None:
            grouped.setdefault(found, []).append(stamp)

    measured = {
        name: signal_diagnostics(
            signal, returns, buckets, minimum_assets, label=name, instants=stamps
        )
        for name, stamps in sorted(grouped.items())
        if len(stamps) >= minimum_instants
    }

    if not measured:
        raise ResearchValidationError(
            f"No regime held at least {minimum_instants} instant(s) of the signal. The "
            f"labels present were {sorted(grouped)} with counts "
            f"{sorted((name, len(stamps)) for name, stamps in grouped.items())}."
        )
    return measured
