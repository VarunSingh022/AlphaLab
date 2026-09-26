"""Regime detection: declared rules, a reconstructable state, and what each regime held.

Which state the world is in -- bull or bear, calm or turbulent, risk-on or
risk-off, growing or contracting -- is a research question, and v3.2 declined to
answer it: :func:`~alphalab.research.signals.conditional_diagnostics` takes
regime labels the caller supplies, and AlphaLab defines no taxonomy. v3.7 keeps
that position and supplies the *machinery* for a caller's own answer, kept apart
into the four things it is:

========================= ==================================================
Definition                :class:`RegimeDefinition` -- which rule, which
                          labels, how much persistence; a derived identity
Classification            :func:`classify_regimes` -- one label per
                          observation, from the declared rule
Transition                :class:`RegimeTransition` -- when the confirmed
                          regime changed, from what to what
Conditioned analysis      :func:`regime_profile`, and
                          :meth:`RegimeSeries.labels_by_instant` for the
                          v3.2 conditional diagnostics
========================= ==================================================

Labels are the caller's words. ``"bull"``, ``"high_volatility"`` and
``"risk_off"`` appear in the examples because they are common, not because
anything here knows them.

Rules are data, so they can be identified
-----------------------------------------

A rule is a declared value -- thresholds on a signal, cut points on a trailing
percentile rank, a table combining several -- rather than a function, because a
function has no identity that survives a process boundary and a definition's
identity must. :class:`ThresholdRule` classifies a value against fixed cut
points. :class:`TrailingQuantileRule` classifies it by its rank within its own
trailing window, which adapts to the level of the series *using only its past*:
thresholds fitted over the whole sample would classify January with December's
data, which is the look-ahead a statistical regime model most easily hides.
:class:`CompositeRule` combines components through a table that must name every
combination of their labels -- there is no default regime, because a default is
a classification nobody made.

Persistence, and a state that can be reconstructed
--------------------------------------------------

A regime that flips on every observation near a threshold is noise.
:attr:`RegimeDefinition.persistence` states how many consecutive observations a
new label must hold before the confirmed regime switches, and it is part of the
identity. That makes the detector stateful, and the state is explicit: a
:class:`RegimeState` records the confirmed regime, the candidate and how long it
has held, and the trailing windows the quantile rules read. Classifying a series
in one pass and classifying it in two -- the second resumed from the first's
final state -- produce the same labels, the same transitions and the same final
state, which is the property "reconstructable" has to mean.

Point in time
-------------

A label at an instant reads the signal values at and before that instant and
nothing later: every rule is either instantaneous or trailing, and truncating a
series leaves every surviving label unchanged. The signals themselves must be
point-in-time -- a trailing feature, or a
:class:`~alphalab.factor_library.knowledge.KnowledgeFrame` series -- and a
series' :attr:`RegimeSeries.input_id` names where they came from.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise, product
from typing import Final

from alphalab.common.statistics import mean, standard_deviation
from alphalab.factor_library.series import FeatureSeries
from alphalab.research.exceptions import ResearchValidationError

__all__ = [
    "REGIME_DEFINITION_SCHEME",
    "REGIME_SERIES_SCHEME",
    "CompositeRule",
    "RegimeCell",
    "RegimeDefinition",
    "RegimeProfile",
    "RegimeRule",
    "RegimeSeries",
    "RegimeState",
    "RegimeStatistics",
    "RegimeTransition",
    "ThresholdRule",
    "TrailingQuantileRule",
    "TransitionFrequency",
    "canonical_regime_key",
    "classify_regimes",
    "initial_regime_state",
    "regime_profile",
    "regime_series_from_features",
]

#: Scheme tags, and the first line of each canonical key.
REGIME_DEFINITION_SCHEME: Final = "alphalab.regime_definition.v1"
REGIME_SERIES_SCHEME: Final = "alphalab.regime_series.v1"

_SIGNAL = "a signal name: letters, digits and underscores, starting with a letter"


def _require_signal(signal: str) -> None:
    if (
        not signal
        or not signal[0].isalpha()
        or not all(character.isalnum() or character == "_" for character in signal)
    ):
        raise ResearchValidationError(f"Signal {signal!r} is not {_SIGNAL}.")


def _require_labels(labels: Sequence[str], expected: int, what: str) -> None:
    if len(labels) != expected:
        raise ResearchValidationError(
            f"{what} has {len(labels)} label(s) for {expected} interval(s); every interval "
            "between the cut points needs exactly one."
        )
    for label in labels:
        if not label.strip() or label != label.strip() or "\n" in label:
            raise ResearchValidationError(
                f"{what} label {label!r} is blank, padded, or spans a line."
            )


def _require_cutoffs(cutoffs: Sequence[float], what: str, unit_interval: bool) -> None:
    if not cutoffs:
        raise ResearchValidationError(f"{what} needs at least one cut point.")
    for cutoff in cutoffs:
        if isinstance(cutoff, bool) or not isinstance(cutoff, int | float):
            raise ResearchValidationError(f"{what} cut point {cutoff!r} is not a number.")
        if not math.isfinite(cutoff):
            raise ResearchValidationError(f"{what} cut point {cutoff!r} is not finite.")
        if unit_interval and not 0.0 < cutoff < 1.0:
            raise ResearchValidationError(
                f"{what} cut point {cutoff!r} is a percentile rank and must lie strictly "
                "between 0 and 1."
            )
    if any(later <= earlier for earlier, later in pairwise(cutoffs)):
        raise ResearchValidationError(f"{what} cut points {list(cutoffs)} are not increasing.")


@dataclass(frozen=True, slots=True)
class ThresholdRule:
    """Classify a signal's value against fixed cut points.

    Intervals are closed below: with cut points ``(c0, c1)`` the labels cover
    ``(-inf, c0)``, ``[c0, c1)`` and ``[c1, inf)``. A value exactly at a cut
    point belongs to the interval above it.

    Attributes:
        signal: The signal the rule reads.
        cutoffs: Strictly increasing, finite cut points.
        labels: One label per interval, ``len(cutoffs) + 1`` of them.
    """

    signal: str
    cutoffs: tuple[float, ...]
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_signal(self.signal)
        _require_cutoffs(self.cutoffs, f"ThresholdRule({self.signal})", unit_interval=False)
        _require_labels(self.labels, len(self.cutoffs) + 1, f"ThresholdRule({self.signal})")

    @property
    def lookback(self) -> int:
        """How many observations the rule reads: the current one."""

        return 1

    def classify(self, window: Sequence[float]) -> str:
        """The label of the latest value in ``window``."""

        return self.labels[bisect_right(self.cutoffs, window[-1])]


@dataclass(frozen=True, slots=True)
class TrailingQuantileRule:
    """Classify a signal by where its value ranks within its own trailing window.

    The rank is the fraction of the ``lookback`` most recent values -- the
    current one included -- that are at or below the current value, a number in
    ``(0, 1]``. It is classified against ``cutoffs`` closed below, like
    :class:`ThresholdRule`. The first ``lookback - 1`` observations have no full
    window and no label.

    Attributes:
        signal: The signal the rule reads.
        lookback: How many observations the window holds, current included.
        cutoffs: Strictly increasing percentile ranks in ``(0, 1)``.
        labels: One label per interval, ``len(cutoffs) + 1`` of them.
    """

    signal: str
    lookback: int
    cutoffs: tuple[float, ...]
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_signal(self.signal)
        if isinstance(self.lookback, bool) or not isinstance(self.lookback, int):
            raise ResearchValidationError(f"lookback {self.lookback!r} is not a count.")
        if self.lookback < 2:
            raise ResearchValidationError(
                f"A trailing quantile needs a window of at least 2 observations; got "
                f"{self.lookback}. A rank within a window of one is always 1."
            )
        what = f"TrailingQuantileRule({self.signal})"
        _require_cutoffs(self.cutoffs, what, unit_interval=True)
        _require_labels(self.labels, len(self.cutoffs) + 1, what)

    def classify(self, window: Sequence[float]) -> str:
        """The label of the latest value's rank within the last ``lookback`` values."""

        recent = window[-self.lookback :]
        current = recent[-1]
        rank = sum(1 for value in recent if value <= current) / len(recent)
        return self.labels[bisect_right(self.cutoffs, rank)]


