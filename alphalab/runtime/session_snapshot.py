"""Complete, restorable snapshots of a :class:`~alphalab.runtime.session.SessionState`.

The outer of two envelopes
--------------------------
:mod:`alphalab.runtime.snapshot` captures ``ExecutionPipelineState``, which is
the stable core every environment threads. This module captures the layer around
it: how far a session has read, what it declined to act on, and the stream it was
reading. The split is deliberate and ADR-0023 gives the reason -- the pipeline
core is not expected to change shape, while this orchestration layer is expected
to be reshaped by the integrated-runtime work, and a single envelope would bump
the stable core's version as a side effect of that.

``SessionState.config.pipeline`` and ``state.pipeline.config`` are the *same*
object: :meth:`~alphalab.runtime.session.TradingSession.initialize` builds the
pipeline from the session's own configuration. The envelope therefore carries the
pipeline configuration exactly once, inside the nested pipeline snapshot, and
:func:`restore` rebuilds the session configuration around it. Two copies could
disagree; one cannot.

What is supplied back
---------------------
Everything :class:`~alphalab.runtime.snapshot.RuntimeObjects` covers, plus the
session's :class:`~alphalab.execution.policy.FillPolicy` -- a policy object, not
data. Missing or wrongly typed objects are refused, never substituted.

Resuming
--------
:func:`restore` reconstructs state and does nothing else: it installs no
identifier source and processes no record. Continuation is
:meth:`~alphalab.runtime.session.TradingSession.resume`, which opens the scope the
restored stream position implies, and the caller then advances the records the
session has not seen::

    restored = restore(from_primitives(deserialize(payload)), objects)
    with TradingSession.resume(restored):
        for record in remaining:
            restored, _ = TradingSession.advance(restored, record, context_factory)

The boundary is *between* ``advance`` calls. A session that processed N records
resumes at record N+1; nothing is replayed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from alphalab.common.append_log import AppendOnlyLog
from alphalab.execution.policy import FillPolicy
from alphalab.market.record import MarketRecord
from alphalab.market.source import OrderingGuarantee
from alphalab.persistence.decode import (
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_optional_str,
    as_sequence,
    as_str,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.session import (
    ExecutionMode,
    SessionConfig,
    SessionState,
    SkippedRecord,
)

# The market-record payload decoders belong to the pipeline snapshot, which owns
# the market state they came from. Imported rather than repeated: a second
# implementation of one decoding contract is how two readers come to disagree.
from alphalab.runtime.snapshot import (
    PipelineSnapshot,
    RuntimeObjects,
    _bar,
    _quote,
    _require_object,
    _tick,
)
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
from alphalab.runtime.snapshot import restore as restore_pipeline

__all__ = [
    "SESSION_SNAPSHOT_SCHEMA",
    "SessionObjects",
    "SessionSnapshot",
    "SkippedRecordRecord",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version this module reads and writes. See ADR-0023.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio: that constant also versions ``CommonEvent`` and
#: ``BaseEvent``, so bumping it would version every event in the system as a side
#: effect of one subsystem's change. This is the constant expected to move when
#: the session layer is reshaped, which is why it is not the pipeline's.
SESSION_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM: Final = "session"

#: A market record's payload is a union, so it carries the tag that says which.
_PAYLOAD_TYPES: Mapping[str, Any] = {"Quote": _quote, "Bar": _bar, "Tick": _tick}


@dataclass(frozen=True, slots=True)
class SkippedRecordRecord:
    """One record the session declined to act on, with its payload tagged.

    ``MarketRecord.payload`` is a ``Quote | Bar | Tick``; without the tag a
    decoder cannot know which of the three a payload describes.
    """

    payload_type: str
    record: MarketRecord
    reason: str


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Complete, JSON-serializable projection of a :class:`SessionState`."""

    pipeline: PipelineSnapshot
    mode: ExecutionMode
    seed: int | None
    start_timestamp: float
    max_market_data_age_seconds: float | None
    ordering: OrderingGuarantee
    fill_policy_type: str
    processed: int
    current_timestamp: float
    skipped: tuple[SkippedRecordRecord, ...]
    last_record_timestamp: float | None
    source_id: str | None
    schema_version: int = SESSION_SNAPSHOT_SCHEMA


