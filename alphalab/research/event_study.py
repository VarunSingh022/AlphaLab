"""Event studies: what prices did around information, anchored where it could be traded.

An event study lines up occurrences -- earnings releases, CPI prints, split
announcements, headlines -- and measures the returns around each: drift before,
reaction at, and drift after. The one decision that makes such a study honest
or not is where the window is *anchored*, and this module takes it out of the
caller's hands:

1. an event is anchored at the instant it became **knowable** under the study's
   :class:`~alphalab.common.point_in_time.VisibilityRule` -- never at the
   instant it happened, and never at all if its availability was never
   established;
2. that instant is placed in the venue's trading day by
   :func:`~alphalab.alt_data.sessions.place_record`, so a release after the
   close reaches the next session and a pre-market release reaches the open;
3. the anchor observation is the first price observation at or after that
   tradable instant.

Offset 0's return therefore runs from the last observation before the
information could be traded to the first one after, and nothing at a negative
offset can contain the event. That holds provided each price observation is
stamped when it became known -- a bar at its close -- which is how AlphaLab's
datasets stamp bars; a series stamped at the *start* of each period would put
information inside an observation dated before it.

A correction is not a second event
----------------------------------

Revisions of one event share a vintage key. The study anchors each event at its
*first* knowable report and excludes later reports of it by name: the market
reacted to the first report, and anchoring a correction as a separate event
would count one reaction twice.

What is reported, and what is not
---------------------------------

Per event: every abnormal return in the window, the cumulative abnormal return
(CAR), and its pre- and post-anchor parts. Across events: the average abnormal
return at each offset, the mean, median and dispersion of CARs, and the share
positive -- each with the count behind it -- plus every excluded event with the
reason.

There is **no t-statistic and no p-value**. Events cluster in calendar time
(half a universe reports in the same fortnight) and share market moves, so
cross-sectional test statistics that assume independent events overstate
significance by an amount that depends on the clustering. ADR-0037 declines a
p-value on an information coefficient for the same kind of reason. The result
reports how many events shared an anchor instant, so a reader can see the
clustering rather than trust a test that ignores it.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from alphalab.alt_data.information import InformationEvent
from alphalab.alt_data.observation_set import ObservationSet
from alphalab.alt_data.sessions import SessionCalendar, SessionTiming, place_record
from alphalab.common.point_in_time import VisibilityRule
from alphalab.common.statistics import mean, median, standard_deviation
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.research.exceptions import ResearchValidationError

__all__ = [
    "EVENT_STUDY_SCHEME",
    "AbnormalReturnModel",
    "EventOutcome",
    "EventStudyDefinition",
    "EventStudyResult",
    "EventWindow",
    "ExcludedEvent",
    "canonical_event_study_key",
    "event_study",
    "event_study_metrics",
]

#: Scheme tag, and the first line of every canonical event-study key.
EVENT_STUDY_SCHEME: Final = "alphalab.event_study.v1"


class AbnormalReturnModel(Enum):
    """What an event's return is measured *against*."""

    #: The security's own return. Includes whatever the market did.
    RAW = auto()

    #: The security's return less a named benchmark's return over the same
    #: observation. The benchmark is part of the study's identity.
    MARKET_ADJUSTED = auto()

    #: The security's return less its own mean return over an estimation window
    #: that ends before the event window begins -- so the model never sees the
    #: event it is the baseline for.
    MEAN_ADJUSTED = auto()


