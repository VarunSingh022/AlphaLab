"""The generic external observation: one measured value, and when it was knowable.

v1 gave alternative data one type per category -- news sentiment, satellite,
shipping, ESG, card spend -- each with its own field names and a closed enum of
metrics. That is readable and it does not extend: a weather index, a web-traffic
count or a new vendor's panel needs a new class in AlphaLab before a caller can
represent it. An :class:`ExternalObservation` is the extensible form. Its
category and metric are dotted identifiers the caller chooses, so a new kind of
data needs vocabulary rather than code, and every observation carries the same
point-in-time stamp, source identity and revision number whatever it measures.

The v1 types stay. They are typed conveniences, and :func:`observation_from_release`
lifts any of them -- or anything else satisfying
:class:`~alphalab.common.point_in_time.PointInTimeRecord` -- into this form,
with the record's ``release_date`` as a declared availability instant.

Revisions are vintages, not replacements
----------------------------------------

A revised figure is a *second* observation with a higher :attr:`revision`, not an
edit of the first. Both stay in a set, and which one research reads is decided
at the research instant by
:class:`~alphalab.alt_data.observation_set.VintagePolicy`: the latest revision
knowable then, or the value as first published. Treating a revision as a
replacement is exactly how restated data comes to be read as though it had
always been known.

Identity
--------

:attr:`ExternalObservation.record_id` is derived from the content -- category,
subject, metric, value, unit, period, revision, the source's identity and version,
and the whole stamp -- under :data:`OBSERVATION_KEY_SCHEME`. A value's
*spelling* is kept: ``Decimal("1.50")`` and ``Decimal("1.5")`` render
differently, as v3.6 keeps ``10`` and ``10.0`` apart, because the bytes a figure
was read from spelled it one way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.identity import digest_lines, stamp_lines
from alphalab.alt_data.source import ObservationSource
from alphalab.alt_data.validation import (
    require_finite_instant,
    require_finite_value,
    require_identifier,
    require_label,
    require_revision,
)
from alphalab.common.point_in_time import PointInTimeRecord, PointInTimeStamp

__all__ = [
    "OBSERVATION_KEY_SCHEME",
    "ExternalObservation",
    "ReferencePeriod",
    "canonical_observation_key",
    "observation_from_release",
]

#: Scheme tag, and the first line of every canonical observation key. Frozen for
#: the life of the scheme; changing it re-identifies every observation.
OBSERVATION_KEY_SCHEME: Final = "alphalab.observation.v1"


@dataclass(frozen=True, slots=True)
class ReferencePeriod:
    """The span of time a periodic figure describes.

    Attributes:
        start: Unix seconds at which the period begins.
        end: Unix seconds at which it ends. Equal to ``start`` for a figure
            describing one instant, such as a balance at a date.
        label: What the period is called -- ``"2024-05"``, ``"2024W19"``.
            Part of identity, and the discriminator between two vintages'
            periods.

    Raises:
        AltDataInputError: If an instant is not finite, the period runs
            backwards, or the label is blank.
    """

    start: float
    end: float
    label: str

    def __post_init__(self) -> None:
        require_finite_instant(self.start, "ReferencePeriod.start")
        require_finite_instant(self.end, "ReferencePeriod.end")
        if self.end < self.start:
            raise AltDataInputError(
                f"ReferencePeriod {self.label!r} ends at {self.end!r}, before it starts at "
                f"{self.start!r}."
            )
        require_label(self.label, "ReferencePeriod.label")


@dataclass(frozen=True, slots=True)
class ExternalObservation:
    """One measured value of something outside the market data itself.

    Attributes:
        category: The kind of data, as a dotted identifier -- ``"economic"``,
            ``"sentiment"``, ``"satellite"``, ``"weather"``, ``"web"``,
            ``"fundamental"``. Open: a new category needs no change here.
        subject: What the value is about -- a symbol, a country, a port, a
            region. Research reads data as ingested, so this is the caller's
            key and not an ``asset_id``, the rule
            :class:`~alphalab.factor_library.observations.ObservationSeries`
            states for symbols.
        metric: What was measured, as a dotted identifier -- ``"cpi_yoy"``,
            ``"parking_lot_traffic"``. The v1 vocabulary: a satellite or
            shipping *metric*, now open rather than an enum.
        value: The measured value, exactly as reported.
        unit: What the value is in -- ``"percent"``, ``"index"``, ``"USD"``,
            ``"count"``. Required: a figure whose unit is assumed is a figure
            that can be compared with the wrong thing.
        stamp: When the value describes, when it became knowable and how that
            was established, and when it takes effect.
        source: Which source, at which version.
        period: The period a periodic figure describes, or ``None`` for a
            measurement at one instant. When present, ``stamp.observed_at``
            must lie within it.
        revision: ``0`` for the value as first published, counting up with
            each revision of the same subject, metric and period.
        record_id: The derived, content-addressed identity. Computed once, on
            construction, from :func:`canonical_observation_key`; never
            supplied.

    Raises:
        AltDataInputError: If any field fails its shape, or the observation
            instant lies outside the declared period.
    """

    category: str
    subject: str
    metric: str
    value: Decimal
    unit: str
    stamp: PointInTimeStamp
    source: ObservationSource
    period: ReferencePeriod | None
    revision: int
    record_id: str = field(init=False)

    def __post_init__(self) -> None:
        require_identifier(self.category, "ExternalObservation.category")
        require_label(self.subject, "ExternalObservation.subject")
        require_identifier(self.metric, "ExternalObservation.metric")
        require_finite_value(self.value, "ExternalObservation.value")
        require_label(self.unit, "ExternalObservation.unit")
        require_revision(self.revision, "ExternalObservation.revision")
        if self.period is not None and not (
            self.period.start <= self.stamp.observed_at <= self.period.end
        ):
            raise AltDataInputError(
                f"{self.category}.{self.metric} for {self.subject!r} is observed at "
                f"{self.stamp.observed_at!r}, outside the period {self.period.label!r} "
                f"[{self.period.start!r}, {self.period.end!r}] it claims to describe."
            )
        object.__setattr__(self, "record_id", digest_lines(_observation_lines(self)))

    @property
    def record_scheme(self) -> str:
        """The scheme this record kind's identities are derived under."""

        return OBSERVATION_KEY_SCHEME

    @property
    def series_key(self) -> tuple[str, ...]:
        """What the observation is a value *of*: source, category, subject, metric."""

        return (
            self.source.source_id,
            repr(self.source.version),
            self.category,
            self.subject,
            self.metric,
        )

    @property
    def vintage_key(self) -> tuple[str, ...]:
        """The series and the period -- what revisions of one figure share.

        A figure with no period is discriminated by its observation instant,
        so a recomputed value for the same instant is a revision of it.
        """

        discriminator = (
            self.period.label if self.period is not None else repr(self.stamp.observed_at)
        )
        return (*self.series_key, discriminator)