@dataclass(frozen=True, slots=True)
class SessionObjects:
    """The live objects a session snapshot records but does not carry.

    Attributes:
        pipeline: What :func:`alphalab.runtime.snapshot.restore` requires --
            sizing model, simulator, strategy instances and the optional
            instrument registry.
        fill_policy: How a simulated venue answers each order. A policy object,
            so it is recorded by type and supplied back like the rest.
    """

    pipeline: RuntimeObjects
    fill_policy: FillPolicy


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(state: SessionState) -> SessionSnapshot:
    """Project ``state`` into its complete serializable snapshot.

    The pipeline configuration is carried once, by the nested pipeline snapshot,
    because the session's copy is the same object.
    """

    return SessionSnapshot(
        pipeline=capture_pipeline(state.pipeline),
        mode=state.config.mode,
        seed=state.config.seed,
        start_timestamp=state.config.start_timestamp,
        max_market_data_age_seconds=state.config.max_market_data_age_seconds,
        ordering=state.config.ordering,
        fill_policy_type=type(state.config.fill_policy).__name__,
        processed=state.processed,
        current_timestamp=state.current_timestamp,
        skipped=tuple(
            SkippedRecordRecord(type(entry.record.payload).__name__, entry.record, entry.reason)
            for entry in state.skipped
        ),
        last_record_timestamp=state.last_record_timestamp,
        source_id=state.source_id,
    )


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(snapshot: SessionSnapshot, objects: SessionObjects) -> SessionState:
    """Rebuild the state a snapshot was captured from.

    The pipeline is rebuilt first, and its configuration becomes the session
    configuration's, so the two cannot disagree. Construction-time validation is
    re-applied by the pipeline's own restore.

    Returns:
        The reconstructed session. No identifier source is installed and no
        record is processed: continuing is
        :meth:`~alphalab.runtime.session.TradingSession.resume` plus
        :meth:`~alphalab.runtime.session.TradingSession.advance`.

    Raises:
        StateDecodeError: If a required live object is missing or of the wrong
            type.
        RuntimeValidationError: If the restored configuration fails a validation
            ``initialize`` enforces.
    """

    pipeline = restore_pipeline(snapshot.pipeline, objects.pipeline)
    fill_policy = _require_object(objects.fill_policy, snapshot.fill_policy_type, "fill policy")

    config = SessionConfig(
        pipeline=pipeline.config,
        mode=snapshot.mode,
        fill_policy=fill_policy,
        seed=snapshot.seed,
        start_timestamp=snapshot.start_timestamp,
        max_market_data_age_seconds=snapshot.max_market_data_age_seconds,
        ordering=snapshot.ordering,
    )
    return SessionState(
        config=config,
        pipeline=pipeline,
        processed=snapshot.processed,
        current_timestamp=snapshot.current_timestamp,
        skipped=AppendOnlyLog(
            SkippedRecord(entry.record, entry.reason) for entry in snapshot.skipped
        ),
        last_record_timestamp=snapshot.last_record_timestamp,
        source_id=snapshot.source_id,
    )


# ---------------------------------------------------------------------------
# Decoding a JSON payload back into snapshot types
# ---------------------------------------------------------------------------


def _record(value: Any, where: str) -> SkippedRecordRecord:
    payload = as_mapping(value, where)
    payload_type = as_str(require(payload, "payload_type"), f"{where}.payload_type")
    decode = _PAYLOAD_TYPES.get(payload_type)
    if decode is None:
        known = ", ".join(sorted(_PAYLOAD_TYPES))
        raise StateDecodeError(
            f"{where}.payload_type is not a market payload: {payload_type!r}; "
            f"expected one of {known}"
        )

    record = as_mapping(require(payload, "record"), f"{where}.record")
    return SkippedRecordRecord(
        payload_type=payload_type,
        record=MarketRecord(
            event_id=as_str(require(record, "event_id"), f"{where}.record.event_id"),
            timestamp=as_float(require(record, "timestamp"), f"{where}.record.timestamp"),
            payload=decode(require(record, "payload"), f"{where}.record.payload"),
        ),
        reason=as_str(require(payload, "reason"), f"{where}.reason"),
    )


def _optional_float(value: Any, where: str) -> float | None:
    return None if value is None else as_float(value, where)


def _optional_int(value: Any, where: str) -> int | None:
    return None if value is None else as_int(value, where)


def from_primitives(payload: Mapping[str, Any]) -> SessionSnapshot:
    """Decode a JSON-decoded snapshot payload back into :class:`SessionSnapshot`.

    The nested pipeline payload is decoded by the module that owns it, so its
    version and its field errors reach the caller as its own rather than being
    normalized here.

    Raises:
        StateDecodeError: If the payload is not an object, declares no schema
            version or one this build does not read, is missing a field, or holds
            a value of the wrong type. The message names the field.
    """

    payload = as_mapping(payload, "session snapshot")
    require_schema_version(payload, SESSION_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    return SessionSnapshot(
        pipeline=pipeline_from_primitives(as_mapping(require(payload, "pipeline"), "pipeline")),
        mode=as_named_enum(ExecutionMode, require(payload, "mode"), "mode"),
        seed=_optional_int(require(payload, "seed"), "seed"),
        start_timestamp=as_float(require(payload, "start_timestamp"), "start_timestamp"),
        max_market_data_age_seconds=_optional_float(
            require(payload, "max_market_data_age_seconds"), "max_market_data_age_seconds"
        ),
        ordering=as_named_enum(OrderingGuarantee, require(payload, "ordering"), "ordering"),
        fill_policy_type=as_str(require(payload, "fill_policy_type"), "fill_policy_type"),
        processed=as_int(require(payload, "processed"), "processed"),
        current_timestamp=as_float(require(payload, "current_timestamp"), "current_timestamp"),
        skipped=tuple(
            _record(item, f"skipped[{index}]")
            for index, item in enumerate(as_sequence(require(payload, "skipped"), "skipped"))
        ),
        last_record_timestamp=_optional_float(
            require(payload, "last_record_timestamp"), "last_record_timestamp"
        ),
        source_id=as_optional_str(require(payload, "source_id"), "source_id"),
        schema_version=SESSION_SNAPSHOT_SCHEMA,
    )