type ComponentRule = ThresholdRule | TrailingQuantileRule


@dataclass(frozen=True, slots=True)
class RegimeCell:
    """One row of a composite rule's table: component labels to a regime.

    Attributes:
        components: One label per component, in component order.
        regime: The regime that combination is.
    """

    components: tuple[str, ...]
    regime: str


@dataclass(frozen=True, slots=True)
class CompositeRule:
    """Combine several component rules through a table naming every combination.

    Growth crossed with inflation, trend crossed with volatility: each
    component labels its own signal, and :attr:`cells` says which regime each
    combination of labels is. The table must be total -- every combination of
    the components' labels named exactly once -- because an unnamed
    combination would need a default regime, and a default is a classification
    nobody made.

    Build one with :meth:`of` from a mapping, which orders the cells.

    Attributes:
        components: The component rules, at least two.
        cells: The table, ordered by components.
    """

    components: tuple[ComponentRule, ...]
    cells: tuple[RegimeCell, ...]

    def __post_init__(self) -> None:
        if len(self.components) < 2:
            raise ResearchValidationError(
                "A composite rule combines at least two components; one component is a rule "
                "of its own."
            )
        expected = set(product(*(sorted(set(component.labels)) for component in self.components)))
        named = [cell.components for cell in self.cells]
        if len(set(named)) != len(named):
            raise ResearchValidationError("The composite table names a combination twice.")
        missing = sorted(expected - set(named))
        extra = sorted(set(named) - expected)
        if missing or extra:
            raise ResearchValidationError(
                f"The composite table must name every combination of its components' labels "
                f"exactly once; missing {missing[:4]}, not a combination {extra[:4]}."
            )
        for cell in self.cells:
            _require_labels((cell.regime,), 1, "A composite cell")

    @classmethod
    def of(
        cls, components: Sequence[ComponentRule], table: Mapping[tuple[str, ...], str]
    ) -> CompositeRule:
        """A composite rule from a mapping, with its cells in canonical order."""

        return cls(
            components=tuple(components),
            cells=tuple(RegimeCell(key, table[key]) for key in sorted(table)),
        )

    def regime_of(self, labels: tuple[str, ...]) -> str:
        """The regime a combination of component labels is."""

        for cell in self.cells:
            if cell.components == labels:
                return cell.regime
        raise ResearchValidationError(f"The composite table does not name {labels}.")