@dataclass(frozen=True, slots=True)
class EventWindow:
    """How many observations around the anchor a study reads.

    Counts of *observations*, not seconds, for the reason ADR-0037 gives for
    feature windows: a window in days spans a holiday week with fewer
    observations than its neighbour, and the two are then not comparable.

    Attributes:
        before: Observations before the anchor included in the window.
        after: Observations after the anchor included. The anchor itself is
            offset 0 and always included.
        estimation: Returns in the estimation window. Read only by
            ``MEAN_ADJUSTED``, which needs at least two.
        gap: Observations between the end of the estimation window and the
            start of the event window, so pre-event drift does not leak into
            the baseline. Read only by ``MEAN_ADJUSTED``.

    Raises:
        ResearchValidationError: If any count is negative.
    """

    before: int
    after: int
    estimation: int
    gap: int

    def __post_init__(self) -> None:
        for name in ("before", "after", "estimation", "gap"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ResearchValidationError(
                    f"EventWindow.{name} is {value!r}; it is a count of observations."
                )

    @property
    def offsets(self) -> tuple[int, ...]:
        """Every offset in the window, from ``-before`` to ``+after``."""

        return tuple(range(-self.before, self.after + 1))


@dataclass(frozen=True, slots=True)
class EventStudyDefinition:
    """One event study, stated completely.

    Attributes:
        name: What the study is called. Part of its identity.
        event_types: The event types studied, sorted on construction.
        window: The observations read around each anchor.
        model: What returns are measured against.
        benchmark: The benchmark symbol, for ``MARKET_ADJUSTED`` only.
        visibility: The clock that decides when an event became knowable.

    Raises:
        ResearchValidationError: If the name is blank or contains ``"@"``; if
            no event type is named or one repeats; if the model is missing
            what it needs -- a benchmark, an estimation window of at least two
            returns -- or is given what it does not read. An unread parameter
            would still change the study's identity, giving one study two
            names.
    """

    name: str
    event_types: tuple[str, ...]
    window: EventWindow
    model: AbnormalReturnModel
    benchmark: str | None
    visibility: VisibilityRule

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_types", tuple(sorted(self.event_types)))
        if not self.name.strip() or "@" in self.name:
            raise ResearchValidationError(f"An event study is named, without '@': {self.name!r}.")
        if not self.event_types:
            raise ResearchValidationError("An event study must name the event types it studies.")
        if len(set(self.event_types)) != len(self.event_types):
            raise ResearchValidationError(f"The event types repeat: {list(self.event_types)}.")
        adjusted = self.model is AbnormalReturnModel.MARKET_ADJUSTED
        if adjusted and not self.benchmark:
            raise ResearchValidationError(
                "A market-adjusted study measures returns against a benchmark and names none."
            )
        if not adjusted and self.benchmark is not None:
            raise ResearchValidationError(
                f"A {self.model.name} study reads no benchmark and was given {self.benchmark!r}."
            )
        if self.model is AbnormalReturnModel.MEAN_ADJUSTED:
            if self.window.estimation < 2:
                raise ResearchValidationError(
                    "A mean-adjusted study needs an estimation window of at least two returns; "
                    f"got {self.window.estimation}."
                )
        elif self.window.estimation or self.window.gap:
            raise ResearchValidationError(
                f"A {self.model.name} study reads no estimation window, and was given "
                f"estimation={self.window.estimation} and gap={self.window.gap}."
            )

    @property
    def study_id(self) -> str:
        """This study's derived, reproducible identity."""

        digest = hashlib.sha256(canonical_event_study_key(self).encode("utf-8")).hexdigest()
        return f"{self.name}@{digest}"


def canonical_event_study_key(definition: EventStudyDefinition) -> str:
    """Render the canonical key an event study's identity is derived from."""

    window = definition.window
    return "\n".join(
        [
            EVENT_STUDY_SCHEME,
            f"name={definition.name!r}",
            f"event_types={definition.event_types!r}",
            f"before={window.before!r}",
            f"after={window.after!r}",
            f"estimation={window.estimation!r}",
            f"gap={window.gap!r}",
            f"model={definition.model.name}",
            f"benchmark={definition.benchmark!r}",
            f"visibility={definition.visibility.name}",
        ]
    )


@dataclass(frozen=True, slots=True)
class EventOutcome:
    """What one event's window measured.

    Attributes:
        event_id: The event's record identity.
        subject: Whose prices were read.
        event_type: What kind of event.
        known_at: When it became knowable under the study's rule.
        tradable_at: The first instant the venue was open at or after that.
        timing: Where ``known_at`` fell in the trading day.
        anchor: The timestamp of the anchor observation, offset 0.
        abnormal_returns: One per offset, from ``-before`` to ``+after``.
        cumulative: Their sum -- the cumulative abnormal return.
        pre_event: The sum over negative offsets.
        post_event: The sum from offset 0 on.
    """

    event_id: str
    subject: str
    event_type: str
    known_at: float
    tradable_at: float
    timing: SessionTiming
    anchor: float
    abnormal_returns: tuple[float, ...]
    cumulative: float
    pre_event: float
    post_event: float


@dataclass(frozen=True, slots=True)
class ExcludedEvent:
    """An event the study did not measure, and why.

    Attributes:
        event_id: The event's record identity.
        subject: Whose event it was.
        reason: Why it was left out, in words.
    """

    event_id: str
    subject: str
    reason: str


@dataclass(frozen=True, slots=True)
class EventStudyResult:
    """Everything one event study measured, and what it could not.

    Attributes:
        definition: The study.
        event_set_version: The event set the events came from.
        prices_version: The price frame's dataset version, or ``None`` for an
            untraceable frame.
        calendar_id: The venue calendar events were placed against.
        outcomes: The measured events, ordered by anchor, subject and identity.
        excluded: The events left out, with reasons, ordered by identity.
        average_abnormal: The mean abnormal return at each offset, across
            every measured event -- all of which have a full window, so each
            mean rests on ``len(outcomes)`` values.
        mean_cumulative: The mean CAR.
        median_cumulative: The median CAR.
        cumulative_std: The sample standard deviation of CARs, or ``None`` for
            fewer than two events.
        positive_share: The share of events with a positive CAR.
        shared_anchor_events: How many measured events share their anchor
            instant with another -- the calendar clustering that makes a test
            assuming independent events unsound.
        result_id: SHA-256 over the study, its inputs and every number.
    """

    definition: EventStudyDefinition
    event_set_version: str
    prices_version: str | None
    calendar_id: str
    outcomes: tuple[EventOutcome, ...]
    excluded: tuple[ExcludedEvent, ...]
    average_abnormal: tuple[float, ...]
    mean_cumulative: float
    median_cumulative: float
    cumulative_std: float | None
    positive_share: float
    shared_anchor_events: int
    result_id: str

    @property
    def offsets(self) -> tuple[int, ...]:
        """The window's offsets, aligned with ``average_abnormal``."""

        return self.definition.window.offsets

    def verify(self) -> bool:
        """Whether :attr:`result_id` still matches this result's content."""

        return self.result_id == _derive_result_id(
            self.definition,
            self.event_set_version,
            self.prices_version,
            self.calendar_id,
            self.outcomes,
            self.excluded,
        )


def _derive_result_id(
    definition: EventStudyDefinition,
    event_set_version: str,
    prices_version: str | None,
    calendar_id: str,
    outcomes: Sequence[EventOutcome],
    excluded: Sequence[ExcludedEvent],
) -> str:
    lines = [
        f"{EVENT_STUDY_SCHEME}.result",
        f"study={definition.study_id!r}",
        f"events={event_set_version!r}",
        f"prices={prices_version!r}",
        f"calendar={calendar_id!r}",
        "outcomes",
        *(
            f"{outcome.event_id!r}@{outcome.anchor!r}:{outcome.abnormal_returns!r}"
            for outcome in outcomes
        ),
        "excluded",
        *(f"{entry.event_id!r}:{entry.reason!r}" for entry in excluded),
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _returns(row: ObservationSeries, first: int, last: int) -> list[float] | str:
    """Simple returns for observations ``first..last`` inclusive, or a reason."""

    found: list[float] = []
    for index in range(first, last + 1):
        base = row.values[index - 1]
        if base <= 0.0:
            return f"a non-positive price ({base!r}) at {row.timestamps[index - 1]!r}"
        found.append(row.values[index] / base - 1.0)
    return found


def event_study(
    events: ObservationSet[InformationEvent],
    prices: ObservationFrame,
    calendar: SessionCalendar,
    definition: EventStudyDefinition,
) -> EventStudyResult:
    """Measure returns around every event of the studied types.

    Args:
        events: The versioned event set. Its version enters the result.
        prices: One price series per subject -- typically read from a
            canonical dataset -- and, for ``MARKET_ADJUSTED``, the benchmark.
        calendar: The venue the subjects trade on, satisfied structurally by
            :class:`~alphalab.data.calendar.MarketCalendar`.
        definition: The study.

    Raises:
        ResearchValidationError: If the set holds none of the studied types, or
            every event was excluded -- a study that measured nothing is not a
            study, and the exclusions it would have reported are in the message.
    """

    studied = [record for record in events.records if record.event_type in definition.event_types]
    if not studied:
        raise ResearchValidationError(
            f"Event set {events.version} holds no event of the types "
            f"{list(definition.event_types)}."
        )

    excluded: list[ExcludedEvent] = []
    first_reports: dict[tuple[str, ...], tuple[float, InformationEvent]] = {}
    for record in studied:
        known = record.stamp.known_at(definition.visibility)
        if known is None:
            excluded.append(
                ExcludedEvent(
                    record.record_id,
                    record.subject,
                    "its availability was never established under "
                    f"{definition.visibility.name}, so it has no instant to anchor on",
                )
            )
            continue
        held = first_reports.get(record.vintage_key)
        if held is None or (known, record.revision) < (held[0], held[1].revision):
            if held is not None:
                excluded.append(_later_report(held[1]))
            first_reports[record.vintage_key] = (known, record)
        else:
            excluded.append(_later_report(record))

    window = definition.window
    benchmark = None if definition.benchmark is None else prices.series.get(definition.benchmark)
    if definition.benchmark is not None and benchmark is None:
        raise ResearchValidationError(
            f"The benchmark {definition.benchmark!r} has no series in the price frame."
        )
    benchmark_values = (
        {} if benchmark is None else dict(zip(benchmark.timestamps, benchmark.values, strict=True))
    )

    outcomes: list[EventOutcome] = []
    for _, record in sorted(first_reports.values(), key=lambda entry: entry[1].record_id):
        measured = _measure(record, prices, calendar, definition, benchmark_values)
        if isinstance(measured, str):
            excluded.append(ExcludedEvent(record.record_id, record.subject, measured))
        else:
            outcomes.append(measured)

    if not outcomes:
        reasons = sorted({entry.reason for entry in excluded})
        raise ResearchValidationError(
            f"No event could be measured; {len(excluded)} were excluded, for these reasons: "
            f"{reasons[:5]}."
        )

    outcomes.sort(key=lambda outcome: (outcome.anchor, outcome.subject, outcome.event_id))
    excluded.sort(key=lambda entry: (entry.event_id, entry.reason))
    count = len(outcomes)
    average = tuple(
        sum(outcome.abnormal_returns[position] for outcome in outcomes) / count
        for position in range(len(window.offsets))
    )
    cumulative = [outcome.cumulative for outcome in outcomes]
    anchors: dict[float, int] = {}
    for outcome in outcomes:
        anchors[outcome.anchor] = anchors.get(outcome.anchor, 0) + 1
    shared = sum(number for number in anchors.values() if number > 1)

    frozen_outcomes = tuple(outcomes)
    frozen_excluded = tuple(excluded)
    return EventStudyResult(
        definition=definition,
        event_set_version=events.version,
        prices_version=prices.dataset_version,
        calendar_id=calendar.calendar_id,
        outcomes=frozen_outcomes,
        excluded=frozen_excluded,
        average_abnormal=average,
        mean_cumulative=mean(cumulative),
        median_cumulative=median(cumulative),
        cumulative_std=standard_deviation(cumulative) if count >= 2 else None,
        positive_share=sum(1 for value in cumulative if value > 0.0) / count,
        shared_anchor_events=shared,
        result_id=_derive_result_id(
            definition,
            events.version,
            prices.dataset_version,
            calendar.calendar_id,
            frozen_outcomes,
            frozen_excluded,
        ),
    )


def _later_report(record: InformationEvent) -> ExcludedEvent:
    return ExcludedEvent(
        record.record_id,
        record.subject,
        f"revision {record.revision} of an event already anchored at its first report",
    )


def _measure(
    record: InformationEvent,
    prices: ObservationFrame,
    calendar: SessionCalendar,
    definition: EventStudyDefinition,
    benchmark: dict[float, float],
) -> EventOutcome | str:
    """One event's outcome, or the reason it cannot be measured."""

    row = prices.series.get(record.subject)
    if row is None:
        return f"the price frame holds no series for {record.subject!r}"
    placement = place_record(record, definition.visibility, calendar)
    anchor = bisect_left(row.timestamps, placement.tradable_at)
    window = definition.window
    if anchor >= len(row.timestamps):
        return f"no price observation at or after the tradable instant {placement.tradable_at!r}"
    first = anchor - window.before
    last = anchor + window.after
    if first - 1 < 0:
        return "the window starts before the price series does"
    if last >= len(row.timestamps):
        return "the window ends after the price series does"

    raw = _returns(row, first, last)
    if isinstance(raw, str):
        return raw
    if definition.model is AbnormalReturnModel.RAW:
        abnormal = raw
    elif definition.model is AbnormalReturnModel.MARKET_ADJUSTED:
        abnormal = []
        for position, index in enumerate(range(first, last + 1)):
            now = benchmark.get(row.timestamps[index])
            before = benchmark.get(row.timestamps[index - 1])
            if now is None or before is None:
                return (
                    f"the benchmark has no observation at {row.timestamps[index - 1]!r} or "
                    f"{row.timestamps[index]!r}"
                )
            if before <= 0.0:
                return f"the benchmark price at {row.timestamps[index - 1]!r} is not positive"
            abnormal.append(raw[position] - (now / before - 1.0))
    else:
        estimation_last = first - window.gap - 1
        estimation_first = estimation_last - window.estimation + 1
        if estimation_first - 1 < 0:
            return "the estimation window starts before the price series does"
        baseline = _returns(row, estimation_first, estimation_last)
        if isinstance(baseline, str):
            return baseline
        expected = mean(baseline)
        abnormal = [value - expected for value in raw]

    returns = tuple(abnormal)
    return EventOutcome(
        event_id=record.record_id,
        subject=record.subject,
        event_type=record.event_type,
        known_at=placement.instant,
        tradable_at=placement.tradable_at,
        timing=placement.timing,
        anchor=row.timestamps[anchor],
        abnormal_returns=returns,
        cumulative=sum(returns),
        pre_event=sum(returns[: window.before]),
        post_event=sum(returns[window.before :]),
    )


def event_study_metrics(result: EventStudyResult) -> dict[str, float]:
    """The result's numbers, flat and named, for a
    :class:`~alphalab.research.study.StudyResult`.

    ``car_std`` is present only when defined; an absent dispersion is not zero.
    """

    metrics: dict[str, float] = {
        "events": float(len(result.outcomes)),
        "excluded": float(len(result.excluded)),
        "mean_car": result.mean_cumulative,
        "median_car": result.median_cumulative,
        "positive_share": result.positive_share,
        "shared_anchor_events": float(result.shared_anchor_events),
    }
    if result.cumulative_std is not None:
        metrics["car_std"] = result.cumulative_std
    for offset, value in zip(result.offsets, result.average_abnormal, strict=True):
        metrics[f"aar[{offset:+d}]"] = value
    return metrics
