"""A versioned set of point-in-time records, and what was visible in it when.

An :class:`ObservationSet` is to external information what
:class:`~alphalab.data.dataset.Dataset` is to a price series: an immutable
collection with a derived identity, read from a named source, that a study names
by version and a result can be traced back to. It holds records of one kind --
generic observations, information events, or statement line items -- and all
three are queried the same way, through an :class:`ObservationView` that answers
"what was knowable at this instant?".

Identity is derived, and transformation derives a new one
---------------------------------------------------------

:func:`derive_set_version` hashes the set's name, record kind, the source's
identity and the digest of the bytes it was read from, its lineage, and every
record's content-addressed identity, following
:func:`~alphalab.data.provenance.derive_dataset_version`. Restricting a set --
to some subjects, a window, what was known by an instant, the originals --
never edits it: :func:`restrict_subjects`, :func:`restrict_observed`,
:func:`known_by` and :func:`originals_only` each derive a *new* set whose
``parent_version`` names the one it came from and whose ``transformations``
record what was done, the rule ``derive_transformed_version`` applies to
datasets.

Vintages are checked, not trusted
---------------------------------

Records sharing a ``vintage_key`` are revisions of one figure. Two of them
claiming the same revision number are refused, and so is a revision that
became available *before* the revision it revises -- a restatement cannot be
published ahead of what it restates, and a set asserting one is a set whose
point-in-time claims cannot all be true.

What a view answers
-------------------

A view pairs a set with a :class:`~alphalab.common.point_in_time.VisibilityRule`
and indexes it once -- globally, per series and per figure -- so that a question
asked at thousands of instants costs a bisection or a handful of vintages each
rather than a scan. Every answer
comes in one of two forms: a value, or an absence the caller can explain --
:meth:`ObservationView.require_vintage` refuses with the instant the figure
*will* become knowable, or with the fact that nobody established when it did.
Records whose availability is ``UNKNOWN`` are never returned by any selection;
they are counted, so a study can report how much of its source it could not
verify.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto
from itertools import pairwise
from types import MappingProxyType
from typing import Final, Protocol

from alphalab.alt_data.exceptions import AltDataInputError, PointInTimeError
from alphalab.alt_data.identity import digest_lines
from alphalab.alt_data.source import ObservationSource
from alphalab.alt_data.validation import (
    require_finite_instant,
    require_identifier,
    require_label,
)
from alphalab.common.point_in_time import (
    PointInTimeIndex,
    StampedRecord,
    VisibilityRule,
)

__all__ = [
    "OBSERVATION_SET_KEY_SCHEME",
    "ObservationSet",
    "ObservationView",
    "PointInTimeSelection",
    "SetRecord",
    "SetTransformation",
    "VintagePolicy",
    "build_observation_set",
    "canonical_set_key",
    "derive_set_version",
    "known_by",
    "latest_vintages",
    "original_vintages",
    "originals_only",
    "restrict_observed",
    "restrict_subjects",
    "verify_observation_set",
]

#: Scheme tag, and the first line of every canonical observation-set key.
OBSERVATION_SET_KEY_SCHEME: Final = "alphalab.observation_set.v1"


class SetRecord(StampedRecord, Protocol):
    """What a record must offer to live in an :class:`ObservationSet`.

    :class:`~alphalab.alt_data.observation.ExternalObservation`,
    :class:`~alphalab.alt_data.information.InformationEvent` and
    :class:`~alphalab.alt_data.fundamentals.FundamentalObservation` satisfy it
    as they stand. Structural, so this module imports none of them.
    """

    @property
    def subject(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def series_key(self) -> tuple[str, ...]: ...

    @property
    def vintage_key(self) -> tuple[str, ...]: ...

    @property
    def source(self) -> ObservationSource: ...

    @property
    def record_scheme(self) -> str: ...


class VintagePolicy(Enum):
    """Which revision of a figure research reads at an instant.

    Two members, and neither is hindsight. There is deliberately no "latest
    revision in the set" policy: reading a restatement at an instant before it
    was published is the look-ahead this module exists to prevent. Comparing
    what was first published with what was eventually restated is an analysis
    over the whole set, and :func:`~alphalab.alt_data.fundamentals.restatements`
    performs it under that name.
    """

    #: The highest revision knowable at the research instant: what a researcher
    #: at that instant would have read.
    AS_KNOWN = auto()

    #: The value as first published, once it was published. Later revisions are
    #: ignored even after they become knowable. A period whose original is not in
    #: the set has no original to read.
    ORIGINAL = auto()


@dataclass(frozen=True, slots=True)
class SetTransformation:
    """One transformation that derived a set from its parent.

    Attributes:
        operation: What was done, as an identifier -- ``"restrict_subjects"``.
        detail: Its parameters in words, rendered deterministically.
        records_before: How many records the parent held.
        records_after: How many the derived set holds.
    """

    operation: str
    detail: str
    records_before: int
    records_after: int

    def __post_init__(self) -> None:
        require_identifier(self.operation, "SetTransformation.operation")
        require_label(self.detail, "SetTransformation.detail")
        if self.records_before < self.records_after or self.records_after < 1:
            raise AltDataInputError(
                f"{self.operation} reports {self.records_before} records before and "
                f"{self.records_after} after; a restriction keeps at least one and adds none."
            )


def canonical_set_key(
    name: str,
    kind: str,
    source: ObservationSource,
    parent_version: str | None,
    transformations: Iterable[SetTransformation],
    record_ids: Iterable[str],
) -> str:
    """Render the canonical key a set's version is derived from.

    Record identities are sorted, because a set is a set: the order records
    arrived in is not part of what it contains. Transformations are rendered in
    application order, because restricting then snapshotting and snapshotting
    then restricting are different pipelines.
    """

    return "\n".join(
        [
            OBSERVATION_SET_KEY_SCHEME,
            f"name={name!r}",
            f"kind={kind!r}",
            *source.set_lines,
            f"parent={parent_version!r}",
            "transformations",
            *(
                f"{step.operation!r}:{step.detail!r}:{step.records_before!r}"
                f"->{step.records_after!r}"
                for step in transformations
            ),
            "records",
            *sorted(record_ids),
        ]
    )


def derive_set_version(
    name: str,
    kind: str,
    source: ObservationSource,
    parent_version: str | None,
    transformations: Iterable[SetTransformation],
    record_ids: Iterable[str],
) -> str:
    """``"<name>@<sha256 of the canonical key>"``. Derived, never minted."""

    key = canonical_set_key(name, kind, source, parent_version, transformations, record_ids)
    return f"{name}@{digest_lines([key])}"


def _check_vintages[T: SetRecord](records: Iterable[T]) -> None:
    by_vintage: dict[tuple[str, ...], dict[int, T]] = {}
    for record in records:
        revisions = by_vintage.setdefault(record.vintage_key, {})
        if record.revision in revisions:
            raise AltDataInputError(
                f"Two records claim revision {record.revision} of {record.vintage_key}: "
                f"{revisions[record.revision].record_id} and {record.record_id}. One figure "
                "cannot have two different values at the same revision."
            )
        revisions[record.revision] = record

    for vintage, revisions in by_vintage.items():
        ordered = sorted(revisions.items())
        for (earlier_revision, earlier), (later_revision, later) in pairwise(ordered):
            before = earlier.stamp.available_at
            after = later.stamp.available_at
            if before is not None and after is not None and after < before:
                raise AltDataInputError(
                    f"Revision {later_revision} of {vintage} became available at {after!r}, "
                    f"before revision {earlier_revision} at {before!r}. A revision cannot be "
                    "published ahead of the figure it revises."
                )


@dataclass(frozen=True, slots=True)
class ObservationSet[T: SetRecord]:
    """An immutable, versioned set of point-in-time records of one kind.

    Built by :func:`build_observation_set` or derived by one of the
    restrictions, which compute :attr:`version`. Constructing one by hand is
    possible, and :func:`verify_observation_set` will say whether its version
    matches its content.

    Attributes:
        name: What the set is called. Part of its identity.
        kind: The record scheme every record shares -- which kind of record
            the set holds.
        source: Where the records came from.
        records: The records, ordered by ``(observed_at, record_id)``.
        parent_version: The set this one was derived from, or ``None``.
        transformations: Every transformation from the original set to this
            one, in application order.
        version: The derived identity.

    Raises:
        AltDataInputError: If the name is blank or contains ``"@"``; if the set
            is empty; if a record's kind or source differs from the set's; if a
            record appears twice; if the records are out of order; or if two
            vintages of one figure are inconsistent.
    """

    name: str
    kind: str
    source: ObservationSource
    records: tuple[T, ...]
    parent_version: str | None
    transformations: tuple[SetTransformation, ...]
    version: str

    def __post_init__(self) -> None:
        require_label(self.name, "ObservationSet.name")
        if "@" in self.name:
            raise AltDataInputError(
                f"A set name may not contain '@': {self.name!r}. The character separates the "
                "name from the digest in a set version."
            )
        if not self.records:
            raise AltDataInputError(
                f"Set {self.name!r} holds no records. An empty set would make every selection "
                "from it vacuously complete."
            )
        seen: set[str] = set()
        previous: tuple[float, str] | None = None
        for record in self.records:
            if record.record_scheme != self.kind:
                raise AltDataInputError(
                    f"Set {self.name!r} holds {self.kind!r} records and was given a "
                    f"{record.record_scheme!r} record ({record.record_id})."
                )
            if record.source != self.source:
                raise AltDataInputError(
                    f"Record {record.record_id} comes from {record.source.source_id!r} "
                    f"version {record.source.version!r}, and set {self.name!r} is read from "
                    f"{self.source.source_id!r} version {self.source.version!r}."
                )
            if record.record_id in seen:
                raise AltDataInputError(
                    f"Record {record.record_id} appears twice in set {self.name!r}."
                )
            seen.add(record.record_id)
            position = (record.stamp.observed_at, record.record_id)
            if previous is not None and position < previous:
                raise AltDataInputError(
                    f"Set {self.name!r} is not in (observed_at, record_id) order. Build it with "
                    "build_observation_set, which orders it."
                )
            previous = position
        _check_vintages(self.records)
        if not self.version.startswith(f"{self.name}@"):
            raise AltDataInputError(
                f"Set {self.name!r} carries version {self.version!r}, which does not name it."
            )

    def __len__(self) -> int:
        return len(self.records)

    @property
    def record_ids(self) -> tuple[str, ...]:
        """Every record's identity, in the set's order."""

        return tuple(record.record_id for record in self.records)

    @property
    def subjects(self) -> tuple[str, ...]:
        """Every subject present, sorted."""

        return tuple(sorted({record.subject for record in self.records}))

    @property
    def unverified(self) -> int:
        """How many records carry no established availability instant."""

        return sum(1 for record in self.records if not record.stamp.verifiable)

    def view(self, visibility: VisibilityRule) -> ObservationView[T]:
        """Index this set for point-in-time queries under ``visibility``."""

        return ObservationView.of(self, visibility)


def _derive[T: SetRecord](
    name: str,
    source: ObservationSource,
    records: Iterable[T],
    parent_version: str | None,
    transformations: tuple[SetTransformation, ...],
) -> ObservationSet[T]:
    ordered = sorted(records, key=lambda record: (record.stamp.observed_at, record.record_id))
    if not ordered:
        raise AltDataInputError(
            f"Set {name!r} would hold no records. An empty set would make every selection "
            "from it vacuously complete."
        )
    kind = ordered[0].record_scheme
    return ObservationSet(
        name=name,
        kind=kind,
        source=source,
        records=tuple(ordered),
        parent_version=parent_version,
        transformations=transformations,
        version=derive_set_version(
            name,
            kind,
            source,
            parent_version,
            transformations,
            (record.record_id for record in ordered),
        ),
    )


def build_observation_set[T: SetRecord](
    name: str, records: Iterable[T], source: ObservationSource
) -> ObservationSet[T]:
    """Build a set with its version computed.

    Records may arrive in any order; the set orders them, and its version does
    not depend on the order they arrived in.

    Raises:
        AltDataInputError: For every reason :class:`ObservationSet` refuses.
    """

    require_label(name, "An observation set's name")
    return _derive(name, source, records, None, ())


def verify_observation_set[T: SetRecord](obs_set: ObservationSet[T]) -> bool:
    """Whether the set's version still matches its content and lineage."""

    return obs_set.version == derive_set_version(
        obs_set.name,
        obs_set.kind,
        obs_set.source,
        obs_set.parent_version,
        obs_set.transformations,
        obs_set.record_ids,
    )