type RegimeRule = ThresholdRule | TrailingQuantileRule | CompositeRule


def _components(rule: RegimeRule) -> tuple[ComponentRule, ...]:
    return rule.components if isinstance(rule, CompositeRule) else (rule,)


def _render(rule: ComponentRule) -> str:
    if isinstance(rule, ThresholdRule):
        return f"threshold(signal={rule.signal!r},cutoffs={rule.cutoffs!r},labels={rule.labels!r})"
    return (
        f"trailing_quantile(signal={rule.signal!r},lookback={rule.lookback!r},"
        f"cutoffs={rule.cutoffs!r},labels={rule.labels!r})"
    )


@dataclass(frozen=True, slots=True)
class RegimeDefinition:
    """One regime model, stated completely.

    Attributes:
        name: What the model is called. Part of its identity.
        rule: How an observation is classified.
        persistence: How many consecutive observations a new label must hold
            before the confirmed regime switches to it; ``1`` switches at once.
            No default: how much evidence a switch needs is the researcher's
            decision.

    Raises:
        ResearchValidationError: If the name is blank or contains ``"@"``, or
            persistence is not a positive count.
    """

    name: str
    rule: RegimeRule
    persistence: int

    def __post_init__(self) -> None:
        if not self.name.strip() or "@" in self.name:
            raise ResearchValidationError(f"A regime model is named, without '@': {self.name!r}.")
        if isinstance(self.persistence, bool) or not isinstance(self.persistence, int):
            raise ResearchValidationError(f"persistence {self.persistence!r} is not a count.")
        if self.persistence < 1:
            raise ResearchValidationError(
                f"persistence is {self.persistence}; a label must hold for at least one "
                "observation to be a regime."
            )

    @property
    def definition_id(self) -> str:
        """This model's derived, reproducible identity."""

        digest = hashlib.sha256(canonical_regime_key(self).encode("utf-8")).hexdigest()
        return f"{self.name}@{digest}"

    @property
    def signals(self) -> tuple[str, ...]:
        """Every signal the rule reads, sorted."""

        return tuple(sorted({component.signal for component in _components(self.rule)}))

    @property
    def labels(self) -> tuple[str, ...]:
        """Every regime the rule can produce, sorted."""

        if isinstance(self.rule, CompositeRule):
            return tuple(sorted({cell.regime for cell in self.rule.cells}))
        return tuple(sorted(set(self.rule.labels)))

    @property
    def warmup(self) -> int:
        """Leading observations with no classification: the longest window, less one."""

        return max(component.lookback for component in _components(self.rule)) - 1

    def windows_needed(self) -> dict[str, int]:
        """Each signal to the longest window any component reads it over."""

        needed: dict[str, int] = {}
        for component in _components(self.rule):
            needed[component.signal] = max(needed.get(component.signal, 1), component.lookback)
        return needed