def canonical_observation_key(observation: ExternalObservation) -> str:
    """Render the canonical key an observation's identity is derived from.

    Public so the rendering can be pinned by a test and read by anyone auditing
    an identity.
    """

    return "\n".join(_observation_lines(observation))


def _observation_lines(observation: ExternalObservation) -> list[str]:
    period = observation.period
    return [
        OBSERVATION_KEY_SCHEME,
        f"category={observation.category!r}",
        f"subject={observation.subject!r}",
        f"metric={observation.metric!r}",
        f"value={str(observation.value)!r}",
        f"unit={observation.unit!r}",
        f"revision={observation.revision!r}",
        "period="
        + ("None" if period is None else f"{period.label!r}[{period.start!r},{period.end!r}]"),
        *observation.source.record_lines,
        *stamp_lines(observation.stamp),
    ]


def observation_from_release(
    record: PointInTimeRecord,
    *,
    category: str,
    subject: str,
    metric: str,
    value: Decimal,
    unit: str,
    source: ObservationSource,
    revision: int,
) -> ExternalObservation:
    """Lift any record with a reference period and a release date into this form.

    The v1 alternative-data types and ``macro.IndicatorObservation`` all satisfy
    :class:`~alphalab.common.point_in_time.PointInTimeRecord`. Their
    ``release_date`` is documented as the instant the value became available,
    so it becomes a *declared* availability instant; their ``reference_period``
    becomes the observation instant. Nothing about the record is inferred: the
    category, metric, unit and revision are the caller's to state, because none
    of them is on the v1 types in a form this record can read.
    """

    return ExternalObservation(
        category=category,
        subject=subject,
        metric=metric,
        value=value,
        unit=unit,
        stamp=PointInTimeStamp.declared(record.reference_period, record.release_date),
        source=source,
        period=None,
        revision=revision,
    )