def _restricted[T: SetRecord](
    obs_set: ObservationSet[T], kept: list[T], operation: str, detail: str
) -> ObservationSet[T]:
    if not kept:
        raise AltDataInputError(
            f"{operation} ({detail}) leaves set {obs_set.name!r} empty. An empty set would make "
            "every selection from it vacuously complete."
        )
    step = SetTransformation(operation, detail, len(obs_set.records), len(kept))
    return _derive(
        obs_set.name,
        obs_set.source,
        kept,
        obs_set.version,
        (*obs_set.transformations, step),
    )


def restrict_subjects[T: SetRecord](
    obs_set: ObservationSet[T], subjects: Iterable[str]
) -> ObservationSet[T]:
    """The records about ``subjects``, as a derived set.

    Raises:
        AltDataInputError: If no subject is named, or none of them is present.
    """

    wanted = sorted(set(subjects))
    if not wanted:
        raise AltDataInputError("A subject restriction must name at least one subject.")
    for subject in wanted:
        require_label(subject, "A restricted subject")
    keep = set(wanted)
    kept = [record for record in obs_set.records if record.subject in keep]
    return _restricted(obs_set, kept, "restrict_subjects", f"subjects={wanted!r}")


def restrict_observed[T: SetRecord](
    obs_set: ObservationSet[T], start: float, end: float
) -> ObservationSet[T]:
    """The records observed in ``[start, end)``, as a derived set.

    Raises:
        AltDataInputError: If the window is empty or runs backwards, or holds
            no record.
    """

    require_finite_instant(start, "start")
    require_finite_instant(end, "end")
    if end <= start:
        raise AltDataInputError(f"The window [{start!r}, {end!r}) is empty.")
    kept = [record for record in obs_set.records if start <= record.stamp.observed_at < end]
    return _restricted(obs_set, kept, "restrict_observed", f"[{start!r}, {end!r})")