def canonical_regime_key(definition: RegimeDefinition) -> str:
    """Render the canonical key a regime model's identity is derived from."""

    rule = definition.rule
    if isinstance(rule, CompositeRule):
        body = [
            "composite",
            *(f"component[{index}]={_render(part)}" for index, part in enumerate(rule.components)),
            *(f"cell={cell.components!r}->{cell.regime!r}" for cell in rule.cells),
        ]
    else:
        body = [_render(rule)]
    return "\n".join(
        [
            REGIME_DEFINITION_SCHEME,
            f"name={definition.name!r}",
            f"persistence={definition.persistence!r}",
            *body,
        ]
    )


@dataclass(frozen=True, slots=True)
class RegimeState:
    """Everything the detector carries from one observation to the next.

    Attributes:
        definition_id: The model this state belongs to.
        observations: How many observations have been classified.
        last_timestamp: The last one's instant, or ``None`` before any.
        regime: The confirmed regime, or ``None`` before the first is confirmed.
        candidate: A label waiting for confirmation, or ``None``.
        candidate_count: How many consecutive observations it has held.
        windows: Each windowed signal to its most recent values, oldest first,
            no longer than the longest window over it.
    """

    definition_id: str
    observations: int
    last_timestamp: float | None
    regime: str | None
    candidate: str | None
    candidate_count: int
    windows: tuple[tuple[str, tuple[float, ...]], ...]

    @property
    def state_id(self) -> str:
        """The derived identity of this exact state."""

        lines = [
            f"{REGIME_SERIES_SCHEME}.state",
            f"definition={self.definition_id!r}",
            f"observations={self.observations!r}",
            f"last_timestamp={self.last_timestamp!r}",
            f"regime={self.regime!r}",
            f"candidate={self.candidate!r}",
            f"candidate_count={self.candidate_count!r}",
            *(f"window.{signal!r}={values!r}" for signal, values in self.windows),
        ]
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def initial_regime_state(definition: RegimeDefinition) -> RegimeState:
    """The state a model starts from before its first observation."""

    return RegimeState(
        definition_id=definition.definition_id,
        observations=0,
        last_timestamp=None,
        regime=None,
        candidate=None,
        candidate_count=0,
        windows=tuple((signal, ()) for signal in sorted(definition.windows_needed())),
    )


