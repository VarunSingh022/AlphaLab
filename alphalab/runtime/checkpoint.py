"""Checkpoints of a long run that grow with what changed, not with the run (ledger PRF-004).

A run's snapshot (:mod:`alphalab.runtime.run_snapshot`) holds everything the
run kept, from its first record. Taken every ``n`` records, ``k`` checkpoints
write every log entry up to ``k`` times over -- quadratic in the run's length,
the cost ADR-0029 decision 8 measured and refused to pay per event.

A chain of checkpoints
----------------------
The first checkpoint of a chain is a **base**: the complete run snapshot. Each
one after it is a **segment**: the run snapshot of the same state with every log
cut to the entries appended since the checkpoint before it -- captured by the
run snapshot's own capture, so the format has one authority -- and, for each
log, where those entries start and end in the log's whole history and how many
of its entries the run has dropped (:mod:`alphalab.runtime.retention`).
Everything that is not a log is written whole in every segment -- positions,
cash, working orders, latest quotes: what the run *is*, rather than what it did
-- except the state that grows with every order the run places.

Per-order state (v3.13, ledger PRF-011)
---------------------------------------
The order book, the completed orders and the execution reports by order hold an
entry for every order the run ever placed, and until v3.13 every segment wrote
them whole: about a kilobyte per order, per segment, so a long session's
segments grew with its order count. Each of them is keyed, only ever grows, and
keeps every entry where it was first placed, so a segment now writes only the
entries added or replaced since the checkpoint before it, and a reader puts each
at its key. The :class:`CheckpointMark` holds, by reference, the entries the
next segment compares against; a mark rebuilt from its digest and ends alone has
none, and the segment after it writes those maps whole -- as it does whenever an
entry is found removed or moved, which the run never does. A segment therefore
costs the size of the run's current state plus what it appended and changed,
whatever the run's length.

Each payload names its predecessor's digest -- SHA-256 of its exact text, which
is what :class:`~alphalab.persistence.run_store.RunStateStore` records beside
it -- so a chain is read only whole and in order: a missing, reordered,
substituted or altered checkpoint is refused, saying where the chain breaks.
Reading a chain stitches each log back together -- the base's entries, then
each segment's -- keeps what the run had kept, and decodes the result with the
run snapshot's own decoder. The state restored is the state a full capture would
have restored, and the merged payload is that capture's, value for value.

Starting a new chain -- writing a new base -- is the caller's decision, as
persisting is (ADR-0029): it bounds how many segments a restore reads. Nothing
here writes anywhere: a payload is a string, stored by whatever store the caller
uses, under whatever sequence the caller chooses.

Nothing here mints an identifier, reads a clock or contacts anything.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.common.evolve import evolve
from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.common.serialization import to_serializable
from alphalab.oms.book import OrderBook
from alphalab.persistence.decode import as_int, as_mapping, as_sequence, as_str, require
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.runtime.run import RunState
from alphalab.runtime.run_snapshot import RunObjects, RunSnapshot
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.run_snapshot import restore as restore_run
from alphalab.runtime.snapshot import _RETAINED

__all__ = [
    "CHECKPOINT_SCHEMA",
    "CheckpointMark",
    "checkpoint",
    "read_checkpoints",
    "restore_checkpoints",
]

#: Version of the checkpoint envelope: the chain fields, the log ranges and the
#: per-order maps around a run snapshot, which carries its own version. Version
#: 2 (v3.13) writes the per-order maps by their changes (ledger PRF-011); a
#: version-1 checkpoint wrote them whole, and is read as such.
CHECKPOINT_SCHEMA: Final = 2

#: Every checkpoint version a release wrote, each read by this one.
_READABLE: Final = frozenset({1, CHECKPOINT_SCHEMA})

#: Every log a checkpoint cuts, and where its entries sit in a run snapshot's
#: primitives -- one entry per log entry (pinned by
#: ``tests/regression/test_checkpoints.py``).
_LOGS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("market.history", ("pipeline", "market", "history")),
    ("market.events", ("pipeline", "market", "events")),
    ("allocation.history", ("pipeline", "allocation", "history")),
    ("allocation.events", ("pipeline", "allocation", "events")),
    ("risk.history", ("pipeline", "risk", "history")),
    ("risk.events", ("pipeline", "risk", "events")),
    ("oms.history", ("pipeline", "oms", "history")),
    ("oms.events", ("pipeline", "oms", "events")),
    ("execution.history", ("pipeline", "execution", "history")),
    ("execution.events", ("pipeline", "execution", "events")),
    ("portfolio.events", ("pipeline", "portfolio", "events")),
    ("portfolio.ledger.transactions", ("pipeline", "portfolio", "transactions")),
    ("fills", ("pipeline", "fills")),
    ("trades", ("pipeline", "trades")),
    ("trade_records", ("pipeline", "trade_records")),
    ("portfolio_snapshots", ("pipeline", "portfolio_snapshots")),
    ("steps", ("steps",)),
    ("skipped", ("skipped",)),
)

#: The run's own logs, as opposed to the pipeline's.
_RUN_LOGS: Final = frozenset({"steps", "skipped"})

#: The per-order state a segment writes by its changes, and where it sits in a
#: run snapshot's primitives. Pinned by ``tests/regression/test_checkpoints.py``.
_MAPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("oms.orders", ("pipeline", "oms", "orders")),
    ("oms.completed_orders", ("pipeline", "oms", "completed_orders")),
    ("execution.reports", ("pipeline", "execution", "reports")),
)

#: A map's entries as ``(key, value)`` pairs, in the map's own order.
_Entries = tuple[tuple[Any, Any], ...]


@dataclass(frozen=True, slots=True)
class CheckpointMark:
    """Where a checkpoint left off: what the next checkpoint of the chain builds on.

    Attributes:
        sequence: The checkpoint's position in its chain; the base is ``0``.
        digest: SHA-256 of the checkpoint's payload, which the next one names.
        ends: Each log's length over the run's whole history -- dropped entries
            included -- when the checkpoint was taken.
        entries: Each per-order map's entries when the checkpoint was taken,
            held by reference and written nowhere: what the next segment
            compares against to write only what changed (v3.13). A mark built
            from ``sequence``, ``digest`` and ``ends`` alone -- after a restart,
            say -- has none, and the next segment writes those maps whole.
    """

    sequence: int
    digest: str
    ends: Mapping[str, int]
    entries: Mapping[str, tuple[tuple[Any, Any], ...]] = field(
        default_factory=dict, compare=False, repr=False
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode(DEFAULT_ENCODING)).hexdigest()


def _logs_of(state: RunState) -> dict[str, AppendOnlyLog[Any]]:
    logs: dict[str, AppendOnlyLog[Any]] = {
        name: read(state.pipeline) for name, read, _ in _RETAINED
    }
    logs["steps"] = state.steps
    logs["skipped"] = state.skipped
    return logs


def _cut(state: RunState, starts: Mapping[str, int]) -> RunState:
    """``state`` with each log holding only its entries from ``starts[name]`` on."""

    pipeline = state.pipeline
    for name, read, write in _RETAINED:
        log = read(pipeline)
        pipeline = write(pipeline, AppendOnlyLog(log[starts[name] - log.dropped :]))
    steps, skipped = state.steps, state.skipped
    return evolve(
        state,
        pipeline=pipeline,
        steps=AppendOnlyLog(steps[starts["steps"] - steps.dropped :]),
        skipped=AppendOnlyLog(skipped[starts["skipped"] - skipped.dropped :]),
    )


def _entries_of(state: RunState) -> dict[str, _Entries]:
    """Each per-order map's entries, in its own order, by reference."""

    oms = state.pipeline.oms
    return {
        "oms.orders": tuple((order.order_id, order) for order in oms.orders.orders()),
        "oms.completed_orders": tuple((order_id, order_id) for order_id in oms.completed_orders),
        "execution.reports": tuple(state.pipeline.execution.reports.items()),
    }