def known_by[T: SetRecord](
    obs_set: ObservationSet[T], as_of: float, visibility: VisibilityRule
) -> ObservationSet[T]:
    """Everything knowable at ``as_of`` under ``visibility``, as a derived set.

    The point-in-time snapshot of a set: what a study frozen at ``as_of`` could
    have read, every vintage included, with records of unknown availability
    left out because they cannot be shown to have been knowable.

    Raises:
        AltDataInputError: If nothing was knowable by then.
    """

    require_finite_instant(as_of, "as_of")
    kept = [record for record in obs_set.records if record.stamp.is_visible(as_of, visibility)]
    return _restricted(obs_set, kept, "known_by", f"as_of={as_of!r} visibility={visibility.name}")


def originals_only[T: SetRecord](obs_set: ObservationSet[T]) -> ObservationSet[T]:
    """Only the values as first published (revision 0), as a derived set.

    Raises:
        AltDataInputError: If the set holds no original.
    """

    kept = [record for record in obs_set.records if record.revision == 0]
    return _restricted(obs_set, kept, "originals_only", "revision=0")


# --------------------------------------------------------------------------- #
# Vintages
# --------------------------------------------------------------------------- #


def _ordering(record: SetRecord) -> tuple[float, tuple[str, ...], int]:
    return (record.stamp.observed_at, record.vintage_key, record.revision)


