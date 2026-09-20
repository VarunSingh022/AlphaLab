"""Robustness: change one thing on purpose, and record exactly what was changed.

A robustness test asks whether a result survives a change it should survive.
That only means something if three things are true of every run: the baseline
is untouched, exactly one thing was perturbed, and the perturbation can be
reproduced. This module is built around making all three structural rather than
conventional.

**The baseline is never altered.** Every function here returns new data and
leaves its input alone; the panels and frames are frozen dataclasses of tuples,
so this is not a promise, it is the type. :attr:`PerturbationRun.baseline`
records the baseline's own identity beside the perturbed result, so a
comparison names both sides.

**One perturbation per run.** :class:`Perturbation` describes a single change.
Composing two is done by running two studies, not by a function that takes six
optional keyword arguments and applies whichever are not ``None`` -- which is
how a run ends up reporting the effect of a change nobody remembers requesting.

**Stochastic means seeded.** Every function that draws a random number takes a
seed and records it on the result. There is no default seed and no unseeded
path: :class:`random.Random` seeded explicitly is reproducible across Python
versions, which is the same guarantee
:class:`~alphalab.common.ids.DeterministicIdSource` rests on, and the seed on
the result is what lets somebody else get the same numbers.

These are experiments, not execution features
---------------------------------------------

:func:`delay_signal` moves a signal later in time and :func:`apply_cost` debits
a return. Neither is an execution model, and neither is trying to be: AlphaLab
already has an execution path with a simulator, commissions and fill policies,
and this module does not reimplement any of it. What these do is answer "how
much of this result depends on acting instantly and trading for free?", which
is a research question that is asked before an execution model is built, and
whose answer is often that there is nothing there to execute.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from types import MappingProxyType

from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.factor_library.panel import FactorTransform, FeaturePanel
from alphalab.research.exceptions import ResearchValidationError

__all__ = [
    "Perturbation",
    "PerturbationKind",
    "PerturbationRun",
    "apply_cost",
    "block_bootstrap_indices",
    "delay_signal",
    "drop_observations",
    "monte_carlo_orders",
    "perturb_observations",
    "perturb_signal",
    "sample_by",
    "shift_parameter",
]


class PerturbationKind(Enum):
    """What kind of change a run applied."""

    #: A numeric parameter of a feature definition was moved.
    PARAMETER = auto()
    #: The underlying observations were jittered.
    DATA = auto()
    #: The signal values were jittered.
    SIGNAL = auto()
    #: Observations were removed at random, simulating missing data.
    MISSING_DATA = auto()
    #: The signal was acted on later than it was computed.
    EXECUTION_DELAY = auto()
    #: A per-period cost was charged against returns.
    EXECUTION_COST = auto()
    #: The sample was resampled in blocks, preserving local dependence.
    BLOCK_BOOTSTRAP = auto()
    #: The order of periods was permuted.
    MONTE_CARLO = auto()


@dataclass(frozen=True, slots=True)
class Perturbation:
    """One change, described well enough to be applied again.

    Attributes:
        kind: Which change it is.
        magnitude: The size of it, in whatever unit the kind uses -- periods
            for a delay, a fraction for a cost, a relative standard deviation
            for a jitter.
        seed: The seed a stochastic perturbation drew from, or ``None`` for a
            deterministic one. A stochastic kind with no seed is refused.
        detail: Free-form extra description, for instance which parameter a
            ``PARAMETER`` perturbation moved.

    Raises:
        ResearchValidationError: If a stochastic kind carries no seed, or a
            deterministic kind carries one -- a seed recorded on a run that
            never drew a number would suggest a reproducibility guarantee that
            has nothing to guarantee.
    """

    kind: PerturbationKind
    magnitude: float
    seed: int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        stochastic = self.kind in _STOCHASTIC_KINDS
        if stochastic and self.seed is None:
            raise ResearchValidationError(
                f"{self.kind.name} draws random numbers and no seed was recorded, so the "
                "run cannot be reproduced. Every stochastic perturbation in AlphaLab takes "
                "an explicit seed; there is no default, because a hidden default makes a "
                "result reproducible only by accident."
            )
        if not stochastic and self.seed is not None:
            raise ResearchValidationError(
                f"{self.kind.name} is deterministic and a seed of {self.seed} was recorded. "
                "A seed on a run that drew no random number suggests a guarantee it has "
                "nothing to make."
            )

    def __str__(self) -> str:
        seed = "" if self.seed is None else f",seed={self.seed}"
        detail = f",{self.detail}" if self.detail else ""
        return f"{self.kind.name}(magnitude={self.magnitude!r}{seed}{detail})"


_STOCHASTIC_KINDS = frozenset(
    {
        PerturbationKind.DATA,
        PerturbationKind.SIGNAL,
        PerturbationKind.MISSING_DATA,
        PerturbationKind.BLOCK_BOOTSTRAP,
        PerturbationKind.MONTE_CARLO,
    }
)


@dataclass(frozen=True, slots=True)
class PerturbationRun:
    """One perturbed measurement, beside the baseline it is compared against.

    Attributes:
        perturbation: What was changed.
        baseline: A rendered identity of the unperturbed configuration -- a
            feature version, a panel lineage, whatever names the thing that was
            not changed.
        dataset_version: The dataset both sides were measured on, or ``None``.
        metrics: The perturbed run's numbers.
        baseline_metrics: The same numbers from the unperturbed run, so a
            reader never has to go and find them.

    Raises:
        ResearchValidationError: If the two metric mappings do not name the
            same quantities. Comparing a perturbed Sharpe against a baseline
            information ratio is not a robustness finding.
    """

    perturbation: Perturbation
    baseline: str
    dataset_version: str | None
    metrics: Mapping[str, float] = field(default_factory=dict)
    baseline_metrics: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(self, "baseline_metrics", MappingProxyType(dict(self.baseline_metrics)))
        if set(self.metrics) != set(self.baseline_metrics):
            raise ResearchValidationError(
                f"A perturbation run reports {sorted(self.metrics)} and its baseline reports "
                f"{sorted(self.baseline_metrics)}. Two different quantities compared side by "
                "side would read as a change in one."
            )

    def change(self, metric: str) -> float:
        """Perturbed less baseline, for one metric.

        Raises:
            ResearchValidationError: If neither side carries ``metric``.
        """

        if metric not in self.metrics:
            raise ResearchValidationError(
                f"This run does not report {metric!r}; it reports {sorted(self.metrics)}."
            )
        return self.metrics[metric] - self.baseline_metrics[metric]

    def relative_change(self, metric: str) -> float | None:
        """``(perturbed - baseline) / |baseline|``, or ``None`` at a zero baseline.

        ``None`` rather than infinity or a large number: a relative change
        against a baseline of zero is undefined, and any finite stand-in would
        be read as a measured degradation.
        """

        base = self.baseline_metrics[metric]
        if base == 0.0:
            return None
        return self.change(metric) / abs(base)


# --------------------------------------------------------------------------- #
# Parameter perturbation
# --------------------------------------------------------------------------- #


def shift_parameter(window: int, relative: float) -> int:
    """A window moved by a relative fraction, rounded away from no change.

    ``shift_parameter(20, 0.1)`` is 22 and ``shift_parameter(20, -0.1)`` is 18.
    Rounding away from zero matters for small windows: a 10% shift on a window
    of 3 rounds to 0 under ordinary rounding, and a "perturbation" that changed
    nothing would be recorded as one that the result survived.

    Raises:
        ResearchValidationError: If ``window`` is not positive, or if the shift
            would take it below 1.
    """

    if window < 1:
        raise ResearchValidationError(f"A window must be at least 1, got {window}.")

    magnitude = abs(window * relative)
    step = max(1, int(magnitude + 0.5)) if magnitude > 0.0 else 0
    shifted = window + (step if relative > 0.0 else -step)

    if shifted < 1:
        raise ResearchValidationError(
            f"Shifting a window of {window} by {relative!r} gives {shifted}, which is not a "
            "window. Use a smaller perturbation, or a longer baseline window."
        )
    return shifted


# --------------------------------------------------------------------------- #
# Data and signal perturbation
# --------------------------------------------------------------------------- #


def perturb_observations(
    frame: ObservationFrame, relative_sigma: float, seed: int
) -> ObservationFrame:
    """Multiply every observation by ``1 + N(0, relative_sigma)``.

    Multiplicative rather than additive, because a price series' natural noise
    scale is proportional: an additive jitter of 0.5 is nothing to a stock at
    400 and catastrophic to one at 0.8, and the perturbation would then be
    testing the universe's price levels rather than the result's robustness.

    Symbols are drawn in sorted order and observations in series order, so the
    same seed produces the same frame regardless of how the input was built.

    Raises:
        ResearchValidationError: If ``relative_sigma`` is not positive, or if a
            draw would take an observation to zero or below -- which would make
            every ratio computed from it undefined and is a sign the sigma is
            too large for the data rather than a result to report.
    """

    if relative_sigma <= 0.0:
        raise ResearchValidationError(
            f"relative_sigma must be positive, got {relative_sigma!r}. A perturbation of "
            "zero changes nothing and would be recorded as one the result survived."
        )

    prng = random.Random(seed)
    perturbed: dict[str, ObservationSeries] = {}

    for symbol in frame.symbols:
        row = frame.series[symbol]
        values = []
        for value in row.values:
            shifted = value * (1.0 + prng.gauss(0.0, relative_sigma))
            if value > 0.0 >= shifted:
                raise ResearchValidationError(
                    f"Perturbing {symbol} at relative_sigma={relative_sigma!r} took a "
                    f"positive observation {value!r} to {shifted!r}. A sigma that can flip "
                    "the sign of a price is testing the arithmetic, not the result."
                )
            values.append(shifted)
        perturbed[symbol] = ObservationSeries(symbol, row.timestamps, tuple(values))

    return ObservationFrame(
        source_field=frame.source_field,
        series=perturbed,
        timezone_name=frame.timezone_name,
        dataset_version=frame.dataset_version,
    )


def perturb_signal(panel: FeaturePanel, absolute_sigma: float, seed: int) -> FeaturePanel:
    """Add ``N(0, absolute_sigma)`` to every signal value.

    Additive here, where :func:`perturb_observations` is multiplicative,
    because a signal is not a price: a rank, a z-score and a demeaned factor
    are all centred near zero, and a multiplicative jitter would leave a value
    of exactly zero untouched while moving the extremes most.

    Raises:
        ResearchValidationError: If ``absolute_sigma`` is not positive.
    """

    if absolute_sigma <= 0.0:
        raise ResearchValidationError(f"absolute_sigma must be positive, got {absolute_sigma!r}.")

    prng = random.Random(seed)
    rows = {
        stamp: {
            asset: panel.rows[stamp][asset] + prng.gauss(0.0, absolute_sigma)
            for asset in sorted(panel.rows[stamp])
        }
        for stamp in panel.timestamps
    }
    return panel.derive(
        rows, FactorTransform("perturb_signal", f"sigma={absolute_sigma!r},seed={seed}")
    )


def drop_observations(frame: ObservationFrame, fraction: float, seed: int) -> ObservationFrame:
    """Remove ``fraction`` of every symbol's observations at random.

    Simulates missing data by *deleting*, never by filling: the resulting
    series is shorter and its gaps are real, which is exactly what the feature
    layer's :class:`~alphalab.factor_library.definition.MissingPolicy` exists
    to handle. A version of this that filled the holes would be testing the
    fill rather than the robustness.

    Raises:
        ResearchValidationError: If ``fraction`` is outside ``(0, 1)``, or if
            it would empty a symbol's series.
    """

    if not 0.0 < fraction < 1.0:
        raise ResearchValidationError(
            f"fraction must lie strictly between 0 and 1, got {fraction!r}. Zero drops "
            "nothing and one drops the dataset."
        )

    prng = random.Random(seed)
    kept: dict[str, ObservationSeries] = {}

    for symbol in frame.symbols:
        row = frame.series[symbol]
        survivors = [index for index in range(len(row)) if prng.random() >= fraction]
        if not survivors:
            raise ResearchValidationError(
                f"Dropping {fraction:.0%} of {symbol}'s {len(row)} observation(s) left none. "
                "A symbol with no observations is not missing data, it is an absent asset."
            )
        kept[symbol] = ObservationSeries(
            symbol,
            tuple(row.timestamps[index] for index in survivors),
            tuple(row.values[index] for index in survivors),
        )

    return ObservationFrame(
        source_field=frame.source_field,
        series=kept,
        timezone_name=frame.timezone_name,
        dataset_version=frame.dataset_version,
    )


# --------------------------------------------------------------------------- #
# Execution assumptions
# --------------------------------------------------------------------------- #


def delay_signal(panel: FeaturePanel, periods: int) -> FeaturePanel:
    """Act on each signal ``periods`` instants later than it was computed.

    The signal at instant ``t`` is moved to instant ``t + periods`` on the
    panel's own index, so the diagnostic that follows correlates a *stale*
    signal with the same forward returns. The final ``periods`` instants of the
    original panel have nowhere to move to and are dropped; the first
    ``periods`` instants of the result have no signal and are absent.

    This is the cheapest and most informative robustness test there is. A
    result that vanishes at a delay of one period was measuring the
    simultaneity of the signal and the outcome, not a prediction.

    Raises:
        ResearchValidationError: If ``periods`` is not positive, or if the
            panel is shorter than the delay.
    """

    if periods < 1:
        raise ResearchValidationError(
            f"A delay must be at least 1 instant, got {periods}. A delay of zero is the "
            "baseline, and recording it as a perturbation would claim a test that did "
            "not happen."
        )

    instants = panel.timestamps
    if len(instants) <= periods:
        raise ResearchValidationError(
            f"Delaying by {periods} instant(s) on a panel of {len(instants)} leaves nothing."
        )

    rows = {
        instants[index + periods]: dict(panel.rows[instants[index]])
        for index in range(len(instants) - periods)
    }
    return panel.derive(rows, FactorTransform("delay", f"periods={periods}"))


def apply_cost(
    returns: Mapping[float, Mapping[str, float]], cost_per_period: float
) -> dict[float, dict[str, float]]:
    """Charge a flat ``cost_per_period`` against every realized return.

    The crudest possible cost model, and deliberately so: it answers "how big
    would costs have to be to erase this?", which is a research question with a
    single number for an answer. It is not an execution model, and a result
    that survives it has not been shown to be tradeable -- see the module
    docstring.

    Raises:
        ResearchValidationError: If ``cost_per_period`` is negative. A negative
            cost is a subsidy, and a robustness test that improved the result
            is not a test.
    """

    if cost_per_period < 0.0:
        raise ResearchValidationError(
            f"cost_per_period must not be negative, got {cost_per_period!r}."
        )
    return {
        stamp: {asset: value - cost_per_period for asset, value in row.items()}
        for stamp, row in returns.items()
    }


# --------------------------------------------------------------------------- #
# Resampling
# --------------------------------------------------------------------------- #


def block_bootstrap_indices(
    count: int, block_size: int, seed: int, blocks: int | None = None
) -> tuple[int, ...]:
    """Positions for a moving-block bootstrap sample of a series of ``count``.

    Blocks rather than individual observations, because an IID bootstrap
    destroys exactly the serial dependence a time-series result depends on --
    resampling daily returns one at a time produces a sample with no
    autocorrelation, no volatility clustering and no trends, and a confidence
    interval built from it is an interval for a different series. The existing
    :func:`~alphalab.research.bootstrap.bootstrap_statistics` resamples a
    *completed run's* returns independently, which is the right thing there and
    the wrong thing here; the two are kept separate rather than merged.

    Returns positions, not values, so the caller resamples whatever they hold
    -- returns, signals, folds -- with one implementation.

    Raises:
        ResearchValidationError: If ``count`` or ``block_size`` is not
            positive, or if ``block_size`` exceeds ``count``.
    """

    if count < 1:
        raise ResearchValidationError(f"count must be at least 1, got {count}.")
    if block_size < 1:
        raise ResearchValidationError(f"block_size must be at least 1, got {block_size}.")
    if block_size > count:
        raise ResearchValidationError(
            f"block_size {block_size} exceeds the series length {count}; there is no block to draw."
        )

    wanted = blocks if blocks is not None else -(-count // block_size)
    if wanted < 1:
        raise ResearchValidationError(f"blocks must be at least 1, got {wanted}.")

    prng = random.Random(seed)
    last_start = count - block_size
    drawn: list[int] = []
    for _ in range(wanted):
        start = prng.randint(0, last_start)
        drawn.extend(range(start, start + block_size))
    return tuple(drawn[:count])


def monte_carlo_orders(count: int, paths: int, seed: int) -> tuple[tuple[int, ...], ...]:
    """``paths`` permutations of ``range(count)``, drawn from one seeded stream.

    Each path is a fresh shuffle of the *original* order rather than a shuffle
    of the previous path. That distinction is not cosmetic: repeatedly
    shuffling one list in place, as
    :func:`~alphalab.research.montecarlo.monte_carlo_simulation` does for a
    completed run's returns, makes each path depend on every path before it, so
    a caller cannot reproduce path 500 without generating the 499 in front of
    it. Here each path is independent given the seed and the index.

    Raises:
        ResearchValidationError: If ``count`` or ``paths`` is not positive.
    """

    if count < 1:
        raise ResearchValidationError(f"count must be at least 1, got {count}.")
    if paths < 1:
        raise ResearchValidationError(f"paths must be at least 1, got {paths}.")

    prng = random.Random(seed)
    generated: list[tuple[int, ...]] = []
    for _ in range(paths):
        order = list(range(count))
        prng.shuffle(order)
        generated.append(tuple(order))
    return tuple(generated)


def sample_by(values: Sequence[float], positions: Sequence[int]) -> tuple[float, ...]:
    """Reorder or resample ``values`` by ``positions``.

    The other half of :func:`block_bootstrap_indices` and
    :func:`monte_carlo_orders`, kept as a named function so a caller never has
    to write the indexing themselves and get it subtly wrong.

    Raises:
        ResearchValidationError: If a position is out of range.
    """

    count = len(values)
    for position in positions:
        if not 0 <= position < count:
            raise ResearchValidationError(
                f"Position {position} is outside a series of {count} value(s)."
            )
    return tuple(values[position] for position in positions)
