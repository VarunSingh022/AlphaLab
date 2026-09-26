"""Information events: something happened, and here is when anyone could know it.

Event-driven research starts from occurrences -- an earnings release, a CPI
print, a split announcement, a headline, a sentiment shift, a satellite anomaly,
a restatement -- and asks what prices did around them. Before v3.7 AlphaLab
could represent each of these only in a shape of its own: a wire
``EconomicEvent`` with one timestamp, a ``macro.CentralBankEvent`` with a
decision date, a ``data.corporate_actions.Delisting`` with an effective date, a
v1 ``NewsSentiment`` with a publication date. None of them could say when the
occurrence became *knowable*, which is the one instant an event study must
anchor on.

An :class:`InformationEvent` is the one canonical shape. Its
:attr:`~InformationEvent.event_type` is a dotted identifier whose first word is
the category -- ``earnings.release``, ``macro.cpi``, ``corporate_action.split``,
``news.article``, ``sentiment.shift``, ``alternative.anomaly``,
``fundamental.restatement`` -- and the vocabulary is open: a new kind of event
needs a name, not a class. Every event carries the same
:class:`~alphalab.common.point_in_time.PointInTimeStamp`, so the occurrence
instant, the availability instant, the effective instant and the ingestion
instant are four separate facts for every kind of event alike.

Not an engine event
-------------------

:class:`~alphalab.common.events.BaseEvent` and the ``events.py`` module of every
engine package are *engine* events: notifications that a state changed inside
AlphaLab -- a dataset was ingested, a study completed. An information event is
data about the world, which a study reads and a strategy trades on. They share
an English word and nothing else, which is why this type is not called
``Event`` and does not live in an ``events.py``;
``tests/regression/test_shared_names_stay_distinct.py`` records the pair.

What an event carries
---------------------

``measurements`` holds the numbers that came with it -- an actual and a
consensus, a prior value, a ratio -- by identifier, each an exact ``Decimal``.
``attributes`` holds the words -- a fiscal period, a headline -- as text. Both
are optional and both are part of the event's identity. A surprise is computed
by :func:`surprise` from two measurements the caller *names*, because which
figure was expected is the caller's statement, not a convention AlphaLab
assumes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.identity import digest_lines, stamp_lines
from alphalab.alt_data.observation_set import ObservationSet, _restricted
from alphalab.alt_data.source import ObservationSource
from alphalab.alt_data.validation import (
    require_finite_value,
    require_identifier,
    require_label,
    require_revision,
)
from alphalab.common.point_in_time import PointInTimeStamp

__all__ = [
    "INFORMATION_EVENT_KEY_SCHEME",
    "InformationEvent",
    "canonical_event_key",
    "restrict_measured",
    "surprise",
]

#: Scheme tag, and the first line of every canonical information-event key.
INFORMATION_EVENT_KEY_SCHEME: Final = "alphalab.information_event.v1"


@dataclass(frozen=True, slots=True)
class InformationEvent:
    """One occurrence in the world, with the instant it became knowable.

    Attributes:
        event_type: What happened, as a dotted identifier whose first word is
            the category -- ``"earnings.release"``, ``"macro.cpi"``.
        subject: Who or what it happened to -- a symbol, an issuer, a country.
        stamp: ``observed_at`` is when it happened (the announcement, the
            print); ``available_at`` when anyone could know it; ``effective_at``
            when it takes effect, where that differs (a split's ex-date).
        source: Which source reported it, at which version.
        measurements: Identifier to exact value -- ``"actual"``,
            ``"consensus"``. Copied and made read-only on construction.
        attributes: Identifier to text -- ``"fiscal_period"``,
            ``"headline"``. Copied and made read-only on construction.
        revision: ``0`` as first reported; a correction of the same event is a
            second event with a higher revision, never an edit of the first.
        record_id: The derived, content-addressed identity, computed on
            construction.

    Raises:
        AltDataInputError: If the type or a key is not an identifier, the
            subject or an attribute value is not a label, a measurement is not a
            finite ``Decimal``, or the revision is negative.
    """

    event_type: str
    subject: str
    stamp: PointInTimeStamp
    source: ObservationSource
    measurements: Mapping[str, Decimal]
    attributes: Mapping[str, str]
    revision: int
    record_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "measurements", MappingProxyType(dict(self.measurements)))
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
        require_identifier(self.event_type, "InformationEvent.event_type")
        require_label(self.subject, "InformationEvent.subject")
        require_revision(self.revision, "InformationEvent.revision")
        for name, value in self.measurements.items():
            require_identifier(name, "An event measurement's name")
            require_finite_value(value, f"Event measurement {name!r}")
        for name, text in self.attributes.items():
            require_identifier(name, "An event attribute's name")
            require_label(text, f"Event attribute {name!r}")
        object.__setattr__(self, "record_id", digest_lines(_event_lines(self)))

    @property
    def record_scheme(self) -> str:
        """The scheme this record kind's identities are derived under."""

        return INFORMATION_EVENT_KEY_SCHEME

    @property
    def category(self) -> str:
        """The first word of the event type: ``"earnings"`` for ``"earnings.release"``."""

        return self.event_type.split(".", 1)[0]

    @property
    def series_key(self) -> tuple[str, ...]:
        """What kind of event, from which source, about whom."""

        return (self.source.source_id, repr(self.source.version), self.event_type, self.subject)

    @property
    def vintage_key(self) -> tuple[str, ...]:
        """The series and the occurrence instant -- what corrections of one event share."""

        return (*self.series_key, repr(self.stamp.observed_at))

    def measurement(self, name: str) -> Decimal:
        """The named measurement, or a refusal naming what the event does carry.

        Raises:
            AltDataInputError: If the event carries no measurement by that name.
                A missing figure is not zero.
        """

        found = self.measurements.get(name)
        if found is None:
            raise AltDataInputError(
                f"{self.event_type} for {self.subject!r} carries no measurement {name!r}; it "
                f"carries {sorted(self.measurements)}. A missing figure is not zero."
            )
        return found


def canonical_event_key(event: InformationEvent) -> str:
    """Render the canonical key an event's identity is derived from."""

    return "\n".join(_event_lines(event))


def _event_lines(event: InformationEvent) -> list[str]:
    return [
        INFORMATION_EVENT_KEY_SCHEME,
        f"event_type={event.event_type!r}",
        f"subject={event.subject!r}",
        f"revision={event.revision!r}",
        "measurements",
        *(f"{name!r}={str(event.measurements[name])!r}" for name in sorted(event.measurements)),
        "attributes",
        *(f"{name!r}={event.attributes[name]!r}" for name in sorted(event.attributes)),
        *event.source.record_lines,
        *stamp_lines(event.stamp),
    ]


def surprise(event: InformationEvent, actual: str, expected: str) -> Decimal:
    """``actual - expected``, from two measurements the caller names.

    Raises:
        AltDataInputError: If either measurement is absent.
    """

    return event.measurement(actual) - event.measurement(expected)


def restrict_measured(
    event_set: ObservationSet[InformationEvent], measurement: str
) -> ObservationSet[InformationEvent]:
    """The events that carry ``measurement``, as a derived set with its lineage.

    What a caller does before reading a measurement across a series in which
    some events lack it -- an earnings release with no published consensus.
    The events without it are left out *visibly*, as a recorded transformation,
    rather than skipped silently by whatever reads the set next.

    Raises:
        AltDataInputError: If ``measurement`` is not an identifier, or no event
            carries it.
    """

    require_identifier(measurement, "The restricted measurement")
    kept = [record for record in event_set.records if measurement in record.measurements]
    return _restricted(event_set, kept, "restrict_measured", f"measurement={measurement!r}")