def latest_vintages[T: SetRecord](records: Iterable[T]) -> tuple[T, ...]:
    """For each vintage key, the record with the highest revision.

    Ordered by ``(observed_at, vintage_key)``. Applied to a point-in-time
    selection this is :attr:`VintagePolicy.AS_KNOWN`: the highest revision
    *knowable then*, not the highest in the set.
    """

    best: dict[tuple[str, ...], T] = {}
    for record in records:
        found = best.get(record.vintage_key)
        if found is None or record.revision > found.revision:
            best[record.vintage_key] = record
    return tuple(sorted(best.values(), key=_ordering))


def original_vintages[T: SetRecord](records: Iterable[T]) -> tuple[T, ...]:
    """For each vintage key, the record as first published (revision 0), if present."""

    return tuple(sorted((record for record in records if record.revision == 0), key=_ordering))


@dataclass(frozen=True, slots=True)
class PointInTimeSelection[T: SetRecord]:
    """What one set made visible at one instant, and how much it held back.

    Attributes:
        set_version: The set selected from.
        as_of: The research instant.
        visibility: The rule the selection applied.
        records: Every record knowable at ``as_of``, all vintages, in knowledge
            order.
        pending: Records that become knowable after ``as_of``.
        unverified: Records whose knowledge instant could not be established
            under ``visibility``. Never selected, always counted.
    """

    set_version: str
    as_of: float
    visibility: VisibilityRule
    records: tuple[T, ...]
    pending: int
    unverified: int

    def __len__(self) -> int:
        return len(self.records)

    def vintages(self, policy: VintagePolicy) -> tuple[T, ...]:
        """One record per figure, chosen by ``policy`` from what was visible."""

        if policy is VintagePolicy.AS_KNOWN:
            return latest_vintages(self.records)
        return original_vintages(self.records)