def _changed(previous: _Entries | None, current: _Entries) -> list[tuple[Any, Any]] | None:
    """The entries added or replaced since ``previous``, or ``None`` to write the map whole.

    Whole when nothing was recorded to compare against, or when an entry the
    previous checkpoint held is gone or has moved: a reader merging by key would
    then put it in the wrong place, and the map is written as it is instead.
    """

    if previous is None or len(current) < len(previous):
        return None
    changed: list[tuple[Any, Any]] = []
    for (key, value), (held_key, held_value) in zip(current, previous, strict=False):
        if key is not held_key and key != held_key:
            return None
        if value is not held_value:
            changed.append((key, value))
    changed.extend(current[len(previous) :])
    return changed


def _with_entries(state: RunState, chosen: Mapping[str, list[tuple[Any, Any]]]) -> RunState:
    """``state`` with each per-order map in ``chosen`` holding only the given entries."""

    pipeline = state.pipeline
    oms, execution = pipeline.oms, pipeline.execution
    if "oms.orders" in chosen:
        book = OrderBook()
        for _, order in chosen["oms.orders"]:
            book = book.add(order)
        oms = evolve(oms, orders=book)
    if "oms.completed_orders" in chosen:
        oms = evolve(
            oms, completed_orders=PersistentSet(key for key, _ in chosen["oms.completed_orders"])
        )
    if "execution.reports" in chosen:
        execution = evolve(execution, reports=PersistentMap(chosen["execution.reports"]))
    return evolve(state, pipeline=evolve(pipeline, oms=oms, execution=execution))


