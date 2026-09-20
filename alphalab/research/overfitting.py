"""Overfitting diagnostics: measurements, thresholds and interpretation, kept apart.

There is no overfit score here. The roadmap says not to invent one and it is
right, for a reason worth stating: a single number blending parameter
sensitivity, out-of-sample degradation and the number of configurations tried
would have to weight them, the weights would be a judgement nobody could
inspect, and the resulting figure would be quoted as though it were measured.
Worse, it would be unfalsifiable -- there is no experiment that shows an
overfit score of 63 to be wrong.

What this module produces instead is three kinds of thing, kept in separate
fields so a reader can tell them apart:

**Measured quantities.** ``in_sample``, ``out_of_sample``, ``degradation``,
``sensitivity``, ``trials``, ``effective_parameters``. Facts about the runs
that were performed. These do not depend on anybody's opinion.

**Thresholds.** :class:`OverfittingPolicy` carries the bounds a caller states
*in advance*. AlphaLab ships no default policy, because a default is a research
standard chosen by a library.

**Interpretation.** :attr:`OverfittingReport.findings` is a tuple of sentences
naming which stated threshold each measurement crossed. Every sentence names
the number and the bound, so it can be checked. Nothing here says a strategy is
overfit; it says what was measured and which of the caller's own bounds it
exceeded.

The multiple-testing correction, and its assumption
---------------------------------------------------

:attr:`OverfittingReport.bonferroni_alpha` is the only correction offered, and
it is offered because it is the only one whose assumption can be stated in one
line: for ``k`` configurations tried, a nominal significance of ``alpha``
becomes ``alpha / k``. It is conservative when the trials are correlated --
which, for a parameter sweep over neighbouring windows, they strongly are --
and the report says so rather than leaving a reader to assume otherwise. A
Šidák or a false-discovery-rate correction needs distributional assumptions
this module cannot check, and a deflated Sharpe ratio needs the variance of the
trial statistics *and* an assumption of normality that daily returns do not
satisfy.

What ``trials`` must count
--------------------------

The number of configurations *evaluated*, not the number reported. A sweep that
tried two hundred windows and wrote up the best one has two hundred trials, and
recording one would make every correction meaningless. :func:`parameter_sweep`
counts them by construction, which is the main reason to run a sweep through
it rather than by hand.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from alphalab.common.statistics import mean, sample_variance
from alphalab.research.exceptions import ResearchValidationError

__all__ = [
    "OverfittingPolicy",
    "OverfittingReport",
    "StabilityReport",
    "SweepResult",
    "build_overfitting_report",
    "parameter_sweep",
    "period_stability",
    "sample_degradation",
    "symbol_stability",
]


@dataclass(frozen=True, slots=True)
class SweepResult:
    """One parameter sweep, and every configuration it actually evaluated.

    Attributes:
        metric: What was measured at each configuration.
        scores: Rendered configuration to its score, for every configuration
            evaluated. The full set, not the survivors.
        best: The configuration with the highest score.
        best_score: Its score.
        sensitivity: Coefficient of variation of the scores -- the standard
            deviation divided by the absolute mean. Scale-free, so a sweep over
            Sharpe ratios and one over information coefficients are comparable.
            ``None`` when fewer than two configurations were tried, or when the
            mean score is zero and the ratio is therefore undefined.
        neighbour_drop: How far the score falls from the best configuration to
            the best of its immediate neighbours in the sweep order, as a
            fraction of the best. A large value is the "cliff" a fragile
            optimum sits on. ``None`` when the best configuration is at an end
            of the sweep and has only one neighbour, or when the best score is
            zero.
    """

    metric: str
    scores: Mapping[str, float]
    best: str
    best_score: float
    sensitivity: float | None
    neighbour_drop: float | None

    @property
    def trials(self) -> int:
        """How many configurations were evaluated."""

        return len(self.scores)


def parameter_sweep(
    metric: str,
    configurations: Sequence[str],
    evaluate: Callable[[str], float],
) -> SweepResult:
    """Evaluate every configuration and report the whole surface.

    ``configurations`` are rendered descriptions -- ``"window=20"``,
    ``"window=25"`` -- in the order they should be considered neighbours, which
    is what makes :attr:`SweepResult.neighbour_drop` meaningful. A sweep over
    an unordered set of unrelated configurations passes them in any order and
    ignores that field.

    Every configuration is evaluated; none is skipped on the basis of an
    earlier result. That is deliberate and is what makes
    :attr:`SweepResult.trials` a true count of the search, which every
    multiple-testing correction downstream depends on.

    Raises:
        ResearchValidationError: If ``configurations`` is empty or repeats one.
    """

    if not configurations:
        raise ResearchValidationError("A sweep needs at least one configuration.")
    if len(set(configurations)) != len(configurations):
        raise ResearchValidationError(
            "The sweep repeats a configuration, so its trial count would understate the "
            "search that was actually performed."
        )

    scores = {name: evaluate(name) for name in configurations}
    ordered = list(configurations)
    best = max(ordered, key=lambda name: scores[name])
    best_score = scores[best]

    sensitivity: float | None = None
    if len(scores) >= 2:
        values = [scores[name] for name in ordered]
        average = mean(values)
        if average != 0.0:
            sensitivity = math.sqrt(sample_variance(values)) / abs(average)

    neighbour_drop: float | None = None
    position = ordered.index(best)
    neighbours = [
        scores[ordered[index]]
        for index in (position - 1, position + 1)
        if 0 <= index < len(ordered)
    ]
    if neighbours and best_score != 0.0:
        neighbour_drop = (best_score - max(neighbours)) / abs(best_score)

    return SweepResult(
        metric=metric,
        scores=MappingProxyType(dict(scores)),
        best=best,
        best_score=best_score,
        sensitivity=sensitivity,
        neighbour_drop=neighbour_drop,
    )


def sample_degradation(in_sample: float, out_of_sample: float) -> float | None:
    """How much of an in-sample result failed to survive out of sample.

    ``(in_sample - out_of_sample) / |in_sample|``: ``0.0`` means the result
    held, ``1.0`` means all of it was lost, above ``1.0`` means the sign
    flipped. ``None`` when the in-sample result is zero, because a fraction of
    nothing is undefined and any stand-in would read as a measured degradation.
    """

    if in_sample == 0.0:
        return None
    return (in_sample - out_of_sample) / abs(in_sample)


def _dispersion(values: Mapping[str, float], what: str) -> tuple[float | None, str, str]:
    """Coefficient of variation across a set of slices, plus the extremes."""

    if len(values) < 2:
        raise ResearchValidationError(
            f"{what} stability needs at least 2 slices to compare, got {len(values)}."
        )
    names = sorted(values)
    series = [values[name] for name in names]
    average = mean(series)
    spread = None if average == 0.0 else math.sqrt(sample_variance(series)) / abs(average)
    return (
        spread,
        min(names, key=lambda name: values[name]),
        max(names, key=lambda name: values[name]),
    )


@dataclass(frozen=True, slots=True)
class StabilityReport:
    """How much a metric moved across slices of the sample.

    Attributes:
        dimension: What the sample was sliced by -- ``"period"`` or
            ``"symbol"``.
        metric: What was measured in each slice.
        by_slice: Slice name to its score.
        coefficient_of_variation: Standard deviation over absolute mean, or
            ``None`` when the mean is zero.
        weakest: The slice with the lowest score.
        strongest: The slice with the highest score.
        positive_share: Fraction of slices whose score exceeds zero. The
            simplest statement of "does this work everywhere or only
            somewhere?", and it needs no threshold to read.
    """

    dimension: str
    metric: str
    by_slice: Mapping[str, float]
    coefficient_of_variation: float | None
    weakest: str
    strongest: str
    positive_share: float


def period_stability(metric: str, by_period: Mapping[str, float]) -> StabilityReport:
    """How stable a metric is across labelled periods of the sample.

    ``by_period`` maps a period label -- a fold index, a year, a regime -- to
    the score measured in it. The labels are the caller's; AlphaLab does not
    decide what a period is.

    Raises:
        ResearchValidationError: If fewer than two periods are supplied.
    """

    spread, weakest, strongest = _dispersion(by_period, "Period")
    return StabilityReport(
        dimension="period",
        metric=metric,
        by_slice=MappingProxyType(dict(by_period)),
        coefficient_of_variation=spread,
        weakest=weakest,
        strongest=strongest,
        positive_share=sum(1 for value in by_period.values() if value > 0.0) / len(by_period),
    )


def symbol_stability(metric: str, by_symbol: Mapping[str, float]) -> StabilityReport:
    """How stable a metric is across the assets it was measured on.

    The companion to :func:`period_stability`, and the one that catches a
    result driven by a single name. A factor whose whole information
    coefficient comes from one symbol is not a factor, and an aggregate figure
    cannot show that.

    Raises:
        ResearchValidationError: If fewer than two symbols are supplied.
    """

    spread, weakest, strongest = _dispersion(by_symbol, "Symbol")
    return StabilityReport(
        dimension="symbol",
        metric=metric,
        by_slice=MappingProxyType(dict(by_symbol)),
        coefficient_of_variation=spread,
        weakest=weakest,
        strongest=strongest,
        positive_share=sum(1 for value in by_symbol.values() if value > 0.0) / len(by_symbol),
    )


@dataclass(frozen=True, slots=True)
class OverfittingPolicy:
    """The bounds a caller states in advance. AlphaLab ships no default.

    Attributes:
        maximum_degradation: The largest acceptable
            :func:`sample_degradation`. ``0.5`` means "losing more than half
            the in-sample result out of sample is a finding".
        maximum_sensitivity: The largest acceptable coefficient of variation
            across a parameter sweep.
        maximum_neighbour_drop: The largest acceptable fall from the best
            configuration to its neighbour.
        minimum_positive_share: The smallest acceptable share of periods or
            symbols with a positive score.
        alpha: Nominal significance before correction, used to report
            :attr:`OverfittingReport.bonferroni_alpha`.

    Raises:
        ResearchValidationError: If every bound is ``None`` -- a policy that
            checks nothing would make every report read as a pass, the same
            reasoning ``ValidationPolicy`` applies in
            :mod:`alphalab.lifecycle.evidence` -- or if ``alpha`` is outside
            ``(0, 1)``.
    """

    maximum_degradation: float | None = None
    maximum_sensitivity: float | None = None
    maximum_neighbour_drop: float | None = None
    minimum_positive_share: float | None = None
    alpha: float = 0.05

    def __post_init__(self) -> None:
        bounds = (
            self.maximum_degradation,
            self.maximum_sensitivity,
            self.maximum_neighbour_drop,
            self.minimum_positive_share,
        )
        if all(bound is None for bound in bounds):
            raise ResearchValidationError(
                "An OverfittingPolicy that states no bound checks nothing, so every report "
                "produced under it would read as a pass."
            )
        if not 0.0 < self.alpha < 1.0:
            raise ResearchValidationError(
                f"alpha must lie strictly between 0 and 1, got {self.alpha!r}."
            )


@dataclass(frozen=True, slots=True)
class OverfittingReport:
    """What was measured, what the bounds were, and which ones were crossed.

    Attributes:
        metric: The quantity every measurement here refers to.
        trials: Configurations evaluated in the search that produced the
            result. One means no search was performed.
        in_sample: The metric measured on the data the configuration was
            chosen on.
        out_of_sample: The same metric on data it was not.
        degradation: :func:`sample_degradation` of the two, or ``None``.
        sensitivity: The sweep's coefficient of variation, or ``None``.
        neighbour_drop: The sweep's cliff measure, or ``None``.
        period: Stability across periods, or ``None`` if not measured.
        symbol: Stability across symbols, or ``None`` if not measured.
        effective_parameters: How many free parameters the configuration had.
            Supplied by the caller: AlphaLab cannot count the parameters of a
            model it does not hold, and guessing would be worse than asking.
        policy: The bounds these measurements were read against.
        findings: One sentence per crossed bound, naming the value and the
            bound. Empty when nothing was crossed -- which is not a statement
            that the result is sound, only that these checks did not object.
    """

    metric: str
    trials: int
    in_sample: float
    out_of_sample: float
    degradation: float | None
    sensitivity: float | None
    neighbour_drop: float | None
    effective_parameters: int
    policy: OverfittingPolicy
    period: StabilityReport | None = None
    symbol: StabilityReport | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def bonferroni_alpha(self) -> float:
        """``alpha / trials``: the corrected threshold for this much searching.

        Conservative when trials are correlated, which a sweep over
        neighbouring parameter values makes them. Reported as a threshold to
        compare against, never applied to anything here -- this module computes
        no p-values, so it has none to correct.
        """

        return self.policy.alpha / self.trials

    @property
    def observations_per_parameter(self) -> float | None:
        """A crude complexity reading: sample size over free parameters.

        ``None`` when ``effective_parameters`` is zero, which is the honest
        answer for a rule with nothing to fit. Deliberately not compared
        against any rule of thumb: the thresholds that circulate for this ratio
        assume independent observations, and financial series are not.
        """

        if self.effective_parameters == 0:
            return None
        return float(self.trials) / self.effective_parameters

    def describe(self) -> str:
        """The measurements on one line, without any verdict attached."""

        degradation = "-" if self.degradation is None else f"{self.degradation:+.2%}"
        sensitivity = "-" if self.sensitivity is None else f"{self.sensitivity:.4f}"
        return (
            f"{self.metric}: in={self.in_sample:+.4f} out={self.out_of_sample:+.4f} "
            f"degradation={degradation} sensitivity={sensitivity} trials={self.trials} "
            f"findings={len(self.findings)}"
        )


def build_overfitting_report(
    metric: str,
    in_sample: float,
    out_of_sample: float,
    policy: OverfittingPolicy,
    effective_parameters: int,
    sweep: SweepResult | None = None,
    period: StabilityReport | None = None,
    symbol: StabilityReport | None = None,
) -> OverfittingReport:
    """Assemble the measurements and check them against the stated bounds.

    Each bound is checked only if the corresponding measurement exists. A bound
    stated for a measurement that was not taken produces a finding saying so,
    rather than silently passing -- an absent number is not a passing one, the
    same rule :func:`~alphalab.lifecycle.evidence.evaluate_policy` applies.

    Raises:
        ResearchValidationError: If ``effective_parameters`` is negative, or if
            the sweep reports a different metric from the one named here.
    """

    if effective_parameters < 0:
        raise ResearchValidationError(
            f"effective_parameters must not be negative, got {effective_parameters}."
        )
    if sweep is not None and sweep.metric != metric:
        raise ResearchValidationError(
            f"The report is about {metric!r} and the sweep measured {sweep.metric!r}."
        )

    degradation = sample_degradation(in_sample, out_of_sample)
    sensitivity = None if sweep is None else sweep.sensitivity
    neighbour_drop = None if sweep is None else sweep.neighbour_drop
    findings: list[str] = []

    def check(bound: float | None, value: float | None, name: str, above: bool) -> None:
        if bound is None:
            return
        if value is None:
            findings.append(
                f"the policy bounds {name} at {bound!r} and it was not measured; an absent "
                "measurement is not a passing one."
            )
            return
        if (above and value > bound) or (not above and value < bound):
            direction = "above" if above else "below"
            findings.append(f"{name} is {value!r}, {direction} the stated bound {bound!r}.")

    check(policy.maximum_degradation, degradation, "out-of-sample degradation", True)
    check(policy.maximum_sensitivity, sensitivity, "parameter sensitivity", True)
    check(policy.maximum_neighbour_drop, neighbour_drop, "neighbour drop", True)
    for report in (period, symbol):
        if report is not None:
            check(
                policy.minimum_positive_share,
                report.positive_share,
                f"{report.dimension} positive share",
                False,
            )
    if policy.minimum_positive_share is not None and period is None and symbol is None:
        findings.append(
            f"the policy bounds positive share at {policy.minimum_positive_share!r} and "
            "neither period nor symbol stability was measured."
        )

    return OverfittingReport(
        metric=metric,
        trials=1 if sweep is None else sweep.trials,
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        degradation=degradation,
        sensitivity=sensitivity,
        neighbour_drop=neighbour_drop,
        effective_parameters=effective_parameters,
        policy=policy,
        period=period,
        symbol=symbol,
        findings=tuple(findings),
    )