@dataclass(frozen=True, slots=True)
class ObservationView[T: SetRecord]:
    """A set indexed for point-in-time questions under one visibility rule.

    Attributes:
        set_version: The set this view reads.
        visibility: The rule every answer applies.
        index: The whole set, ordered by knowledge instant.
        series: Each series key to its own index, so a question about one
            series never scans another.
        figures: Each vintage key to that figure's knowable vintages, in
            knowledge order, so reading one figure never scans its series'
            history. Records whose availability is unknown are not in it.
    """

    set_version: str
    visibility: VisibilityRule
    index: PointInTimeIndex[T]
    series: Mapping[tuple[str, ...], PointInTimeIndex[T]]
    figures: Mapping[tuple[str, ...], tuple[T, ...]]

    @classmethod
    def of(cls, obs_set: ObservationSet[T], visibility: VisibilityRule) -> ObservationView[T]:
        """Index ``obs_set`` once: globally, per series and per figure."""

        index = PointInTimeIndex.build(obs_set.records, visibility)
        grouped: dict[tuple[str, ...], list[T]] = {}
        for record in obs_set.records:
            grouped.setdefault(record.series_key, []).append(record)
        figures: dict[tuple[str, ...], list[T]] = {}
        for record in index.records:
            figures.setdefault(record.vintage_key, []).append(record)
        return cls(
            set_version=obs_set.version,
            visibility=visibility,
            index=index,
            series=MappingProxyType(
                {
                    key: PointInTimeIndex.build(rows, visibility)
                    for key, rows in sorted(grouped.items())
                }
            ),
            figures=MappingProxyType({key: tuple(rows) for key, rows in figures.items()}),
        )

    @property
    def series_keys(self) -> tuple[tuple[str, ...], ...]:
        """Every series in the set, sorted."""

        return tuple(self.series)

    def select(self, as_of: float) -> PointInTimeSelection[T]:
        """Everything knowable at ``as_of``, with what was held back counted."""

        visible = self.index.visible_as_of(as_of)
        return PointInTimeSelection(
            set_version=self.set_version,
            as_of=as_of,
            visibility=self.visibility,
            records=visible,
            pending=len(self.index.records) - len(visible),
            unverified=len(self.index.unverified),
        )

    def _series(self, series_key: tuple[str, ...]) -> PointInTimeIndex[T]:
        found = self.series.get(series_key)
        if found is None:
            raise AltDataInputError(
                f"Set {self.set_version} holds no series {series_key}. It holds "
                f"{len(self.series)} series; list them with series_keys."
            )
        return found

    def visible_in_series(self, series_key: tuple[str, ...], as_of: float) -> tuple[T, ...]:
        """Every vintage in one series knowable at ``as_of``, in knowledge order.

        Raises:
            AltDataInputError: If the set holds no such series.
        """

        return self._series(series_key).visible_as_of(as_of)

    def latest_in_series(
        self, series_key: tuple[str, ...], as_of: float, policy: VintagePolicy
    ) -> T | None:
        """The most recent figure in a series knowable at ``as_of``, or ``None``.

        "Most recent" is by observation instant -- the newest period, not the
        newest publication -- and within it the revision ``policy`` selects. A
        restatement of an old period published today does not displace this
        quarter's figure.

        Raises:
            AltDataInputError: If the set holds no such series.
        """

        visible = self.visible_in_series(series_key, as_of)
        chosen = (
            latest_vintages(visible)
            if policy is VintagePolicy.AS_KNOWN
            else original_vintages(visible)
        )
        return chosen[-1] if chosen else None

    def latest_timeline(
        self, series_key: tuple[str, ...], policy: VintagePolicy
    ) -> tuple[tuple[float, T], ...]:
        """How :meth:`latest_in_series`'s answer changed as the series became knowable.

        One ``(instant, record)`` entry for each knowledge instant at which the
        answer changed, in time order. The answer at any research instant is the
        last entry at or before it -- one bisection -- so a caller sampling a
        series at thousands of instants reads it in one linear pass rather than
        rescanning it at each. The same ordering as :meth:`latest_in_series`,
        computed incrementally: what is visible only ever grows, so the most
        recent figure can only be displaced by a newer period or a higher
        revision of the same one.

        Raises:
            AltDataInputError: If the set holds no such series.
        """

        index = self._series(series_key)
        records = index.records
        instants = index.known_instants
        timeline: list[tuple[float, T]] = []
        best: T | None = None
        best_order: tuple[float, tuple[str, ...], int] | None = None
        position = 0
        while position < len(records):
            instant = instants[position]
            while position < len(records) and instants[position] == instant:
                record = records[position]
                position += 1
                if policy is VintagePolicy.ORIGINAL and record.revision != 0:
                    continue
                order = _ordering(record)
                if best_order is None or order > best_order:
                    best, best_order = record, order
            if best is not None and (not timeline or timeline[-1][1] is not best):
                timeline.append((instant, best))
        return tuple(timeline)

    def vintage_as_of(
        self, vintage_key: tuple[str, ...], as_of: float, policy: VintagePolicy
    ) -> T | None:
        """One figure as ``policy`` reads it at ``as_of``, or ``None`` if not yet knowable.

        Reads only that figure's vintages -- a handful, however long its series
        -- through :meth:`~alphalab.common.point_in_time.PointInTimeStamp.is_visible`,
        the rule every index applies, so a study reading one figure at each of
        thousands of instants never rescans the series' history.
        """

        candidates = [
            record
            for record in self.figures.get(vintage_key, ())
            if record.stamp.is_visible(as_of, self.visibility)
        ]
        if policy is VintagePolicy.ORIGINAL:
            originals = [record for record in candidates if record.revision == 0]
            return originals[0] if originals else None
        return max(candidates, key=lambda record: record.revision) if candidates else None

    def require_vintage(
        self, vintage_key: tuple[str, ...], as_of: float, policy: VintagePolicy
    ) -> T:
        """One figure as ``policy`` reads it at ``as_of``, or a refusal that explains.

        Raises:
            PointInTimeError: If the figure was not yet knowable at ``as_of`` --
                naming the instant it becomes knowable -- or if its availability
                was never established, or if the set holds no such figure.
        """

        found = self.vintage_as_of(vintage_key, as_of, policy)
        if found is not None:
            return found
        series = self.series.get(vintage_key[:-1])
        if series is not None:
            later = [
                instant
                for record, instant in zip(series.records, series.known_instants, strict=True)
                if record.vintage_key == vintage_key
                and (policy is VintagePolicy.AS_KNOWN or record.revision == 0)
            ]
            if later:
                raise PointInTimeError(
                    f"{vintage_key} is not knowable at {as_of!r} under "
                    f"{self.visibility.name}: it becomes knowable at {min(later)!r}. Reading it "
                    "earlier would be reading the future."
                )
            if any(record.vintage_key == vintage_key for record in series.unverified):
                raise PointInTimeError(
                    f"{vintage_key} is in set {self.set_version} but its availability was never "
                    f"established under {self.visibility.name}, so it cannot be shown to have "
                    f"been knowable at {as_of!r}. It is not assumed to have been."
                )
        raise PointInTimeError(
            f"Set {self.set_version} holds no figure {vintage_key} readable under {policy.name}."
        )