@dataclass(frozen=True, slots=True)
class RegimeTransition:
    """One change of confirmed regime.

    Attributes:
        at: The instant of the observation that confirmed it.
        index: That observation's position in the model's whole history.
        previous: The regime before, or ``None`` for the first confirmation.
        current: The regime after.
    """

    at: float
    index: int
    previous: str | None
    current: str


@dataclass(frozen=True, slots=True)
class RegimeSeries:
    """A model applied to a signal history.

    Attributes:
        definition: The model.
        input_id: Where the signals came from -- a feature lineage, a
            knowledge frame -- or ``None`` when untraceable.
        initial_state_id: The state classification started from.
        timestamps: The observations classified.
        raw_labels: Each observation's classification before persistence,
            ``None`` during warmup.
        regimes: The confirmed regime at each observation, ``None`` before the
            first confirmation.
        transitions: Every change of confirmed regime.
        final_state: The state after the last observation, from which a later
            series resumes.
    """

    definition: RegimeDefinition
    input_id: str | None
    initial_state_id: str
    timestamps: tuple[float, ...]
    raw_labels: tuple[str | None, ...]
    regimes: tuple[str | None, ...]
    transitions: tuple[RegimeTransition, ...]
    final_state: RegimeState

    @property
    def series_id(self) -> str:
        """The derived identity of the classification: model, inputs, start and result."""

        lines = [
            REGIME_SERIES_SCHEME,
            f"definition={self.definition.definition_id!r}",
            f"input={self.input_id!r}",
            f"initial_state={self.initial_state_id!r}",
            f"timestamps={self.timestamps!r}",
            f"regimes={self.regimes!r}",
        ]
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    def labels_by_instant(self) -> dict[float, str]:
        """Confirmed regime by instant, unconfirmed instants omitted.

        The shape :func:`~alphalab.research.signals.conditional_diagnostics`
        takes, so a detected regime conditions a signal diagnostic directly.
        """

        return {
            stamp: regime
            for stamp, regime in zip(self.timestamps, self.regimes, strict=True)
            if regime is not None
        }


def _aligned(
    definition: RegimeDefinition, signals: Mapping[str, Sequence[tuple[float, float]]]
) -> tuple[tuple[float, ...], dict[str, tuple[float, ...]]]:
    wanted = set(definition.signals)
    given = set(signals)
    if given != wanted:
        raise ResearchValidationError(
            f"Model {definition.name!r} reads {sorted(wanted)} and was given {sorted(given)}. "
            "A missing signal cannot be classified, and an unread one would still be named "
            "as an input."
        )
    ordered = sorted(signals)
    stamps = tuple(stamp for stamp, _ in signals[ordered[0]])
    values: dict[str, tuple[float, ...]] = {}
    for signal in ordered:
        rows = signals[signal]
        these = tuple(stamp for stamp, _ in rows)
        if these != stamps:
            raise ResearchValidationError(
                f"Signal {signal!r} is observed at different instants from the others. Align "
                "the signals on one clock before classifying; pairing values across two clocks "
                "would classify one instant with another's data."
            )
        observed = tuple(value for _, value in rows)
        for stamp, value in rows:
            if not (math.isfinite(stamp) and math.isfinite(value)):
                raise ResearchValidationError(
                    f"Signal {signal!r} holds a non-finite instant or value at {stamp!r}."
                )
        values[signal] = observed
    if not stamps:
        raise ResearchValidationError("There are no observations to classify.")
    if any(later <= earlier for earlier, later in pairwise(stamps)):
        raise ResearchValidationError("The signals' instants must be strictly increasing.")
    return stamps, values


