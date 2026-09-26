"""Knowledge frames: what was knowable about each subject, sampled on a research clock.

A feature is computed over an
:class:`~alphalab.factor_library.observations.ObservationFrame` -- one value per
subject per instant -- and price data arrives in that shape. External
information does not. A quarterly earnings figure, a monthly inflation print and
a daily sentiment score each describe their own periods and become knowable on
their own schedules, and the question a feature asks of them at a bar is not
"what was the value at this instant?" -- there was none -- but "what was the most
recent value anyone could have known at this instant?".

A :class:`KnowledgeFrame` answers exactly that, subject by subject and instant by
instant, and hands the answer to the feature engine in the shape it already
reads. Ranking a valuation ratio across a universe, standardizing a sentiment
score, measuring a macro series' information coefficient: all of it is the v3.2
machinery unchanged, run over what was knowable rather than over what was
eventually true.

This is not a forward fill
--------------------------

ADR-0037 refuses to fill a missing *price*, because a forward fill claims that
yesterday's value was still true today. A knowledge frame makes no claim about
the world between two publications. It states a fact about the *information
set*: at this instant, this was the latest figure known. That fact is true until
the next figure is published, which is why the value repeats -- and a figure
that is too old to be the answer a researcher would accept is refused by a
staleness bound the caller states, measured from the instant the figure
*describes*. ``max_age_seconds=None`` is allowed and is a statement ("any age"),
never a default.

The clock, and joining two datasets honestly
--------------------------------------------

A frame is sampled on a :class:`ResearchClock` -- usually the timestamps of the
price frame the result will be measured against, taken by
:meth:`ResearchClock.of_frame`, which records that frame's dataset version. The
frame's identity is derived from the set version, the selection, the visibility
rule, the vintage policy, the staleness bound, the universe and the whole clock,
dataset version included, and it is carried as the frame's ``dataset_version``,
so a feature computed over it has exact lineage back to the observation set
*and* the prices it was sampled against.

v3.2's diagnostics refuse to correlate a factor with returns from another
dataset, and that refusal stands. :func:`align_prices` is how the two are joined
instead: it checks that a knowledge frame was sampled on exactly this price
frame's clock, and returns the prices identified by the frame's joint identity.
Forward returns computed from them then share the factor's identity -- because
that identity names both sources -- rather than because a check was loosened.

What a frame records
--------------------

Every point names the records behind it in :attr:`KnowledgeFrame.sources`, and
the frame counts what it could not answer -- instants at which nothing was yet
known, instants whose latest figure was too old, and records in the set whose
availability was never established and which were therefore never read.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from types import MappingProxyType
from typing import Any, Final

from alphalab.alt_data.information import InformationEvent
from alphalab.alt_data.observation import ExternalObservation
from alphalab.alt_data.observation_set import ObservationView, VintagePolicy
from alphalab.common.point_in_time import VisibilityRule
from alphalab.factor_library.definition import FeatureField
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries

__all__ = [
    "KNOWLEDGE_FRAME_SCHEME",
    "FrameRatio",
    "KnowledgeFrame",
    "KnowledgeStep",
    "ResearchClock",
    "align_prices",
    "derive_frame_id",
    "divide_frames",
    "event_frame",
    "observation_frame",
    "sample_knowledge",
]

#: Scheme tag, and the first line of every canonical knowledge-frame key.
KNOWLEDGE_FRAME_SCHEME: Final = "alphalab.knowledge_frame.v1"


@dataclass(frozen=True, slots=True)
class ResearchClock:
    """The instants a knowledge frame is sampled at, and where they came from.

    Attributes:
        instants: Strictly increasing, finite Unix seconds.
        timezone_name: The zone they are reported in.
        dataset_version: The dataset whose timestamps they are, or ``None`` for
            instants a caller listed. A frame sampled on a dataset's clock can be
            joined to that dataset with :func:`align_prices`; one sampled on a
            listed clock cannot.

    Raises:
        FactorInputError: If there are no instants, or they are not finite and
            strictly increasing.
    """

    instants: tuple[float, ...]
    timezone_name: str
    dataset_version: str | None

    def __post_init__(self) -> None:
        if not self.instants:
            raise FactorInputError("A research clock needs at least one instant.")
        for instant in self.instants:
            if isinstance(instant, bool) or not isinstance(instant, int | float):
                raise FactorInputError(f"Research instant {instant!r} is not a number of seconds.")
            if not math.isfinite(instant):
                raise FactorInputError(f"Research instant {instant!r} is not finite.")
        if any(later <= earlier for earlier, later in pairwise(self.instants)):
            raise FactorInputError(
                "The research instants must be strictly increasing. A clock that repeats or "
                "runs backwards has no 'latest known' at each tick."
            )
        if not self.timezone_name.strip():
            raise FactorInputError("A research clock names the zone its instants are in.")

    @classmethod
    def of_frame(cls, frame: ObservationFrame) -> ResearchClock:
        """The clock of a frame: its timestamps, its zone and its dataset version."""

        return cls(frame.timestamps, frame.timezone_name, frame.dataset_version)

    @classmethod
    def of_instants(cls, instants: Sequence[float], timezone_name: str) -> ResearchClock:
        """A clock a caller lists, belonging to no dataset."""

        return cls(tuple(instants), timezone_name, None)


@dataclass(frozen=True, slots=True)
class KnowledgeStep:
    """One change in what was known about one subject.

    Attributes:
        known_at: The instant the change became knowable.
        value: The latest value from then on, or ``None`` when nothing usable
            was known.
        observed_at: The instant that value describes -- what staleness is
            measured from -- or ``None``.
        record_ids: The records behind the value.
    """

    known_at: float
    value: float | None
    observed_at: float | None
    record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeFrame:
    """What was knowable about each subject at each research instant.

    Attributes:
        frame: The values, as an ``OBSERVATION`` frame whose
            ``dataset_version`` is :attr:`frame_id`. Instants at which a subject
            had no usable value are absent from its series, never filled.
        frame_id: The derived identity; see :func:`derive_frame_id`.
        set_version: The observation set sampled.
        selection: What was read from it, in words.
        clock: The instants sampled at, and the dataset they came from.
        visibility: The rule deciding what was knowable.
        policy: Which revision of each figure was read.
        max_age_seconds: The staleness bound, or ``None`` for any age.
        sources: Subject to the record identities behind each of its points,
            aligned with ``frame.series[subject].timestamps``.
        unknown: Subject-instants at which nothing was yet knowable.
        stale: Subject-instants whose latest knowable figure was older than
            the bound.
        unverified: Records in the set whose availability was never
            established under ``visibility``, and which no point read.
    """

    frame: ObservationFrame
    frame_id: str
    set_version: str
    selection: str
    clock: ResearchClock
    visibility: VisibilityRule
    policy: VintagePolicy
    max_age_seconds: float | None
    sources: Mapping[str, tuple[tuple[str, ...], ...]]
    unknown: int
    stale: int
    unverified: int


def derive_frame_id(
    kind: str,
    set_version: str,
    selection: str,
    visibility: VisibilityRule,
    policy: VintagePolicy,
    max_age_seconds: float | None,
    clock: ResearchClock,
    subjects: Sequence[str],
) -> str:
    """``"<selection>@<sha256>"`` over everything that decides a frame's values.

    The clock's instants enter as a digest of their ``repr``, so a frame over a
    million bars has a key of fixed size; the count and the clock's dataset
    version are rendered beside it.
    """

    instants = hashlib.sha256(
        "\n".join(repr(instant) for instant in clock.instants).encode("utf-8")
    ).hexdigest()
    lines = [
        KNOWLEDGE_FRAME_SCHEME,
        f"kind={kind!r}",
        f"set={set_version!r}",
        f"selection={selection!r}",
        f"visibility={visibility.name}",
        f"policy={policy.name}",
        f"max_age_seconds={max_age_seconds!r}",
        f"clock.dataset={clock.dataset_version!r}",
        f"clock.timezone={clock.timezone_name!r}",
        f"clock.instants={len(clock.instants)}:{instants}",
        "subjects",
        *(repr(subject) for subject in sorted(subjects)),
    ]
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return f"{selection}@{digest}"


def _require_universe(subjects: Sequence[str]) -> None:
    if not subjects:
        raise FactorInputError("A knowledge frame needs at least one subject.")
    if len(set(subjects)) != len(subjects):
        raise FactorInputError(f"The universe repeats a subject: {sorted(subjects)}.")


def sample_knowledge(
    timelines: Mapping[str, Sequence[KnowledgeStep]],
    instants: Sequence[float],
    max_age_seconds: float | None,
) -> tuple[dict[str, ObservationSeries], dict[str, tuple[tuple[str, ...], ...]], int, int]:
    """Sample each subject's timeline at each research instant.

    Returns the per-subject series, the records behind each point, and the
    counts of unknown and stale subject-instants. Public so every frame builder
    samples the same way, and so a caller with a timeline of their own can too.

    Raises:
        FactorInputError: If the bound is negative or not finite.
    """

    if max_age_seconds is not None and (
        not math.isfinite(max_age_seconds) or max_age_seconds < 0.0
    ):
        raise FactorInputError(
            f"max_age_seconds is {max_age_seconds!r}; a staleness bound is a finite, "
            "non-negative number of seconds, or None for any age."
        )
    series: dict[str, ObservationSeries] = {}
    sources: dict[str, tuple[tuple[str, ...], ...]] = {}
    unknown = 0
    stale = 0
    for subject in sorted(timelines):
        steps = timelines[subject]
        known = [step.known_at for step in steps]
        stamps: list[float] = []
        values: list[float] = []
        behind: list[tuple[str, ...]] = []
        for instant in instants:
            position = bisect_right(known, instant)
            step = steps[position - 1] if position else None
            if step is None or step.value is None or step.observed_at is None:
                unknown += 1
                continue
            if max_age_seconds is not None and instant - step.observed_at > max_age_seconds:
                stale += 1
                continue
            stamps.append(instant)
            values.append(step.value)
            behind.append(step.record_ids)
        if stamps:
            series[subject] = ObservationSeries(subject, tuple(stamps), tuple(values))
            sources[subject] = tuple(behind)
    return series, sources, unknown, stale


def _frame(
    kind: str,
    view: ObservationView[Any],
    selection: str,
    timelines: Mapping[str, Sequence[KnowledgeStep]],
    clock: ResearchClock,
    subjects: Sequence[str],
    max_age_seconds: float | None,
    policy: VintagePolicy,
) -> KnowledgeFrame:
    series, sources, unknown, stale = sample_knowledge(timelines, clock.instants, max_age_seconds)
    if not series:
        raise FactorInputError(
            f"Nothing about {selection!r} was knowable, within the staleness bound, for any "
            f"subject at any of the {len(clock.instants)} research instant(s). A frame of no "
            "values would make every feature over it vacuously complete."
        )
    frame_id = derive_frame_id(
        kind,
        view.set_version,
        selection,
        view.visibility,
        policy,
        max_age_seconds,
        clock,
        subjects,
    )
    return KnowledgeFrame(
        frame=ObservationFrame(
            source_field=FeatureField.OBSERVATION,
            series=MappingProxyType(series),
            timezone_name=clock.timezone_name,
            dataset_version=frame_id,
        ),
        frame_id=frame_id,
        set_version=view.set_version,
        selection=selection,
        clock=clock,
        visibility=view.visibility,
        policy=policy,
        max_age_seconds=max_age_seconds,
        sources=MappingProxyType(sources),
        unknown=unknown,
        stale=stale,
        unverified=len(view.index.unverified),
    )


def observation_frame(
    view: ObservationView[ExternalObservation],
    category: str,
    metric: str,
    clock: ResearchClock,
    subjects: Sequence[str],
    *,
    max_age_seconds: float | None,
    policy: VintagePolicy,
) -> KnowledgeFrame:
    """The latest knowable value of one metric, for each subject, at each instant.

    Args:
        view: The observation set, indexed under the visibility rule to apply.
        category: The observations' category.
        metric: The metric to read.
        clock: The research clock -- typically
            ``ResearchClock.of_frame(prices)`` for the price frame the result
            will be measured against.
        subjects: The universe. A subject with no such series contributes only
            unknown instants.
        max_age_seconds: How old, measured from the instant a figure describes,
            a figure may be and still be read. ``None`` for any age.
        policy: Which revision of each figure to read.

    Raises:
        FactorInputError: If the universe is malformed or nothing was knowable
            anywhere.
    """

    _require_universe(subjects)
    keys = {key[2:]: key for key in view.series_keys}
    timelines: dict[str, list[KnowledgeStep]] = {}
    for subject in subjects:
        key = keys.get((category, subject, metric))
        timelines[subject] = (
            []
            if key is None
            else [
                KnowledgeStep(
                    known_at, float(record.value), record.stamp.observed_at, (record.record_id,)
                )
                for known_at, record in view.latest_timeline(key, policy)
            ]
        )
    return _frame(
        "observation",
        view,
        f"{category}.{metric}",
        timelines,
        clock,
        subjects,
        max_age_seconds,
        policy,
    )


def event_frame(
    view: ObservationView[InformationEvent],
    event_type: str,
    measurement: str,
    clock: ResearchClock,
    subjects: Sequence[str],
    *,
    max_age_seconds: float | None,
    policy: VintagePolicy,
) -> KnowledgeFrame:
    """The latest knowable value of one event measurement, per subject and instant.

    The last earnings surprise known at each bar, the last CPI print known on
    each day. Staleness is measured from the instant the event occurred.

    Raises:
        FactorInputError: If the universe is malformed; if an event in a
            sampled series lacks ``measurement`` -- restrict the set with
            :func:`~alphalab.alt_data.information.restrict_measured` so the
            omission is recorded rather than silent; or if nothing was
            knowable anywhere.
    """

    _require_universe(subjects)
    keys = {key[2:]: key for key in view.series_keys}
    timelines: dict[str, list[KnowledgeStep]] = {}
    for subject in subjects:
        key = keys.get((event_type, subject))
        steps: list[KnowledgeStep] = []
        if key is not None:
            for record in view.series[key].records:
                if measurement not in record.measurements:
                    raise FactorInputError(
                        f"{event_type} for {subject!r} at {record.stamp.observed_at!r} carries "
                        f"no {measurement!r}. Restrict the set with restrict_measured so the "
                        "events without it are left out visibly."
                    )
            for known_at, record in view.latest_timeline(key, policy):
                value: Decimal = record.measurements[measurement]
                steps.append(
                    KnowledgeStep(
                        known_at, float(value), record.stamp.observed_at, (record.record_id,)
                    )
                )
        timelines[subject] = steps
    return _frame(
        "event",
        view,
        f"{event_type}:{measurement}",
        timelines,
        clock,
        subjects,
        max_age_seconds,
        policy,
    )


def align_prices(prices: ObservationFrame, knowledge: KnowledgeFrame) -> ObservationFrame:
    """The price frame a knowledge frame was sampled on, identified by the join.

    v3.2's information coefficient, signal diagnostics and decay refuse a factor
    and returns from two different datasets, and a knowledge frame *is* a
    different dataset from the prices. This is the checked join: the knowledge
    frame must have been sampled on exactly this frame's clock -- same dataset
    version, same instants -- and the prices come back unchanged in value,
    identified by the knowledge frame's own identity, which names both the
    observation set and these prices. Returns computed from the result then
    share the factor's identity for the reason that it is true.

    Raises:
        FactorInputError: If the knowledge frame was sampled on a listed clock,
            on another dataset, or on other instants than this frame's.
    """

    clock = knowledge.clock
    if clock.dataset_version is None:
        raise FactorInputError(
            f"Knowledge frame {knowledge.frame_id} was sampled on instants a caller listed, not "
            "on a dataset's clock, so it names no prices it could be joined to. Sample it on "
            "ResearchClock.of_frame(prices)."
        )
    if prices.dataset_version != clock.dataset_version:
        raise FactorInputError(
            f"Knowledge frame {knowledge.frame_id} was sampled on {clock.dataset_version!r} and "
            f"these prices are {prices.dataset_version!r}. Joining them would measure the "
            "factor against data it was never aligned to."
        )
    if prices.timestamps != clock.instants or prices.timezone_name != clock.timezone_name:
        raise FactorInputError(
            f"Knowledge frame {knowledge.frame_id} was sampled on another clock than this "
            "frame's instants. Sample it on ResearchClock.of_frame of these prices."
        )
    return replace(prices, dataset_version=knowledge.frame_id)


@dataclass(frozen=True, slots=True)
class FrameRatio:
    """One frame divided by another, point by point.

    Attributes:
        frame: The quotient at every subject-instant both frames hold and the
            denominator is not zero. Its ``dataset_version`` is the inputs'
            shared one when they share one; derived from both when they differ;
            ``None`` if either is untraceable.
        undefined: Subject-instants left out because the denominator was zero
            -- undefined, and therefore absent rather than infinite or zero.
    """

    frame: ObservationFrame
    undefined: int


def divide_frames(
    numerator: ObservationFrame, denominator: ObservationFrame, *, name: str
) -> FrameRatio:
    """Divide two frames where both have a value: an earnings yield, a sales-to-price.

    A ratio of two frames from one dataset -- a knowledge frame and the prices
    aligned to it by :func:`align_prices` -- keeps that dataset's identity, as a
    feature computed over a dataset keeps the dataset's; the ratio's own
    identity is the feature definition computed over it. A ratio of two frames
    from different datasets is identified by both.

    Both frames must be reported in the same zone; a quotient of two clocks
    that disagree about what an instant means would pair the wrong values.

    Raises:
        FactorInputError: If the zones differ, the name is blank or contains
            ``"@"``, or the frames share no subject-instant at all.
    """

    if not name.strip() or "@" in name:
        raise FactorInputError(f"A frame ratio is named, without '@': {name!r}.")
    if numerator.timezone_name != denominator.timezone_name:
        raise FactorInputError(
            f"The numerator is reported in {numerator.timezone_name!r} and the denominator in "
            f"{denominator.timezone_name!r}."
        )
    series: dict[str, ObservationSeries] = {}
    undefined = 0
    for subject in sorted(set(numerator.series) & set(denominator.series)):
        upper = numerator.series[subject]
        top = dict(zip(upper.timestamps, upper.values, strict=True))
        bottom = denominator.series[subject]
        stamps: list[float] = []
        values: list[float] = []
        for stamp, divisor in zip(bottom.timestamps, bottom.values, strict=True):
            dividend = top.get(stamp)
            if dividend is None:
                continue
            if divisor == 0.0:
                undefined += 1
                continue
            stamps.append(stamp)
            values.append(dividend / divisor)
        if stamps:
            series[subject] = ObservationSeries(subject, tuple(stamps), tuple(values))
    if not series:
        raise FactorInputError(
            f"{name!r}: the two frames share no subject-instant with a defined quotient."
        )
    left, right = numerator.dataset_version, denominator.dataset_version
    version: str | None
    if left is None or right is None:
        version = None
    elif left == right:
        version = left
    else:
        key = "\n".join(
            [
                KNOWLEDGE_FRAME_SCHEME,
                "kind='ratio'",
                f"name={name!r}",
                f"numerator={left!r}",
                f"denominator={right!r}",
            ]
        )
        version = f"{name}@{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
    return FrameRatio(
        frame=ObservationFrame(
            source_field=FeatureField.OBSERVATION,
            series=MappingProxyType(series),
            timezone_name=numerator.timezone_name,
            dataset_version=version,
        ),
        undefined=undefined,
    )
