"""Observations that arrive over time, and the instant each may be acted on.

An :class:`~alphalab.alt_data.observation_set.ObservationSet` is built whole: a
study reads a vendor file, builds the set once and asks it questions. A live
session -- or a backtest that wants its strategies to *receive* external
information as it becomes knowable rather than look it up -- needs the other two
halves (ledger OFE-009, OFE-011):

* :class:`ObservationStream` grows record by record, checking each arrival
  against what it already holds -- the same refusals the set makes (one kind,
  one source, no record twice, no revision claimed twice, no revision published
  ahead of the one it revises) at the cost of the arrival and its figure's own
  revisions, never of everything received so far. :meth:`ObservationStream.to_set`
  is the set those records make, with the version a set built from them whole
  would have: content decides identity, not the order it arrived in.
* :func:`delivery_schedule` puts a set's records in the order a strategy may
  learn them: each at its **knowledge instant** under a
  :class:`~alphalab.common.point_in_time.VisibilityRule`, ties broken by record
  identity, and every record whose instant nobody established counted and left
  out -- the rule every point-in-time selection here follows. The execution path
  delivers a schedule's entries (:meth:`~alphalab.runtime.run.RunEngine.deliver_observation`).

Nothing here reads a clock or contacts anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Final

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.observation_set import (
    ObservationSet,
    SetRecord,
    build_observation_set,
)
from alphalab.alt_data.source import ObservationSource
from alphalab.alt_data.validation import require_label
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.common.point_in_time import VisibilityRule

__all__ = [
    "DeliverySchedule",
    "ObservationDelivery",
    "ObservationStream",
    "delivery_schedule",
]

_NO_REVISIONS: Final[PersistentMap[int, float | None]] = PersistentMap()


@dataclass(frozen=True, slots=True)
class ObservationStream[T: SetRecord]:
    """A point-in-time record set that grows as records arrive (ledger OFE-011).

    Immutable, like everything else: :meth:`append` returns a new stream and
    leaves this one as it was. Open one with :meth:`open`.

    Attributes:
        name: What the set it becomes is called.
        source: Where its records come from; every record must say so.
        kind: The record scheme its records share, fixed by the first one, or
            ``None`` while it is empty.
        records: The records, in arrival order.
    """

    name: str
    source: ObservationSource
    kind: str | None = None
    records: AppendOnlyLog[T] = field(default_factory=AppendOnlyLog)
    _ids: PersistentMap[str, None] = field(default_factory=PersistentMap)
    _vintages: PersistentMap[tuple[str, ...], PersistentMap[int, float | None]] = field(
        default_factory=PersistentMap
    )

    @classmethod
    def open(cls, name: str, source: ObservationSource) -> ObservationStream[T]:
        """An empty stream that will become the set ``name``, read from ``source``.

        Raises:
            AltDataInputError: If the name is blank or contains ``"@"``.
        """

        require_label(name, "An observation stream's name")
        if "@" in name:
            raise AltDataInputError(
                f"A set name may not contain '@': {name!r}. The character separates the name "
                "from the digest in a set version."
            )
        return cls(name, source)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[T]:
        return iter(self.records)

    def append(self, records: Iterable[T]) -> ObservationStream[T]:
        """This stream with ``records`` received, in the order given.

        Each arrival is checked against what the stream holds and against the
        arrivals before it in the same call; the first refusal raises, and the
        stream it was called on is unchanged.

        Raises:
            AltDataInputError: If a record's kind or source differs from the
                stream's, a record arrives twice, a revision of a figure is
                claimed twice, or a revision is available before the revision
                it revises (or after one that revises it).
        """

        kind = self.kind
        log = self.records
        ids = self._ids
        vintages = self._vintages
        for record in records:
            if kind is None:
                kind = record.record_scheme
            elif record.record_scheme != kind:
                raise AltDataInputError(
                    f"Stream {self.name!r} holds {kind!r} records and received a "
                    f"{record.record_scheme!r} record ({record.record_id})."
                )
            if record.source != self.source:
                raise AltDataInputError(
                    f"Record {record.record_id} comes from {record.source.source_id!r} "
                    f"version {record.source.version!r}, and stream {self.name!r} is read from "
                    f"{self.source.source_id!r} version {self.source.version!r}."
                )
            if record.record_id in ids:
                raise AltDataInputError(
                    f"Record {record.record_id} arrived twice in stream {self.name!r}."
                )
            revisions = vintages.get(record.vintage_key, _NO_REVISIONS)
            _check_arrival(record, revisions)
            ids = ids.set(record.record_id, None)
            vintages = vintages.set(
                record.vintage_key, revisions.set(record.revision, record.stamp.available_at)
            )
            log = log.append(record)
        return ObservationStream(self.name, self.source, kind, log, ids, vintages)

    def to_set(self) -> ObservationSet[T]:
        """The set these records make: ordered, versioned, and checked whole once more.

        Its version is the one :func:`~alphalab.alt_data.observation_set.build_observation_set`
        derives from the same records, whatever order they arrived in.

        Raises:
            AltDataInputError: If the stream holds no record.
        """

        return build_observation_set(self.name, self.records, self.source)


def _check_arrival(record: SetRecord, revisions: PersistentMap[int, float | None]) -> None:
    """Refuse a revision claimed twice, or published out of order with its neighbours."""

    revision = record.revision
    if revision in revisions:
        raise AltDataInputError(
            f"Two records claim revision {revision} of {record.vintage_key}; the second is "
            f"{record.record_id}. One figure cannot have two values at one revision."
        )
    available = record.stamp.available_at
    if available is None:
        return
    for other, at in revisions.items():
        if at is None:
            continue
        if other < revision and at > available:
            raise AltDataInputError(
                f"Revision {revision} of {record.vintage_key} became available at {available!r}, "
                f"before revision {other} at {at!r}. A revision cannot be published ahead of "
                "the figure it revises."
            )
        if other > revision and at < available:
            raise AltDataInputError(
                f"Revision {revision} of {record.vintage_key} became available at {available!r}, "
                f"after revision {other} at {at!r} that revises it."
            )


@dataclass(frozen=True, slots=True)
class ObservationDelivery[T: SetRecord]:
    """One record, at the instant a strategy may first know it.

    Attributes:
        known_at: The record's knowledge instant under the schedule's rule.
        delivery_id: ``"<set version>:<record id>"`` -- which set, which record.
        record: The record itself.
    """

    known_at: float
    delivery_id: str
    record: T

    @property
    def order_key(self) -> tuple[float, str]:
        """What deliveries are ordered by: the instant, then the identity."""

        return (self.known_at, self.delivery_id)


@dataclass(frozen=True, slots=True)
class DeliverySchedule[T: SetRecord]:
    """A set's records in the order they may be acted on, and what was held back.

    Attributes:
        set_version: The set the schedule was made from.
        visibility: Whose clock decided each knowledge instant.
        deliveries: Every record whose knowledge instant is established, in
            ``(known_at, delivery_id)`` order.
        unverified: Records left out because no knowledge instant could be
            established for them under ``visibility``. Never delivered, always
            counted.
    """

    set_version: str
    visibility: VisibilityRule
    deliveries: tuple[ObservationDelivery[T], ...]
    unverified: int

    def __len__(self) -> int:
        return len(self.deliveries)

    def __iter__(self) -> Iterator[ObservationDelivery[T]]:
        return iter(self.deliveries)


def delivery_schedule[T: SetRecord](
    obs_set: ObservationSet[T], visibility: VisibilityRule
) -> DeliverySchedule[T]:
    """Every record of ``obs_set`` at the instant it becomes knowable under ``visibility``.

    A record whose availability nobody established -- or, under ``INGESTION``,
    whose arrival was not recorded -- has no instant to be delivered at, and is
    counted rather than placed somewhere convenient.
    """

    deliveries: list[ObservationDelivery[T]] = []
    unverified = 0
    for record in obs_set.records:
        known = record.stamp.known_at(visibility)
        if known is None:
            unverified += 1
            continue
        deliveries.append(
            ObservationDelivery(known, f"{obs_set.version}:{record.record_id}", record)
        )
    deliveries.sort(key=lambda delivery: delivery.order_key)
    return DeliverySchedule(obs_set.version, visibility, tuple(deliveries), unverified)