def classify_regimes(
    definition: RegimeDefinition,
    signals: Mapping[str, Sequence[tuple[float, float]]],
    *,
    input_id: str | None,
    initial: RegimeState | None = None,
) -> RegimeSeries:
    """Classify each observation, then confirm regimes under the persistence rule.

    Args:
        definition: The model.
        signals: Each signal the model reads to its ``(instant, value)``
            history. Every signal must be observed at the same instants.
        input_id: Where the signals came from, or ``None`` when untraceable.
            Carried into :attr:`RegimeSeries.series_id`.
        initial: A state to resume from -- a previous series' ``final_state``
            -- or ``None`` to start fresh.

    Raises:
        ResearchValidationError: If the signals do not match the model's, are
            misaligned, unordered or non-finite; or if ``initial`` belongs to
            another model or is not before the first observation.
    """

    stamps, values = _aligned(definition, signals)
    state = initial_regime_state(definition) if initial is None else initial
    if state.definition_id != definition.definition_id:
        raise ResearchValidationError(
            f"The state belongs to {state.definition_id!r}, not to {definition.definition_id!r}."
        )
    if state.last_timestamp is not None and stamps[0] <= state.last_timestamp:
        raise ResearchValidationError(
            f"The first observation ({stamps[0]!r}) is not after the state's last "
            f"({state.last_timestamp!r}). A state resumes forwards in time only."
        )

    needed = definition.windows_needed()
    windows = {signal: list(history) for signal, history in state.windows}
    regime, candidate, count = state.regime, state.candidate, state.candidate_count
    observations = state.observations
    components = _components(definition.rule)
    raw_labels: list[str | None] = []
    regimes: list[str | None] = []
    transitions: list[RegimeTransition] = []

    for position, stamp in enumerate(stamps):
        for signal, window in windows.items():
            window.append(values[signal][position])
            del window[: max(0, len(window) - needed[signal])]
        if any(len(windows[part.signal]) < part.lookback for part in components):
            raw: str | None = None
        else:
            labels = tuple(part.classify(windows[part.signal]) for part in components)
            rule = definition.rule
            raw = rule.regime_of(labels) if isinstance(rule, CompositeRule) else labels[0]

        if raw is not None:
            if raw == regime:
                candidate, count = None, 0
            else:
                if raw == candidate:
                    count += 1
                else:
                    candidate, count = raw, 1
                if count >= definition.persistence:
                    transitions.append(RegimeTransition(stamp, observations, regime, raw))
                    regime, candidate, count = raw, None, 0
        raw_labels.append(raw)
        regimes.append(regime)
        observations += 1

    final = RegimeState(
        definition_id=definition.definition_id,
        observations=observations,
        last_timestamp=stamps[-1],
        regime=regime,
        candidate=candidate,
        candidate_count=count,
        windows=tuple((signal, tuple(windows[signal])) for signal in sorted(windows)),
    )
    return RegimeSeries(
        definition=definition,
        input_id=input_id,
        initial_state_id=state.state_id,
        timestamps=stamps,
        raw_labels=tuple(raw_labels),
        regimes=tuple(regimes),
        transitions=tuple(transitions),
        final_state=final,
    )


def regime_series_from_features(
    definition: RegimeDefinition,
    features: Mapping[str, FeatureSeries],
    *,
    initial: RegimeState | None = None,
) -> RegimeSeries:
    """Classify computed features, carrying their lineage as the series' input.

    Each signal the model reads is one :class:`FeatureSeries` -- a volatility
    regime ratio, a trend, a knowledge-frame series turned feature -- and the
    input identity is derived from their lineage ids, or ``None`` if any is
    untraceable.

    Raises:
        ResearchValidationError: For every reason :func:`classify_regimes` does.
    """

    lineage: list[str] = []
    for signal in sorted(features):
        identity = features[signal].lineage_id
        if identity is None:
            lineage = []
            break
        lineage.append(f"{signal!r}={identity!r}")
    input_id = (
        hashlib.sha256("\n".join([f"{REGIME_SERIES_SCHEME}.input", *lineage]).encode()).hexdigest()
        if lineage
        else None
    )
    signals = {
        signal: tuple(zip(series.timestamps, series.values, strict=True))
        for signal, series in features.items()
    }
    return classify_regimes(definition, signals, input_id=input_id, initial=initial)