def _place(run: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = run
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _find(run: Mapping[str, Any], path: tuple[str, ...], where: str) -> Sequence[Any]:
    node: Any = run
    for key in path:
        node = as_mapping(node, where)
        node = require(node, key)
    return as_sequence(node, f"{where}.{'.'.join(path)}")


def checkpoint(
    state: RunState, previous: CheckpointMark | None = None
) -> tuple[str, CheckpointMark]:
    """The payload of the next checkpoint of ``state``, and the mark the one after builds on.

    With no ``previous`` mark, a base: the complete run snapshot. After one, a
    segment holding each log's entries appended since ``previous`` and the rest
    of the state whole. Pure: nothing is stored, minted or mutated.

    Raises:
        StateDecodeError: If ``previous`` covers a log further than ``state``
            has reached -- a mark of another run, or of a later state.
    """

    logs = _logs_of(state)
    ends = {name: log.dropped + len(log) for name, log in logs.items()}
    entries = _entries_of(state)
    maps = {name: {"size": len(entries[name]), "whole": True} for name, _ in _MAPS}
    if previous is None:
        starts = {name: log.dropped for name, log in logs.items()}
        run = to_serializable(capture_run(state))
        kind, sequence, before = "base", 0, None
    else:
        starts = {}
        for name, log in logs.items():
            covered = previous.ends.get(name)
            if covered is None or covered > ends[name]:
                raise StateDecodeError(
                    f"The previous checkpoint covers {name} to entry {covered}, and this state "
                    f"holds {ends[name]} of them. A segment continues the chain of its own run, "
                    "from a mark no later than the state it is taken of."
                )
            starts[name] = max(covered, log.dropped)
        chosen: dict[str, list[tuple[Any, Any]]] = {}
        for name, _ in _MAPS:
            changed = _changed(previous.entries.get(name), entries[name])
            if changed is not None:
                chosen[name] = changed
                maps[name]["whole"] = False
        run = to_serializable(capture_run(_with_entries(_cut(state, starts), chosen)))
        # The cut logs dropped nothing; the counts are the run's.
        run["pipeline"]["dropped"] = {
            name: logs[name].dropped for name, _, _ in _RETAINED if logs[name].dropped
        }
        run["dropped"] = {
            name: logs[name].dropped for name in ("skipped", "steps") if logs[name].dropped
        }
        kind, sequence, before = "segment", previous.sequence + 1, previous.digest
    payload = serialize(
        {
            "schema_version": CHECKPOINT_SCHEMA,
            "kind": kind,
            "sequence": sequence,
            "previous": before,
            "logs": {
                name: {"start": starts[name], "end": ends[name], "dropped": logs[name].dropped}
                for name, _ in _LOGS
            },
            "maps": maps,
            "run": run,
        }
    )
    return payload, CheckpointMark(sequence, _digest(payload), ends, entries)


def _ranges(value: Any, where: str) -> dict[str, tuple[int, int, int]]:
    payload = as_mapping(value, where)
    names = {name for name, _ in _LOGS}
    unknown = sorted(set(payload) - names)
    missing = sorted(names - set(payload))
    if unknown or missing:
        raise StateDecodeError(
            f"{where} must name exactly the logs a checkpoint cuts: missing {missing}, "
            f"unknown {unknown}."
        )
    ranges: dict[str, tuple[int, int, int]] = {}
    for name, _ in _LOGS:
        entry = as_mapping(payload[name], f"{where}.{name}")
        start = as_int(require(entry, "start"), f"{where}.{name}.start")
        end = as_int(require(entry, "end"), f"{where}.{name}.end")
        dropped = as_int(require(entry, "dropped"), f"{where}.{name}.dropped")
        if not 0 <= dropped <= start <= end:
            raise StateDecodeError(
                f"{where}.{name} says entries {start} to {end} after dropping {dropped}; a range "
                "starts no earlier than what was dropped and ends no earlier than it starts."
            )
        ranges[name] = (start, end, dropped)
    return ranges


def _map_header(value: Any, where: str) -> dict[str, tuple[int, bool]]:
    payload = as_mapping(value, where)
    names = {name for name, _ in _MAPS}
    if set(payload) != names:
        raise StateDecodeError(
            f"{where} must name exactly the per-order maps a checkpoint writes: "
            f"{sorted(names)}, not {sorted(payload)}."
        )
    header: dict[str, tuple[int, bool]] = {}
    for name, _ in _MAPS:
        entry = as_mapping(payload[name], f"{where}.{name}")
        size = as_int(require(entry, "size"), f"{where}.{name}.size")
        whole = require(entry, "whole")
        if size < 0 or not isinstance(whole, bool):
            raise StateDecodeError(f"{where}.{name} is not a size and a whole flag: {entry!r}.")
        header[name] = (size, whole)
    return header


def _keyed(name: str, run: Mapping[str, Any], path: tuple[str, ...], where: str) -> list[Any]:
    """A per-order map's entries in a payload, each as ``(key text, entry)``."""

    node: Any = run
    for key in path:
        node = require(as_mapping(node, where), key)
    if name == "execution.reports":
        return [
            (as_str(key, f"{where} key"), value) for key, value in as_mapping(node, where).items()
        ]
    entries = as_sequence(node, f"{where}.{'.'.join(path)}")
    if name == "oms.orders":
        return [(serialize(require(as_mapping(item, where), "order_id")), item) for item in entries]
    return [(serialize(item), item) for item in entries]


def read_checkpoints(payloads: Sequence[str]) -> RunSnapshot:
    """The run snapshot a chain of checkpoints records, verified link by link.

    ``payloads`` is the chain in order: its base, then every segment after it.

    Raises:
        StateDecodeError: If the chain is empty, does not start with a base,
            skips, repeats or reorders a checkpoint, names a predecessor other
            than the payload before it, leaves a gap or an overlap in a log, or
            holds a payload the run snapshot's decoder refuses.
    """

    if not payloads:
        raise StateDecodeError("No checkpoint was given; a chain starts with its base.")
    kept: dict[str, list[Any]] = {}
    covered: dict[str, tuple[int, int]] = {}
    merged: dict[str, dict[str, Any]] = {}
    previous: str | None = None
    run: dict[str, Any] = {}
    for sequence, text in enumerate(payloads):
        where = f"checkpoint {sequence}"
        payload = as_mapping(deserialize(text), where)
        version = require(payload, "schema_version")
        if isinstance(version, bool) or version not in _READABLE:
            raise StateDecodeError(
                f"{where} declares checkpoint schema {version!r}; this build reads "
                f"{sorted(_READABLE)}."
            )
        kind = as_str(require(payload, "kind"), f"{where}.kind")
        expected = "base" if sequence == 0 else "segment"
        if kind != expected or as_int(require(payload, "sequence"), f"{where}.sequence") != (
            sequence
        ):
            raise StateDecodeError(
                f"{where} is a {kind} numbered {payload.get('sequence')!r}; a chain is its base "
                "then its segments, numbered from zero without a gap."
            )
        named = require(payload, "previous")
        if named != previous:
            raise StateDecodeError(
                f"{where} follows a checkpoint with digest {named!r}, and the one before it in "
                f"this chain has digest {previous!r}. The chain is broken here: a checkpoint is "
                "missing, out of order, or altered."
            )
        ranges = _ranges(require(payload, "logs"), f"{where}.logs")
        run = dict(as_mapping(require(payload, "run"), f"{where}.run"))
        run["pipeline"] = dict(as_mapping(require(run, "pipeline"), f"{where}.run.pipeline"))
        for name, path in _LOGS:
            start, end, dropped = ranges[name]
            entries = list(_find(run, path, f"{where}.run"))
            if len(entries) != end - start:
                raise StateDecodeError(
                    f"{where}.logs.{name} says entries {start} to {end}, and the payload holds "
                    f"{len(entries)}."
                )
            if sequence == 0:
                if start != dropped:
                    raise StateDecodeError(
                        f"{where} is a base, so {name} starts at the first entry the run kept "
                        f"({dropped}), not at {start}."
                    )
                kept[name] = entries
            else:
                first, last = covered[name]
                if dropped < first or start != max(last, dropped):
                    raise StateDecodeError(
                        f"{where}.logs.{name} starts at {start} after dropping {dropped}; the "
                        f"chain so far holds entries {first} to {last}, so it continues at "
                        f"{max(last, dropped)} and has dropped at least {first}."
                    )
                kept[name] = kept[name][dropped - first :] + entries
            covered[name] = (dropped, end)
        # Version 1 wrote every per-order map whole; version 2 says which it did.
        header = (
            {name: (None, True) for name, _ in _MAPS}
            if version == 1
            else _map_header(require(payload, "maps"), f"{where}.maps")
        )
        for name, path in _MAPS:
            size, whole = header[name]
            entries = _keyed(name, run, path, f"{where}.run")
            if whole:
                merged[name] = dict(entries)
            elif sequence == 0:
                raise StateDecodeError(f"{where} is a base, so it writes {name} whole.")
            else:
                # An entry already held stays where it is; a new one goes last.
                merged[name].update(entries)
            if size is not None and len(merged[name]) != size:
                raise StateDecodeError(
                    f"{where}.maps.{name} says the run held {size} entries, and the chain "
                    f"holds {len(merged[name])}: a change was lost or invented."
                )
        previous = _digest(text)
    for name, path in _LOGS:
        _place(run, path, kept[name])
    for name, path in _MAPS:
        held = merged[name]
        _place(run, path, dict(held) if name == "execution.reports" else list(held.values()))
    pipeline_dropped = {name: covered[name][0] for name, _, _ in _RETAINED if covered[name][0]}
    run_dropped = {name: covered[name][0] for name in sorted(_RUN_LOGS) if covered[name][0]}
    if (
        dict(as_mapping(require(run["pipeline"], "dropped"), "run.pipeline.dropped"))
        != pipeline_dropped
        or dict(as_mapping(require(run, "dropped"), "run.dropped")) != run_dropped
    ):
        raise StateDecodeError(
            "The last checkpoint's dropped counts disagree with its log ranges; the payload "
            "was not written by checkpoint()."
        )
    return run_from_primitives(run)


def restore_checkpoints(payloads: Sequence[str], objects: RunObjects) -> RunState:
    """The run state a chain of checkpoints records: :func:`read_checkpoints`, then restore.

    Raises:
        StateDecodeError: As :func:`read_checkpoints`, or if a live object is
            missing or of the wrong type.
    """

    return restore_run(read_checkpoints(payloads), objects)
