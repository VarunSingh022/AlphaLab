"""Complete, restorable snapshots of a paused replay (v4.1, ledger FUT-001).

The outermost of three envelopes
--------------------------------
:mod:`alphalab.runtime.snapshot` captures the execution core,
:mod:`alphalab.runtime.run_snapshot` the run around it, and this module the
replay cursor around the run. The run's payload is nested here exactly as
:func:`alphalab.runtime.run_snapshot.capture` writes it and is read by that
module's own decoder, so neither inner schema moves and an older run payload
nested in a replay envelope is upgraded by the run's own history. Until v4.1 a
replay had no envelope at all: its cursor and the cursor's identifier stream
were recorded nowhere (ADR-0029, ADR-0030), and an interrupted replay restarted
from its dataset.

What is recorded, and what is supplied back
-------------------------------------------
The cursor -- its session, status, position, record-index clock and lifecycle
log -- and the position of its own identifier stream are recorded. The
dataset's records are **not**: they are the replay's input, as the live objects
in :class:`~alphalab.runtime.run_snapshot.RunObjects` are the run's, and a
caller supplies them back to :func:`restore`. What *is* recorded about them is
enough to refuse any other input: the dataset's id, its record count, a SHA-256
digest of every record in its canonical JSON form (:func:`dataset_digest`), and
the ids of the last record taken and the next one due. A missing, extra,
duplicated, reordered or altered record, another dataset, or a payload whose
cursor and run disagree about how far they have read, is refused by name --
never continued from, because continuing would duplicate or omit work.

Where a payload is kept is the caller's: the payload is a ``str``, and
:class:`~alphalab.persistence.run_store.FileRunStateStore` keeps one atomically
and refuses truncated or altered bytes by digest.

Resuming
--------
:func:`restore` reconstructs a :class:`~alphalab.backtesting.replay.PausedReplay`
and processes nothing; :meth:`~alphalab.backtesting.replay.ReplayBacktest.resume`
continues it::

    paused = restore(from_primitives(deserialize(payload)), objects, dataset)
    result = ReplayBacktest.resume(paused, context_factory)
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay import PausedReplay, cursor_seed
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.common.ids import IdStreamPosition
from alphalab.persistence.decode import (
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_optional_str,
    as_sequence,
    as_str,
    require,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import serialize
from alphalab.persistence.upgrade import SchemaHistory
from alphalab.replay.events import (
    ReplayAdvanced,
    ReplayPaused,
    ReplayResumed,
    ReplayStarted,
    ReplaySystemEvent,
)
from alphalab.replay.session import ReplaySession
from alphalab.replay.state import ReplayState, ReplayStatus
from alphalab.runtime.run import ExecutionMode
from alphalab.runtime.run_snapshot import RunObjects, RunSnapshot
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.run_snapshot import restore as restore_run

# The identifier-position decoder belongs to the pipeline snapshot, which first
# wrote one. Imported rather than repeated, as run_snapshot imports its decoders.
from alphalab.runtime.snapshot import _id_position

__all__ = [
    "REPLAY_SCHEMA_HISTORY",
    "REPLAY_SNAPSHOT_SCHEMA",
    "ReplayEventRecord",
    "ReplaySnapshot",
    "capture",
    "dataset_digest",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. New in v4.1, so it reads nothing
#: older; a module-local literal for the reason ``RUN_SNAPSHOT_SCHEMA`` is one.
REPLAY_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "replay"

#: How every replay payload a release has written is read by this one. Version 1
#: is the first, so there is nothing yet to upgrade.
REPLAY_SCHEMA_HISTORY = SchemaHistory(_SUBSYSTEM, REPLAY_SNAPSHOT_SCHEMA)


@dataclass(frozen=True, slots=True)
class ReplayEventRecord:
    """One entry of the cursor's lifecycle log, tagged with its class name.

    The log holds several event types; without the tag a decoder could not tell
    a ``ReplayPaused`` from a ``ReplayResumed``, whose fields are the same.
    """

    event_type: str
    event: ReplaySystemEvent


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    """Complete, JSON-serializable projection of a :class:`PausedReplay`.

    Attributes:
        run: The run, as :func:`alphalab.runtime.run_snapshot.capture` writes it.
        session: The cursor's session: the dataset's id and its first and last
            instants.
        status: The cursor's status -- ``PAUSED`` in every payload this writes.
        current_index: Records the cursor has taken.
        current_timestamp: The cursor's replay clock.
        real_start_time: The cursor's record-index clock at its start.
        real_current_time: The cursor's record-index clock now.
        events: The cursor's lifecycle log, in order: every event, or the newest
            a declared retention kept.
        cursor_position: Where the cursor's own identifier stream had reached.
        records_total: How many records the dataset holds.
        dataset_digest: :func:`dataset_digest` of the dataset.
        last_event_id: The last record taken, or ``None`` before the first.
        next_event_id: The record due next.
        events_dropped: How many earlier lifecycle events a declared retention
            dropped (``RetentionPolicy.audit_events``).
    """

    run: RunSnapshot
    session: ReplaySession
    status: ReplayStatus
    current_index: int
    current_timestamp: float
    real_start_time: float
    real_current_time: float
    events: tuple[ReplayEventRecord, ...]
    cursor_position: IdStreamPosition
    records_total: int
    dataset_digest: str
    last_event_id: str | None
    next_event_id: str
    events_dropped: int = 0
    schema_version: int = REPLAY_SNAPSHOT_SCHEMA


def dataset_digest(dataset: MarketDataset) -> str:
    """SHA-256 over every record of ``dataset``, each in its canonical JSON form, in order.

    The fingerprint a replay payload holds its input to. Linear in the records
    and built from the encoder every payload uses, so it is the same on every
    host: a timestamp is written as Python's shortest round-trip ``repr`` and a
    ``Decimal`` with its exponent.
    """

    return _digest(dataset.records)


def _digest(records: Iterable[object]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(serialize(record).encode(DEFAULT_ENCODING))
        digest.update(b"\n")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(paused: PausedReplay) -> ReplaySnapshot:
    """Project ``paused`` into its complete serializable snapshot.

    Pure: nothing is minted and nothing is mutated, so capturing one paused
    replay twice gives equal snapshots. Linear in the run's recorded history
    and the dataset's length, as the run's own capture is.
    """

    cursor = paused.cursor
    records = cursor.events
    index = cursor.current_index
    return ReplaySnapshot(
        run=capture_run(paused.run),
        session=cursor.session,
        status=cursor.status,
        current_index=index,
        current_timestamp=cursor.current_timestamp,
        real_start_time=cursor.real_start_time,
        real_current_time=cursor.real_current_time,
        events=tuple(
            ReplayEventRecord(type(event).__name__, event) for event in cursor.system_events
        ),
        cursor_position=paused.cursor_position,
        records_total=len(records),
        dataset_digest=_digest(records),
        last_event_id=records[index - 1].event_id if index else None,
        next_event_id=records[index].event_id,
        events_dropped=cursor.system_events.dropped,
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _refuse(message: str) -> StateDecodeError:
    return StateDecodeError(f"The replay checkpoint cannot continue: {message}")


def _check_input(snapshot: ReplaySnapshot, dataset: MarketDataset) -> None:
    """The dataset supplied is the one the replay read, record for record."""

    session = snapshot.session
    if dataset.dataset_id != session.session_id:
        raise _refuse(
            f"it replays dataset {session.session_id!r}, and dataset {dataset.dataset_id!r} "
            "was supplied."
        )
    records = dataset.records
    if len(records) != snapshot.records_total:
        raise _refuse(
            f"dataset {dataset.dataset_id!r} held {snapshot.records_total} records when it "
            f"paused and holds {len(records)} now; a record is missing or was added."
        )
    if (dataset.start_time, dataset.end_time) != (session.start_time, session.end_time):
        raise _refuse(
            f"the dataset spanned {session.start_time} to {session.end_time} and spans "
            f"{dataset.start_time} to {dataset.end_time} now."
        )
    index = snapshot.current_index
    if not 0 <= index < len(records):
        raise _refuse(
            f"its cursor is at {index} of {len(records)} records; a paused replay is "
            "between two of them."
        )
    last = records[index - 1].event_id if index else None
    if last != snapshot.last_event_id or records[index].event_id != snapshot.next_event_id:
        raise _refuse(
            f"it paused after {snapshot.last_event_id!r} with {snapshot.next_event_id!r} next, "
            f"and the dataset has {last!r} then {records[index].event_id!r} at that "
            "position; resuming would duplicate or skip records."
        )
    found = dataset_digest(dataset)
    if found != snapshot.dataset_digest:
        raise _refuse(
            f"the records of dataset {dataset.dataset_id!r} are not the ones it read: "
            f"digest {snapshot.dataset_digest} when it paused, {found} now."
        )


def _check_cursor(snapshot: ReplaySnapshot) -> None:
    """The cursor and the run inside one payload describe one paused replay."""

    if snapshot.status is not ReplayStatus.PAUSED:
        raise _refuse(f"its cursor is {snapshot.status.name}, not PAUSED.")
    run = snapshot.run
    if run.mode is not ExecutionMode.REPLAY:
        raise _refuse(f"its run is a {run.mode.name} run, not a REPLAY run.")
    if run.source_id != snapshot.session.session_id:
        raise _refuse(
            f"its run read {run.source_id!r} and its cursor replays "
            f"{snapshot.session.session_id!r}."
        )
    index = snapshot.current_index
    taken = run.processed + len(run.skipped) + run.dropped.get("skipped", 0)
    if taken != index:
        raise _refuse(
            f"its cursor has taken {index} records and its run processed or skipped "
            f"{taken}; resuming would duplicate or omit {abs(taken - index)}."
        )

    expected_seed = cursor_seed(run.seed)
    position = snapshot.cursor_position
    if position.seed != expected_seed:
        raise _refuse(
            f"its cursor's identifier stream is seeded {position.seed!r}, and a run seeded "
            f"{run.seed!r} replays with {expected_seed!r}."
        )
    events, dropped = snapshot.events, snapshot.events_dropped
    if dropped < 0:
        raise _refuse(f"its cursor's log dropped {dropped} events.")
    # Each lifecycle event of a seeded cursor is one draw from its stream, and
    # nothing else draws from it -- including the events a retention dropped.
    if position.seed is None and position.draws != 0:
        raise _refuse(f"an unseeded cursor records {position.draws} draws; it has no stream.")
    if position.seed is not None and position.draws != len(events) + dropped:
        raise _refuse(
            f"its cursor's stream has drawn {position.draws} identifiers for a log of "
            f"{len(events)} events and {dropped} dropped; each event is one draw."
        )
    if not events or not isinstance(events[-1].event, ReplayPaused):
        last = events[-1].event_type if events else "nothing"
        raise _refuse(f"its cursor's log ends with {last}, not ReplayPaused.")
    advanced = sum(1 for entry in events if isinstance(entry.event, ReplayAdvanced))
    if dropped == 0:
        if not isinstance(events[0].event, ReplayStarted):
            raise _refuse("its cursor's log does not begin with ReplayStarted.")
        started = events[0].event
        if started.total_events != snapshot.records_total or started.session_id != (
            snapshot.session.session_id
        ):
            raise _refuse(
                f"its cursor started on {started.total_events} records of {started.session_id!r}."
            )
        if advanced != index:
            raise _refuse(
                f"its cursor's log advanced {advanced} times and its cursor is at {index}."
            )
    elif not index - dropped <= advanced <= index:
        raise _refuse(
            f"its cursor's log keeps {advanced} advances after dropping {dropped} events, and "
            f"its cursor is at {index}."
        )


def restore(snapshot: ReplaySnapshot, objects: RunObjects, dataset: MarketDataset) -> PausedReplay:
    """Rebuild the paused replay a snapshot was captured from.

    ``dataset`` is the replay's input, supplied back as ``objects`` are; the
    snapshot records what it must be, and anything else is refused before any
    live object is touched. The run is rebuilt by
    :func:`alphalab.runtime.run_snapshot.restore`, which refuses an object
    configured otherwise than the run recorded (ledger PER-008).

    Returns:
        The paused replay. Nothing is processed and no identifier is minted:
        continuing is :meth:`~alphalab.backtesting.replay.ReplayBacktest.resume`.

    Raises:
        StateDecodeError: If the snapshot does not describe one replay paused
            between two records; if ``dataset`` is not the one it read -- another
            id, another length, another span, another record at the boundary, or
            any record changed (:func:`dataset_digest`); or if a live object is
            missing, of the wrong type, or configured otherwise.
    """

    _check_cursor(snapshot)
    _check_input(snapshot, dataset)
    records = dataset.records
    index = snapshot.current_index
    expected_clock = records[index - 1].timestamp if index else snapshot.session.start_time
    if snapshot.current_timestamp != expected_clock:
        raise _refuse(
            f"its cursor's clock reads {snapshot.current_timestamp}, and the record it last "
            f"took is at {expected_clock}."
        )

    run = restore_run(snapshot.run, objects)
    cursor = ReplayState(
        session=snapshot.session,
        status=snapshot.status,
        events=records,
        current_index=index,
        current_timestamp=snapshot.current_timestamp,
        real_start_time=snapshot.real_start_time,
        real_current_time=snapshot.real_current_time,
        system_events=AppendOnlyLog.restored(
            (entry.event for entry in snapshot.events), snapshot.events_dropped
        ),
    )
    return PausedReplay(run=run, cursor=cursor, cursor_position=snapshot.cursor_position)


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _session(value: Any, where: str) -> ReplaySession:
    payload = as_mapping(value, where)
    return ReplaySession(
        session_id=as_str(require(payload, "session_id"), f"{where}.session_id"),
        start_time=as_float(require(payload, "start_time"), f"{where}.start_time"),
        end_time=as_float(require(payload, "end_time"), f"{where}.end_time"),
        speed_multiplier=as_float(
            require(payload, "speed_multiplier"), f"{where}.speed_multiplier"
        ),
    )


#: Every event a paused replay's log can hold, by the tag it is written with --
#: its class name. A completed, stopped or reset cursor is not a paused one, so
#: those events are not here and a log holding one is refused.
_EVENTS: Mapping[str, type[ReplaySystemEvent]] = {
    "ReplayAdvanced": ReplayAdvanced,
    "ReplayPaused": ReplayPaused,
    "ReplayResumed": ReplayResumed,
    "ReplayStarted": ReplayStarted,
}


def _event(value: Any, where: str) -> ReplayEventRecord:
    entry = as_mapping(value, where)
    tag = as_str(require(entry, "event_type"), f"{where}.event_type")
    kind = _EVENTS.get(tag)
    if kind is None:
        known = ", ".join(sorted(_EVENTS))
        raise StateDecodeError(
            f"{where}.event_type is {tag!r}, which a paused replay's log cannot hold; "
            f"expected one of {known}."
        )
    at = f"{where}.event"
    payload = as_mapping(require(entry, "event"), at)
    event_id = as_str(require(payload, "event_id"), f"{at}.event_id")
    timestamp = as_float(require(payload, "timestamp"), f"{at}.timestamp")
    event: ReplaySystemEvent
    if kind is ReplayAdvanced:
        event = ReplayAdvanced(
            event_id,
            timestamp,
            old_timestamp=as_float(require(payload, "old_timestamp"), f"{at}.old_timestamp"),
            new_timestamp=as_float(require(payload, "new_timestamp"), f"{at}.new_timestamp"),
            events_yielded=as_int(require(payload, "events_yielded"), f"{at}.events_yielded"),
        )
    elif kind is ReplayStarted:
        event = ReplayStarted(
            event_id,
            timestamp,
            session_id=as_str(require(payload, "session_id"), f"{at}.session_id"),
            total_events=as_int(require(payload, "total_events"), f"{at}.total_events"),
        )
    else:
        session_id = as_str(require(payload, "session_id"), f"{at}.session_id")
        event = (
            ReplayPaused(event_id, timestamp, session_id)
            if kind is ReplayPaused
            else ReplayResumed(event_id, timestamp, session_id)
        )
    return ReplayEventRecord(tag, event)


def from_primitives(payload: Mapping[str, Any]) -> ReplaySnapshot:
    """Decode a JSON-decoded replay payload back into :class:`ReplaySnapshot`.

    The nested run payload is decoded by :mod:`alphalab.runtime.run_snapshot`,
    so its version and its field errors reach the caller as its own.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or holds
            a value of the wrong type. The message names the field.
    """

    payload = REPLAY_SCHEMA_HISTORY.upgrade(as_mapping(payload, "replay snapshot"))
    events = as_sequence(require(payload, "events"), "events")
    return ReplaySnapshot(
        run=run_from_primitives(as_mapping(require(payload, "run"), "run")),
        session=_session(require(payload, "session"), "session"),
        status=as_named_enum(ReplayStatus, require(payload, "status"), "status"),
        current_index=as_int(require(payload, "current_index"), "current_index"),
        current_timestamp=as_float(require(payload, "current_timestamp"), "current_timestamp"),
        real_start_time=as_float(require(payload, "real_start_time"), "real_start_time"),
        real_current_time=as_float(require(payload, "real_current_time"), "real_current_time"),
        events=tuple(_event(item, f"events[{index}]") for index, item in enumerate(events)),
        cursor_position=_id_position(require(payload, "cursor_position"), "cursor_position"),
        records_total=as_int(require(payload, "records_total"), "records_total"),
        dataset_digest=as_str(require(payload, "dataset_digest"), "dataset_digest"),
        last_event_id=as_optional_str(require(payload, "last_event_id"), "last_event_id"),
        next_event_id=as_str(require(payload, "next_event_id"), "next_event_id"),
        events_dropped=as_int(require(payload, "events_dropped"), "events_dropped"),
        schema_version=REPLAY_SNAPSHOT_SCHEMA,
    )