@dataclass(frozen=True, slots=True)
class RegimeStatistics:
    """What one regime held.

    Attributes:
        label: The regime.
        observations: Observations confirmed in it.
        share: Its share of confirmed observations.
        spells: Separate runs of it.
        mean_duration: Mean observations per spell.
        mean_return: Mean of the supplied returns at its observations, or
            ``None`` with none.
        return_std: Their sample standard deviation, or ``None`` with fewer
            than two.
        return_observations: How many returns the two figures rest on.
    """

    label: str
    observations: int
    share: float
    spells: int
    mean_duration: float
    mean_return: float | None
    return_std: float | None
    return_observations: int


@dataclass(frozen=True, slots=True)
class TransitionFrequency:
    """How often one confirmed regime was followed by another.

    Attributes:
        previous: The regime at one observation.
        current: The regime at the next.
        count: How many consecutive confirmed pairs went from one to the other,
            staying in place included.
        probability: ``count`` over every pair leaving ``previous`` -- the
            empirical probability of the next regime given the current one.
    """

    previous: str
    current: str
    count: int
    probability: float


@dataclass(frozen=True, slots=True)
class RegimeProfile:
    """What each regime in a series held, and how regimes followed one another.

    Attributes:
        series_id: The series profiled.
        classified: Observations with a confirmed regime.
        unclassified: Observations without one -- warmup, and before the first
            confirmation.
        statistics: One entry per regime that occurred, sorted by label.
        transitions: Every observed ``previous -> current`` pair, sorted.
    """

    series_id: str
    classified: int
    unclassified: int
    statistics: tuple[RegimeStatistics, ...]
    transitions: tuple[TransitionFrequency, ...]


def regime_profile(series: RegimeSeries, returns: Mapping[float, float] | None) -> RegimeProfile:
    """Profile a regime series, optionally against returns keyed by instant.

    ``returns`` are the caller's -- forward returns for a predictive question,
    same-period returns for a descriptive one -- and are read only at
    confirmed instants. A regime at an instant uses information at and before
    it, so conditioning a *forward* return on it looks ahead in the return, as
    every forward-return study must, and never in the regime.

    Raises:
        ResearchValidationError: If no observation has a confirmed regime.
    """

    confirmed = [
        (stamp, regime)
        for stamp, regime in zip(series.timestamps, series.regimes, strict=True)
        if regime is not None
    ]
    if not confirmed:
        raise ResearchValidationError(
            f"Series {series.series_id} confirmed no regime at any of its "
            f"{len(series.timestamps)} observation(s)."
        )
    total = len(confirmed)
    by_label: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    spells: dict[str, int] = {}
    previous: str | None = None
    for stamp, regime in zip(series.timestamps, series.regimes, strict=True):
        if regime is None:
            previous = None
            continue
        counts[regime] = counts.get(regime, 0) + 1
        if regime != previous:
            spells[regime] = spells.get(regime, 0) + 1
        previous = regime
        if returns is not None and stamp in returns:
            by_label.setdefault(regime, []).append(returns[stamp])

    statistics = []
    for label in sorted(counts):
        sample = by_label.get(label, [])
        statistics.append(
            RegimeStatistics(
                label=label,
                observations=counts[label],
                share=counts[label] / total,
                spells=spells[label],
                mean_duration=counts[label] / spells[label],
                mean_return=mean(sample) if sample else None,
                return_std=standard_deviation(sample) if len(sample) >= 2 else None,
                return_observations=len(sample),
            )
        )

    pairs: dict[tuple[str, str], int] = {}
    for earlier, later in pairwise(series.regimes):
        if earlier is not None and later is not None:
            pairs[(earlier, later)] = pairs.get((earlier, later), 0) + 1
    leaving: dict[str, int] = {}
    for (source, _), number in pairs.items():
        leaving[source] = leaving.get(source, 0) + number
    transitions = tuple(
        TransitionFrequency(source, target, number, number / leaving[source])
        for (source, target), number in sorted(pairs.items())
    )
    return RegimeProfile(
        series_id=series.series_id,
        classified=total,
        unclassified=len(series.timestamps) - total,
        statistics=tuple(statistics),
        transitions=transitions,
    )
